from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import BUSINESS_TIMEZONE


BUSINESS_ZONE = ZoneInfo(BUSINESS_TIMEZONE)


def business_now() -> datetime:
    """Return the timezone-aware current time used for business decisions."""
    return datetime.now(BUSINESS_ZONE)


def business_today() -> date:
    return business_now().date()
