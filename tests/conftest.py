from __future__ import annotations

import os


# The database pool is lazy, so tests can import the app without a live database.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://meattrack:meattrack@127.0.0.1:5433/meattrack",
)
os.environ.setdefault("APP_ENV", "development")
