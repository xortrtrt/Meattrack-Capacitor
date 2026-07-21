from __future__ import annotations

import asyncio
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import main, repositories
from app.security import PASSWORD_POLICY_MESSAGE, hash_password, verify_password


STRONG_NEW_PASSWORD = "New-password1!"


class RequestStub:
    def __init__(self, session=None):
        self.session = dict(session or {})


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

    repositories.change_account_password(7, "old-password", STRONG_NEW_PASSWORD)

    assert not verify_password("old-password", captured["password_hash"])
    assert verify_password(STRONG_NEW_PASSWORD, captured["password_hash"])


def test_change_account_password_rejects_weak_new_password(monkeypatch):
    stored_hash = hash_password("old-password")
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": stored_hash},
    )
    monkeypatch.setattr(repositories, "execute_write", lambda *args, **kwargs: pytest.fail("weak password must not be saved"))

    with pytest.raises(ValueError, match=PASSWORD_POLICY_MESSAGE):
        repositories.change_account_password(7, "old-password", "new-password")


def test_change_account_password_rejects_wrong_current_password(monkeypatch):
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": hash_password("right-password")},
    )

    with pytest.raises(ValueError, match="Current password is incorrect"):
        repositories.change_account_password(7, "wrong-password", STRONG_NEW_PASSWORD)


def test_profile_password_fields_render_live_rule_tracking():
    reseller_profile = open("app/templates/portals/reseller/profile.html", encoding="utf-8").read()
    team_profile = open("app/templates/portals/team-leader/profile.html", encoding="utf-8").read()
    app_js = open("app/static/js/app.js", encoding="utf-8").read()
    portal_css = open("app/static/css/portal_base.css", encoding="utf-8").read()

    for template in (reseller_profile, team_profile):
        assert "data-password-rules-input" in template
        assert "data-password-rule-list" in template
        assert 'data-password-rule="length"' in template
        assert 'data-password-rule="uppercase"' in template
        assert 'data-password-rule="lowercase"' in template
        assert 'data-password-rule="number"' in template
        assert 'data-password-rule="special"' in template

    assert "function bindPasswordRuleTracking()" in app_js
    assert "bindPasswordRuleTracking();" in app_js
    assert ".password-rule-list" in portal_css
    assert ".password-rule-list li.is-met" in portal_css


def test_reseller_password_route_rejects_mismatched_confirmation(monkeypatch):
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(main.data, "request_reseller_password_change", lambda *args, **kwargs: pytest.fail("otp request must not run"))

    response = TestClient(main.app).post(
        "/portal/reseller/profile/password",
        data={
            "current_password": "old-password",
            "new_password": STRONG_NEW_PASSWORD,
            "confirm_password": "different-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/portal/reseller/profile" in response.headers["location"]
    assert "do+not+match" in response.headers["location"]


def test_reseller_password_route_sends_otp(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(
        main.data,
        "request_reseller_password_change",
        lambda *args: calls.append(args) or {
            "otp_id": 22,
            "account_id": 7,
            "name": "Demo Reseller",
            "email": "reseller@example.test",
            "otp_code": "123456",
        },
    )
    monkeypatch.setattr(main, "send_password_change_otp", lambda **kwargs: (True, "sent"))

    response = TestClient(main.app).post(
        "/portal/reseller/profile/password",
        data={
            "current_password": "old-password",
            "new_password": STRONG_NEW_PASSWORD,
            "confirm_password": STRONG_NEW_PASSWORD,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(7, "old-password", STRONG_NEW_PASSWORD)]
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["otp"] == ["1"]
    assert query["message"] == ["OTP sent to your account email. Enter it below to confirm your password change."]


def test_reseller_password_route_cancels_pending_otp_when_email_fails(monkeypatch):
    cancelled = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(
        main.data,
        "request_reseller_password_change",
        lambda *args: {
            "otp_id": 22,
            "account_id": 7,
            "name": "Demo Reseller",
            "email": "reseller@example.test",
            "otp_code": "123456",
        },
    )
    monkeypatch.setattr(main, "send_password_change_otp", lambda **kwargs: (False, "Email failed."))
    monkeypatch.setattr(main.data, "cancel_reseller_password_change", lambda *args: cancelled.append(args))

    response = TestClient(main.app).post(
        "/portal/reseller/profile/password",
        data={
            "current_password": "old-password",
            "new_password": STRONG_NEW_PASSWORD,
            "confirm_password": STRONG_NEW_PASSWORD,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert cancelled == [(7, 22)]
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["Email failed."]


def test_reseller_password_confirm_route_success(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(main.data, "confirm_reseller_password_change", lambda *args: calls.append(args))

    response = TestClient(main.app).post(
        "/portal/reseller/profile/password/confirm",
        data={"otp_code": "123456"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(7, "123456")]
    assert parse_qs(urlsplit(response.headers["location"]).query)["message"] == ["Password updated."]


def test_reseller_profile_update_route_success(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 7)
    monkeypatch.setattr(main.data, "update_reseller_profile", lambda *args: calls.append(args))

    with TestClient(main.app) as client:
        response = client.post(
            "/portal/reseller/profile",
            data={
                "name": "Updated Person",
                "business_name": "Updated Store",
                "contact_number": "09170000000",
                "address": "Batangas",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert calls == [(7, "Updated Person", "Updated Store", "09170000000", "Batangas")]
    assert parse_qs(urlsplit(response.headers["location"]).query)["message"] == ["Profile updated."]


def test_login_requires_email_otp_before_portal_session(monkeypatch):
    calls = []
    account = {
        "account_id": 7,
        "account_type": "owner",
        "role_key": "owner",
        "name": "Owner",
        "email": "owner@example.test",
    }
    monkeypatch.setattr(main.data, "authenticate_account", lambda email, password: account)
    monkeypatch.setattr(main.data, "request_login_otp", lambda account_id: {"otp_id": 4, "otp_code": "123456", "account": account})
    monkeypatch.setattr(main, "send_login_otp", lambda **kwargs: calls.append(kwargs) or (True, "sent"))

    response = TestClient(main.app).post(
        "/login",
        data={"email": "owner@example.test", "password": "demo1234", "consent": "yes"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["message"] == ["OTP sent to your account email. Enter it below to finish signing in."]
    assert calls == [{"to_email": "owner@example.test", "name": "Owner", "otp_code": "123456"}]


def test_login_otp_confirmation_establishes_portal_session(monkeypatch):
    account = {
        "account_id": 7,
        "account_type": "owner",
        "role_key": "owner",
        "name": "Owner",
        "email": "owner@example.test",
    }
    consent_calls = []
    monkeypatch.setattr(main.data, "confirm_login_otp", lambda account_id, otp_code: account)
    monkeypatch.setattr(main.data, "record_user_consent", lambda *args: consent_calls.append(args))
    monkeypatch.setattr(main.data, "add_log", lambda *args, **kwargs: None)

    request = RequestStub({
        "pending_login_account_id": 7,
        "pending_login_email": "owner@example.test",
        "pending_login_consent_version": "v1",
        "pending_login_consent_source": "password_otp",
    })
    response = asyncio.run(main.submit_login_otp(request, "123456"))

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/owner/dashboard"
    assert request.session["role_key"] == "owner"
    assert "portal_expires_at" in request.session
    assert consent_calls == [(7, "v1", "password_otp")]


def test_portal_session_expires_after_timestamp():
    request = RequestStub({"role_key": "owner", "portal_expires_at": 1})

    response = main.require_portal_session(request, "owner")

    assert response.status_code == 303
    assert request.session == {}


def test_dispatch_due_inquiry_followups_sends_and_marks(monkeypatch):
    sent = []
    marked = []
    monkeypatch.setattr(
        main.data,
        "due_inquiry_followups",
        lambda limit=20: [
            {
                "inquiry_id": 2,
                "name": "Potential Reseller",
                "email": "lead@example.test",
                "business_name": "Lead Store",
            }
        ],
    )
    monkeypatch.setattr(main, "send_inquiry_status_update", lambda **kwargs: sent.append(kwargs) or (True, "sent"))
    monkeypatch.setattr(main.data, "mark_inquiry_followup_sent", lambda inquiry_id: marked.append(inquiry_id))

    main.dispatch_due_inquiry_followups()

    assert sent == [{"to_email": "lead@example.test", "name": "Potential Reseller", "business_name": "Lead Store"}]
    assert marked == [2]


def test_team_leader_password_route_sends_otp(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 9)
    monkeypatch.setattr(
        main.data,
        "request_account_password_change",
        lambda *args: calls.append(args) or {
            "otp_id": 33,
            "account_id": 9,
            "account_type": "team_leader",
            "name": "Sales Leader",
            "email": "sales@example.test",
            "otp_code": "456789",
        },
    )
    monkeypatch.setattr(main, "send_password_change_otp", lambda **kwargs: (True, "sent"))

    response = TestClient(main.app).post(
        "/portal/team-leader/profile/password",
        data={
            "current_password": "old-password",
            "new_password": STRONG_NEW_PASSWORD,
            "confirm_password": STRONG_NEW_PASSWORD,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(9, "old-password", STRONG_NEW_PASSWORD, ("team_leader",))]
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["otp"] == ["1"]


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
