from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from app.config import APP_ENV, AUTH_RATE_LIMIT_ENABLED, CSRF_PROTECTION_ENABLED, RATE_LIMIT_HASH_KEY
from app.database import get_transaction_cursor


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class RateLimitExceeded(ValueError):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


def _rate_subject(value: str) -> str:
    return hmac.new(
        RATE_LIMIT_HASH_KEY.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _session_rate_id(request: Request) -> str:
    value = request.session.get("rate_limit_id")
    if not value:
        value = secrets.token_urlsafe(18)
        request.session["rate_limit_id"] = value
    return str(value)


def consume_rate_limit(
    event_type: str,
    subject: str,
    limit: int,
    window_seconds: int,
    message: str,
) -> None:
    """Atomically consume a fixed-window allowance in PostgreSQL."""
    if not AUTH_RATE_LIMIT_ENABLED:
        return
    subject_hash = _rate_subject(subject)
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            INSERT INTO rate_limit_buckets (
                event_type, subject_hash, window_seconds, bucket_started_at,
                request_count, expires_at
            )
            VALUES (
                %s, %s, %s,
                to_timestamp(floor(extract(epoch FROM now()) / %s) * %s),
                1,
                to_timestamp(floor(extract(epoch FROM now()) / %s) * %s) + make_interval(secs => %s)
            )
            ON CONFLICT (event_type, subject_hash, window_seconds, bucket_started_at)
            DO UPDATE SET request_count = rate_limit_buckets.request_count + 1
            WHERE rate_limit_buckets.request_count < %s
            RETURNING extract(epoch FROM (expires_at - now()))::integer AS retry_after;
            """,
            (
                event_type,
                subject_hash,
                window_seconds,
                window_seconds,
                window_seconds,
                window_seconds,
                window_seconds,
                window_seconds,
                limit,
            ),
        )
        row = cur.fetchone()
        if row:
            return
        cur.execute(
            """
            SELECT greatest(1, extract(epoch FROM (expires_at - now()))::integer) AS retry_after
            FROM rate_limit_buckets
            WHERE event_type = %s AND subject_hash = %s AND window_seconds = %s
              AND expires_at > now()
            ORDER BY bucket_started_at DESC
            LIMIT 1;
            """,
            (event_type, subject_hash, window_seconds),
        )
        blocked = cur.fetchone()
    raise RateLimitExceeded(message, int(blocked["retry_after"] if blocked else window_seconds))


def csrf_token(request: Request) -> str:
    if not CSRF_PROTECTION_ENABLED:
        return ""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


async def enforce_csrf(request: Request) -> None:
    if not CSRF_PROTECTION_ENABLED or request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return

    host = request.headers.get("host", "").lower()
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    source = origin or referer
    if APP_ENV == "production":
        if not host or not source or urlparse(source).netloc.lower() != host:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request origin.")

    supplied = request.headers.get("x-csrf-token", "")
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith("multipart/form-data"):
            form = await request.form()
            supplied = str(form.get("csrf_token", ""))
    expected = request.session.get("csrf_token", "")
    if not expected or not supplied or not secrets.compare_digest(str(expected), supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing CSRF token.")


def _failure_count(event_type: str, subject_key: str, interval: str) -> int:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS count
            FROM security_events
            WHERE event_type = %s AND subject_key = %s AND succeeded = false
              AND created_at >= now() - %s::interval
            """,
            (event_type, subject_key, interval),
        )
        return int(cur.fetchone()["count"])


def enforce_limit(event_type: str, subject_key: str, limit: int, interval: str, message: str) -> None:
    if AUTH_RATE_LIMIT_ENABLED and _failure_count(event_type, subject_key, interval) >= limit:
        raise ValueError(message)


def record_event(event_type: str, subject_key: str, *, succeeded: bool = False) -> None:
    if not AUTH_RATE_LIMIT_ENABLED:
        return
    with get_transaction_cursor() as cur:
        if succeeded:
            cur.execute(
                "DELETE FROM security_events WHERE event_type = %s AND subject_key = %s AND succeeded = false",
                (event_type, subject_key),
            )
        cur.execute(
            "INSERT INTO security_events (event_type, subject_key, succeeded) VALUES (%s, %s, %s)",
            (event_type, subject_key, succeeded),
        )


def enforce_password_login(request: Request, email: str) -> None:
    enforce_limit("password_login", f"email:{email.lower()}", 5, "15 minutes", "Too many failed sign-in attempts. Try again in 15 minutes.")
    enforce_limit("password_login", f"ip:{client_ip(request)}", 20, "15 minutes", "Too many failed sign-in attempts from this address. Try again in 15 minutes.")


def record_password_login(request: Request, email: str, succeeded: bool) -> None:
    record_event("password_login", f"email:{email.lower()}", succeeded=succeeded)
    record_event("password_login", f"ip:{client_ip(request)}", succeeded=succeeded)


def enforce_otp_attempt(account_id: int) -> None:
    enforce_limit("otp_verify", f"account:{account_id}", 5, "15 minutes", "Too many incorrect OTP attempts. Request a new code.")


def enforce_otp_resend(account_id: int) -> None:
    enforce_limit("otp_resend_minute", f"account:{account_id}", 1, "60 seconds", "Please wait 60 seconds before requesting another OTP.")
    enforce_limit("otp_resend_hour", f"account:{account_id}", 5, "1 hour", "OTP request limit reached. Try again later.")


def record_otp_resend(account_id: int) -> None:
    record_event("otp_resend_minute", f"account:{account_id}")
    record_event("otp_resend_hour", f"account:{account_id}")


def enforce_chatbot(request: Request) -> None:
    consume_rate_limit("chatbot_session", f"session:{_session_rate_id(request)}", 20, 60, "Chat limit reached. Please wait a moment.")
    consume_rate_limit("chatbot_ip", f"ip:{client_ip(request)}", 60, 600, "Chat limit reached for this network. Please try again later.")


def enforce_chatbot_reset(request: Request) -> None:
    consume_rate_limit("chatbot_reset", f"session:{_session_rate_id(request)}", 5, 600, "Too many new-chat requests. Please wait before resetting again.")
    consume_rate_limit("chatbot_reset_ip", f"ip:{client_ip(request)}", 20, 600, "Too many new-chat requests from this network. Please wait before resetting again.")


def enforce_live_message(request: Request, *, account_id: int | None = None) -> None:
    identity = f"account:{account_id}" if account_id is not None else f"session:{_session_rate_id(request)}"
    consume_rate_limit("live_message", identity, 30, 60, "Live-chat message limit reached. Please wait a moment.")
    consume_rate_limit("live_message_ip", f"ip:{client_ip(request)}", 120, 600, "Live-chat message limit reached for this network.")


def enforce_live_typing(request: Request, *, account_id: int | None = None) -> None:
    identity = f"account:{account_id}" if account_id is not None else f"session:{_session_rate_id(request)}"
    consume_rate_limit("live_typing", identity, 60, 60, "Live-chat typing updates are temporarily limited.")
    consume_rate_limit("live_typing_ip", f"ip:{client_ip(request)}", 240, 600, "Live-chat typing updates are temporarily limited for this network.")


def enforce_handoff(request: Request) -> None:
    consume_rate_limit("live_handoff", f"session:{_session_rate_id(request)}", 3, 3600, "Live-chat request limit reached. Please try again later.")
    consume_rate_limit("live_handoff_ip", f"ip:{client_ip(request)}", 10, 3600, "Live-chat request limit reached for this network.")


def enforce_ably_token(request: Request, *, account_id: int | None = None) -> None:
    identity = f"account:{account_id}" if account_id is not None else f"session:{_session_rate_id(request)}"
    consume_rate_limit("ably_token", identity, 10, 60, "Too many live-chat connection attempts. Please wait a moment.")


def enforce_lead(request: Request, email: str) -> None:
    consume_rate_limit("chatbot_lead_contact", f"email:{email.lower()}", 3, 86400, "Lead submission limit reached. Please contact Batangas Premium directly.")
    consume_rate_limit("chatbot_lead_ip", f"ip:{client_ip(request)}", 3, 86400, "Lead submission limit reached. Please contact Batangas Premium directly.")
