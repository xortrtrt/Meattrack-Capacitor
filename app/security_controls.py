from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from app.config import APP_ENV, AUTH_RATE_LIMIT_ENABLED, CSRF_PROTECTION_ENABLED
from app.database import get_transaction_cursor


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


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
    key = request.scope.get("meattrack.session_token") or client_ip(request)
    enforce_limit("chatbot", str(key), 30, "1 minute", "Chat limit reached. Please wait a minute.")
    record_event("chatbot", str(key))


def enforce_lead(request: Request, email: str) -> None:
    key = f"{email.lower()}|{client_ip(request)}"
    enforce_limit("chatbot_lead", key, 3, "1 day", "Lead submission limit reached. Please contact Batangas Premium directly.")
    record_event("chatbot_lead", key)
