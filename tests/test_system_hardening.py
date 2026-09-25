from __future__ import annotations

import asyncio
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import repositories, security_controls, web_sessions
from app.business_time import business_now, business_today


def test_money_parser_preserves_exact_cents_and_rejects_float_syntax():
    assert repositories.parse_money("129") == Decimal("129.00")
    assert repositories.parse_money("129.50") == Decimal("129.50")
    for value in ("129.501", "1e2", "NaN", "Infinity", "-1", "+1", ""):
        with pytest.raises(ValueError):
            repositories.parse_money(value)


def test_business_clock_is_manila_aware():
    now = business_now()
    assert now.tzinfo is not None
    assert getattr(now.tzinfo, "key", None) == "Asia/Manila"
    assert business_today() == now.date()


def test_production_configuration_refuses_disabled_security_controls():
    project_root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env.update(
        APP_ENV="production",
        AUTH_RATE_LIMIT_ENABLED="false",
        CSRF_PROTECTION_ENABLED="true",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "required security controls are disabled" in result.stderr


def test_csrf_rejects_missing_token_when_enabled(monkeypatch):
    class Request:
        method = "POST"
        headers = {"content-type": "application/json"}
        session = {"csrf_token": "expected"}

    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    with pytest.raises(Exception) as exc:
        asyncio.run(security_controls.enforce_csrf(Request()))
    assert getattr(exc.value, "status_code", None) == 403


def test_session_identifier_is_hashed_before_storage():
    token_hash = web_sessions._hash_token("opaque-browser-token")
    assert token_hash != "opaque-browser-token"
    assert len(token_hash) == 64
    assert "private@example.test" not in token_hash


def test_notification_queries_are_account_scoped(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(repositories, "fetch_all", lambda query, params=None: calls.append((query, params)) or [])
    repositories.list_notifications("team-leader", 42)
    assert "notification_recipients" in calls[0][0]
    assert "nr.account_id = %s" in calls[0][0]
    assert calls[0][1] == (42, 8)


def test_checkout_entrypoint_uses_atomic_cart_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repositories,
        "create_order_from_items",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"order_id": 1},
    )
    result = repositories.checkout_reseller_cart(7, "note")
    assert result["order_id"] == 1
    assert calls == [(("reseller", [], "note"), {"account_id": 7, "checkout_cart": True})]
