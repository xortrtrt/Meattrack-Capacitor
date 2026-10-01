from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.security_controls import RateLimitExceeded


def rate_limit_response(exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        {"error": "rate_limited", "message": str(exc), "retry_after": exc.retry_after},
        status_code=429,
        headers={"Retry-After": str(exc.retry_after)},
    )


async def json_payload(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Use application/json.")
    try:
        payload = await request.json()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Send a valid JSON object.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Send a valid JSON object.")
    return payload

