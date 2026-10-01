from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette import status


BASE_DIR = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def path_with_query(path: str, **params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return path
    return path + "?" + urlencode(clean)


def require_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    return email


def require_date_range(period_start: date, period_end: date) -> None:
    if period_end < period_start:
        raise ValueError("Period end cannot be earlier than period start.")
