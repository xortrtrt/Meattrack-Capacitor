from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders

from app.business_time import business_now
from app.config import APP_ENV, SESSION_COOKIE_NAME
from app.database import get_transaction_cursor


PUBLIC_SESSION_SECONDS = 24 * 60 * 60
PORTAL_SESSION_SECONDS = 2 * 60 * 60


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _cookie_value(headers: list[tuple[bytes, bytes]]) -> str | None:
    cookie = SimpleCookie()
    for key, value in headers:
        if key.lower() == b"cookie":
            cookie.load(value.decode("latin-1"))
    morsel = cookie.get(SESSION_COOKIE_NAME)
    return morsel.value if morsel else None


def _load_session(token: str | None) -> dict:
    if not token:
        return {}
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT data
            FROM web_sessions
            WHERE token_hash = %s AND expires_at > now()
            """,
            (_hash_token(token),),
        )
        row = cur.fetchone()
    return dict(row["data"]) if row else {}


def _persist_session(token: str, data: dict, expires_seconds: int) -> None:
    account_id = data.get("account_id")
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            INSERT INTO web_sessions (token_hash, account_id, data, expires_at)
            VALUES (%s, %s, %s::jsonb, now() + make_interval(secs => %s))
            ON CONFLICT (token_hash) DO UPDATE
            SET account_id = EXCLUDED.account_id,
                data = EXCLUDED.data,
                expires_at = EXCLUDED.expires_at,
                updated_at = now()
            """,
            (_hash_token(token), account_id, json.dumps(data), expires_seconds),
        )


def _delete_session(token: str | None) -> None:
    if not token:
        return
    with get_transaction_cursor() as cur:
        cur.execute("DELETE FROM web_sessions WHERE token_hash = %s", (_hash_token(token),))


def rotate_session(request) -> None:
    """Rotate the opaque identifier after a privilege or credential boundary."""
    scope = getattr(request, "scope", None)
    if scope is not None:
        scope["meattrack.rotate_session"] = True


def revoke_account_sessions(account_id: int, *, except_token: str | None = None) -> None:
    with get_transaction_cursor() as cur:
        if except_token:
            cur.execute(
                "DELETE FROM web_sessions WHERE account_id = %s AND token_hash <> %s",
                (account_id, _hash_token(except_token)),
            )
        else:
            cur.execute("DELETE FROM web_sessions WHERE account_id = %s", (account_id,))


class DatabaseSessionMiddleware:
    """PostgreSQL-backed opaque sessions; only a random identifier reaches the browser."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_token = _cookie_value(scope.get("headers", []))
        loaded = _load_session(incoming_token)
        original = dict(loaded)
        scope["session"] = loaded
        scope["meattrack.session_token"] = incoming_token

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                session = scope["session"]
                rotate = bool(scope.get("meattrack.rotate_session"))
                token = incoming_token
                headers = MutableHeaders(scope=message)

                if not session:
                    if token:
                        _delete_session(token)
                        headers.append(
                            "set-cookie",
                            f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
                        )
                elif session != original or rotate:
                    if rotate and token:
                        _delete_session(token)
                        token = None
                    token = token or _new_token()
                    seconds = PORTAL_SESSION_SECONDS if session.get("account_id") else PUBLIC_SESSION_SECONDS
                    _persist_session(token, session, seconds)
                    cookie = (
                        f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={seconds}; "
                        "HttpOnly; SameSite=Lax"
                    )
                    if APP_ENV == "production":
                        cookie += "; Secure"
                    headers.append("set-cookie", cookie)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def cleanup_expired_sessions() -> int:
    with get_transaction_cursor() as cur:
        cur.execute("DELETE FROM web_sessions WHERE expires_at <= now()")
        return cur.rowcount
