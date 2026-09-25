from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

APP_ENV = os.getenv("APP_ENV", "development")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "change-this-local-dev-secret")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


AUTH_RATE_LIMIT_ENABLED = env_bool("AUTH_RATE_LIMIT_ENABLED", APP_ENV == "production")
CSRF_PROTECTION_ENABLED = env_bool("CSRF_PROTECTION_ENABLED", APP_ENV == "production")
BUSINESS_TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Manila").strip() or "Asia/Manila"
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "meattrack_session").strip() or "meattrack_session"
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")

if APP_ENV == "production":
    disabled_controls = [
        name
        for name, enabled in (
            ("AUTH_RATE_LIMIT_ENABLED", AUTH_RATE_LIMIT_ENABLED),
            ("CSRF_PROTECTION_ENABLED", CSRF_PROTECTION_ENABLED),
        )
        if not enabled
    ]
    if disabled_controls:
        raise RuntimeError(
            "Production startup refused: required security controls are disabled: "
            + ", ".join(disabled_controls)
        )
LOGIN_OTP_ENABLED = os.getenv(
    "LOGIN_OTP_ENABLED",
    "true" if APP_ENV == "production" else "false",
).strip().lower() in {"1", "true", "yes", "on"}

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    database_user = quote(os.getenv("POSTGRES_USER", "meattrack"), safe="")
    database_password = quote(os.getenv("POSTGRES_PASSWORD", "meattrack"), safe="")
    database_host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    database_port = os.getenv("POSTGRES_PORT", "55433")
    database_name = quote(os.getenv("POSTGRES_DB", "MeatTrack Database"), safe="")
    DATABASE_URL = (
        f"postgresql://{database_user}:{database_password}"
        f"@{database_host}:{database_port}/{database_name}"
    )
DATABASE_POOL_MIN = int(os.getenv("DATABASE_POOL_MIN", "1"))
DATABASE_POOL_MAX = int(os.getenv("DATABASE_POOL_MAX", "5"))

CONSENT_VERSION = os.getenv("CONSENT_VERSION", "2026-07-22")

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
BREVO_API_URL = os.getenv("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email").strip()
BREVO_FROM_EMAIL = os.getenv("BREVO_FROM_EMAIL", "").strip()
BREVO_FROM_NAME = os.getenv("BREVO_FROM_NAME", "Batangas Premium").strip()


def database_dsn(value: str = DATABASE_URL) -> str:
    """Normalize PostgreSQL URLs accepted by psycopg2."""
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    return value

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "demo123")
TEAM_LEADER_PASSWORD = os.getenv("TEAM_LEADER_PASSWORD", "demo1234")
RESELLER_PASSWORD = os.getenv("RESELLER_PASSWORD", "demo1234")
DEFAULT_ACCOUNT_PASSWORD = os.getenv("DEFAULT_ACCOUNT_PASSWORD", "demo1234")
