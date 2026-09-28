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
RATE_LIMIT_HASH_KEY = os.getenv("RATE_LIMIT_HASH_KEY", "").strip()
if not RATE_LIMIT_HASH_KEY and APP_ENV != "production":
    RATE_LIMIT_HASH_KEY = SESSION_SECRET_KEY + ":rate-limits"
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
    if not RATE_LIMIT_HASH_KEY:
        raise RuntimeError("Production startup refused: RATE_LIMIT_HASH_KEY is required.")
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

ABLY_API_KEY = os.getenv("ABLY_API_KEY", "").strip()
ABLY_TOKEN_SIGNING_KEY = os.getenv("ABLY_TOKEN_SIGNING_KEY", "").strip() or ABLY_API_KEY
ABLY_REST_BASE_URL = os.getenv("ABLY_REST_BASE_URL", "https://rest.ably.io").strip().rstrip("/")
LIVE_CHAT_ENABLED = env_bool("LIVE_CHAT_ENABLED", bool(ABLY_API_KEY))
ABLY_TOKEN_TTL_SECONDS = int(os.getenv("ABLY_TOKEN_TTL_SECONDS", "900"))
LIVE_CHAT_ACCEPT_TIMEOUT_SECONDS = int(os.getenv("LIVE_CHAT_ACCEPT_TIMEOUT_SECONDS", "120"))
LIVE_CHAT_PRESENCE_STALE_SECONDS = int(os.getenv("LIVE_CHAT_PRESENCE_STALE_SECONDS", "45"))
LIVE_CHAT_RECONNECT_GRACE_SECONDS = int(os.getenv("LIVE_CHAT_RECONNECT_GRACE_SECONDS", "60"))
CHAT_TRANSCRIPT_RETENTION_DAYS = int(os.getenv("CHAT_TRANSCRIPT_RETENTION_DAYS", "30"))

if APP_ENV == "production" and LIVE_CHAT_ENABLED and not ABLY_API_KEY:
    raise RuntimeError("Production startup refused: ABLY_API_KEY is required when live chat is enabled.")
if APP_ENV == "production" and LIVE_CHAT_ENABLED and not ABLY_TOKEN_SIGNING_KEY:
    raise RuntimeError("Production startup refused: ABLY_TOKEN_SIGNING_KEY is required when live chat is enabled.")
