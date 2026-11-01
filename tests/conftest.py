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
    from app.portals import team_leader
    monkeypatch.setattr(team_leader,"leader_work_summary",lambda *args:{"counts":{},"queue":[],"attention_total":0,"recent":[]})
    from app import web_sessions
    from app import repositories
    from app.repositories import replacement_messages
    monkeypatch.setattr(replacement_messages,"workspace",lambda *args,**kwargs:{"replacement_threads":[],"replacement_chat":None})
    monkeypatch.setattr(replacement_messages,"threads",lambda *args,**kwargs:[])

    monkeypatch.setattr(repositories,"settlement_summary",lambda *args,**kwargs:{})
    monkeypatch.setattr(repositories,"team_sales_refund_entries",lambda *args,**kwargs:[])

    monkeypatch.setattr(web_sessions, "_load_session", lambda token: {})
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, expires_seconds: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda token: None)
