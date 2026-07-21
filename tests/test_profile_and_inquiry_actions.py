from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import main, repositories
from app.security import hash_password, verify_password


def test_change_account_password_updates_hash_and_invalidates_old_password(monkeypatch):
    stored_hash = hash_password("old-password")
    captured = {}

    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": stored_hash},
    )
    monkeypatch.setattr(repositories, "add_log", lambda *args, **kwargs: None)

    def fake_execute_write(query, params=None, returning=False):
        captured["password_hash"] = params[0]

    monkeypatch.setattr(repositories, "execute_write", fake_execute_write)

    repositories.change_account_password(7, "old-password", "new-password")

    assert not verify_password("old-password", captured["password_hash"])
    assert verify_password("new-password", captured["password_hash"])


def test_change_account_password_rejects_wrong_current_password(monkeypatch):
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": hash_password("right-password")},
    )

    with pytest.raises(ValueError, match="Current password is incorrect"):
        repositories.change_account_password(7, "wrong-password", "new-password")


def test_reseller_password_route_rejects_mismatched_confirmation(monkeypatch):
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(main.data, "change_account_password", lambda *args, **kwargs: pytest.fail("password update must not run"))

    response = TestClient(main.app).post(
        "/portal/reseller/profile/password",
        data={
            "current_password": "old-password",
            "new_password": "new-password",
            "confirm_password": "different-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/portal/reseller/profile" in response.headers["location"]
    assert "do+not+match" in response.headers["location"]


def test_reseller_password_route_success(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(main.data, "change_account_password", lambda *args: calls.append(args))

    response = TestClient(main.app).post(
        "/portal/reseller/profile/password",
        data={
            "current_password": "old-password",
            "new_password": "new-password",
            "confirm_password": "new-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(7, "old-password", "new-password")]
    assert parse_qs(urlsplit(response.headers["location"]).query)["message"] == ["Password updated."]


def test_duplicate_email_approval_keeps_inquiry_unapproved(monkeypatch):
    class Cursor:
        def __init__(self):
            self.updated = False
            self.query = ""

        def execute(self, query, params=None):
            self.query = query
            if query.lstrip().upper().startswith("UPDATE INQUIRIES"):
                self.updated = True

        def fetchone(self):
            if "SELECT * FROM inquiries" in self.query:
                return {
                    "inquiry_id": 2,
                    "assigned_team_leader_account_id": 4,
                    "email": "owner@example.test",
                    "business_name": "Demo Store",
                    "name": "Demo",
                    "contact_number": "09170000000",
                }
            if "FROM accounts" in self.query and "lower(email)" in self.query:
                return {"account_id": 1, "account_type": "owner"}
            return None

    cursor = Cursor()

    @contextmanager
    def fake_transaction():
        yield cursor

    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(repositories, "get_transaction_cursor", fake_transaction)

    with pytest.raises(ValueError, match="already used"):
        repositories.add_reseller_from_inquiry(2, approving_team_leader_account_id=4)

    assert cursor.updated is False


def test_successful_approval_creates_reseller_account_and_marks_approved(monkeypatch):
    class Cursor:
        def __init__(self):
            self.query = ""
            self.approved = False
            self.inserted_reseller = False
            self.inserted_account = False

        def execute(self, query, params=None):
            self.query = query
            normalized = query.lstrip().upper()
            if normalized.startswith("UPDATE INQUIRIES"):
                self.approved = True
            if normalized.startswith("INSERT INTO RESELLERS"):
                self.inserted_reseller = True
            if normalized.startswith("INSERT INTO ACCOUNTS"):
                self.inserted_account = True

        def fetchone(self):
            if "SELECT * FROM inquiries" in self.query:
                return {
                    "inquiry_id": 3,
                    "assigned_team_leader_account_id": 4,
                    "email": "reseller@example.test",
                    "business_name": "Demo Store",
                    "name": "Demo",
                    "contact_number": "09170000000",
                }
            if "FROM accounts" in self.query and "lower(email)" in self.query:
                return None
            if "FROM resellers" in self.query:
                return None
            if "INSERT INTO resellers" in self.query:
                return {
                    "reseller_id": 9,
                    "business_name": "Demo Store",
                    "contact_person": "Demo",
                    "email": "reseller@example.test",
                    "contact_number": "09170000000",
                    "address": "Pending onboarding details",
                    "reseller_status": "active",
                    "team_leader_account_id": 4,
                    "approved_by_account_id": 4,
                    "created_at": None,
                }
            if "INSERT INTO accounts" in self.query:
                return {"account_id": 12, "email": "reseller@example.test"}
            if "SELECT name, email FROM accounts" in self.query:
                return {"name": "Sales Leader A", "email": "sales.a@example.test"}
            return None

    cursor = Cursor()

    @contextmanager
    def fake_transaction():
        yield cursor

    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(repositories, "get_transaction_cursor", fake_transaction)
    monkeypatch.setattr(repositories, "add_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(repositories, "create_notification", lambda *args, **kwargs: None)

    result = repositories.add_reseller_from_inquiry(3, approving_team_leader_account_id=4)

    assert cursor.approved is True
    assert cursor.inserted_reseller is True
    assert cursor.inserted_account is True
    assert result["account_email"] == "reseller@example.test"


def test_reviewed_inquiry_hides_approval_actions(monkeypatch):
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "sales")
    monkeypatch.setattr(main, "session_account_id", lambda request: 4)
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_inquiries", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
        "list_inquiries",
        lambda *args, **kwargs: [
            {
                "inquiry_id": 2,
                "business_name": "Reviewed Store",
                "name": "Reviewed",
                "email": "reviewed@example.test",
                "contact_number": "09170000000",
                "message": "Reviewed lead",
                "status": "approved",
            }
        ],
    )

    response = TestClient(main.app).get("/portal/team-leader/inquiries")

    assert response.status_code == 200
    assert "/portal/team-leader/inquiries/2/approve" not in response.text
    assert "Reviewed" in response.text


def test_order_approval_requires_payment_proof(monkeypatch):
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {
            "order_id": 7,
            "order_type": "reseller",
            "status": "pending",
            "created_by_account_id": 9,
        }
        if "FROM orders" in query
        else None,
    )

    with pytest.raises(ValueError, match="Proof of payment is required"):
        repositories.decide_order(7, "approve", team_leader_account_id=None)


def test_team_order_route_returns_error_when_proof_missing(monkeypatch):
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "require_team_leader_role", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 4)

    def fake_decide_order(*args, **kwargs):
        raise ValueError("Proof of payment is required before approving this order.")

    monkeypatch.setattr(main.data, "decide_order", fake_decide_order)

    response = TestClient(main.app).post(
        "/portal/team-leader/orders/7/approve",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Proof+of+payment+is+required" in response.headers["location"]
