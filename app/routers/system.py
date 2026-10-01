from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse

from app import repositories as data
from app.web import media_url


MEDIA_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health():
    """Deployment probe that verifies the application and database."""
    try:
        result = data.database_health()
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "unavailable"}, status_code=503)
    return {"status": "ok", "database": result}




@router.get("/media/{filename}")
async def media_asset(filename: str):
    if not MEDIA_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404)
    return RedirectResponse(
        media_url(filename),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "public, max-age=3600"},
    )



