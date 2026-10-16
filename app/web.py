from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette import status

from app.business_time import BUSINESS_ZONE


BASE_DIR = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

PRODUCT_IMAGE_FILENAMES = {
    "bacon smoked": "bacon_smoked.jpg",
    "beef longganisa": "beef_longganisa.jpg",
    "beef tapa ala eh": "beef_tapa_ala_eh.jpg",
    "cheesy overload sausage": "cheesy_overload_sausage.jpg",
    "chicken rebusado": "chicken_rebusado.jpg",
    "chicken tocino": "chicken_tocino.jpg",
    "deli beef": "deli_beef.jpg",
    "hamon ala eh": "hamon_ala_eh.jpg",
    "hungarian sausage": "hungarian_sausage.jpg",
    "pork garlic longganisa": "pork_garlic_longganisa.jpg",
    "pork rebusado": "pork_rebusado.jpg",
    "pork tapa": "pork_tapa.jpg",
    "spicy garlic longganisa": "spicy_garlic_longganisa.jpg",
    "tocino ala eh": "tocino_ala_eh.jpg",
    "trial package": "trial_package.jpg",
    "resellers package": "reseller_package.jpg",
    "area distributors package": "area_distributor_package.jpg",
    "area distributor s package": "area_distributor_package.jpg",
    "triple garlic longganisa": "triple_garlic_longganisa.jpg",
}

PRODUCT_IMAGE_KEYWORDS = [
    ("bacon", "bacon_smoked.jpg"),
    ("tocino", "tocino_ala_eh.jpg"),
    ("longganisa", "pork_garlic_longganisa.jpg"),
    ("sausage", "hungarian_sausage.jpg"),
    ("tapa", "beef_tapa_ala_eh.jpg"),
    ("rebusado", "pork_rebusado.jpg"),
    ("ham", "hamon_ala_eh.jpg"),
    ("beef", "deli_beef.jpg"),
    ("chicken", "chicken_tocino.jpg"),
    ("pork", "pork_tapa.jpg"),
]


def currency(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"PHP {value:,.2f}"


def number(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def nice_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        localized = value.astimezone(BUSINESS_ZONE) if value.tzinfo is not None else value
        return localized.strftime("%b %d, %Y %I:%M %p")
    if isinstance(value, date):
        return value.strftime("%b %d, %Y")
    return str(value)


def product_image(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    if key in PRODUCT_IMAGE_FILENAMES:
        return PRODUCT_IMAGE_FILENAMES[key]
    for keyword, filename in PRODUCT_IMAGE_KEYWORDS:
        if keyword in key:
            return filename
    return "hero-meat-counter.png"


def media_url(filename: str) -> str:
    return f"/static/img/{quote(filename, safe='')}"


def configure_templates(csrf_token) -> None:
    templates.env.globals["csrf_token"] = csrf_token
    templates.env.filters.update(
        currency=currency,
        number=number,
        nice_date=nice_date,
        product_image=product_image,
        media_url=media_url,
    )


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
