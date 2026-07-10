from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

APP_ENV = os.getenv("APP_ENV", "development")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "change-this-local-dev-secret")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://meattrack:meattrack@127.0.0.1:5433/meattrack",
)

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "demo123")
TEAM_LEADER_PASSWORD = os.getenv("TEAM_LEADER_PASSWORD", "demo1234")
RESELLER_PASSWORD = os.getenv("RESELLER_PASSWORD", "demo1234")
DEFAULT_ACCOUNT_PASSWORD = os.getenv("DEFAULT_ACCOUNT_PASSWORD", "demo1234")
