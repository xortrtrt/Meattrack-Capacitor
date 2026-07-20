from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

APP_ENV = os.getenv("APP_ENV", "development")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "change-this-local-dev-secret")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://meattrack:meattrack@127.0.0.1:5433/meattrack",
)
DATABASE_POOL_MIN = int(os.getenv("DATABASE_POOL_MIN", "1"))
DATABASE_POOL_MAX = int(os.getenv("DATABASE_POOL_MAX", "5"))

MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL", "").strip().rstrip("/")
if MEDIA_BASE_URL and not MEDIA_BASE_URL.startswith("https://"):
    raise ValueError("MEDIA_BASE_URL must use HTTPS")


def database_dsn(value: str = DATABASE_URL) -> str:
    """Normalize hosted PostgreSQL URLs and require TLS for Supabase."""
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    parts = urlsplit(value)
    hostname = (parts.hostname or "").lower()
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if (hostname.endswith(".supabase.co") or hostname.endswith(".pooler.supabase.com")) and "sslmode" not in query:
        query["sslmode"] = "require"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "demo123")
TEAM_LEADER_PASSWORD = os.getenv("TEAM_LEADER_PASSWORD", "demo1234")
RESELLER_PASSWORD = os.getenv("RESELLER_PASSWORD", "demo1234")
DEFAULT_ACCOUNT_PASSWORD = os.getenv("DEFAULT_ACCOUNT_PASSWORD", "demo1234")
