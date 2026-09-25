from __future__ import annotations

import os

import pytest


# The database pool is lazy, so tests can import the app without a live database.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://meattrack:meattrack@127.0.0.1:55433/MeatTrack%20Database",
)
os.environ.setdefault("APP_ENV", "development")


@pytest.fixture(autouse=True)
def isolate_web_session_store(monkeypatch, request):
    """Route unit tests do not require the PostgreSQL session integration."""
    if request.node.get_closest_marker("postgres"):
        return
    from app import web_sessions

    monkeypatch.setattr(web_sessions, "_load_session", lambda token: {})
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, expires_seconds: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda token: None)
