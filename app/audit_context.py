from __future__ import annotations

import hashlib
import hmac
import uuid
from contextvars import ContextVar

from fastapi import Request

from app.config import SESSION_SECRET_KEY


_request_id: ContextVar[str] = ContextVar("audit_request_id", default="")
_source_key: ContextVar[str] = ContextVar("audit_source_key", default="")


def begin_audit_context(request: Request):
    supplied = request.headers.get("x-request-id", "").strip()
    request_id = supplied[:100] if supplied else str(uuid.uuid4())
    source = request.client.host if request.client else "unknown"
    source_key = hmac.new(
        (SESSION_SECRET_KEY + ":audit-source").encode("utf-8"),
        source.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return _request_id.set(request_id), _source_key.set(source_key)


def end_audit_context(tokens) -> None:
    request_token, source_token = tokens
    _request_id.reset(request_token)
    _source_key.reset(source_token)


def current_audit_context() -> tuple[str, str]:
    return _request_id.get(), _source_key.get()


__all__ = ["begin_audit_context", "current_audit_context", "end_audit_context"]
