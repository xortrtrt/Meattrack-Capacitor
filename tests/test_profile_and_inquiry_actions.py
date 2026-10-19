from __future__ import annotations

import asyncio
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import main, portal_dependencies, repositories
from app.config import CONSENT_VERSION
from app.routers import auth as auth_routes
from app.portals import shared as shared_portal
from app.repositories import accounts as account_repository
from app.repositories import inquiries as inquiry_repository
from app.repositories import orders as order_repository
from app.security import PASSWORD_POLICY_MESSAGE, hash_password, verify_password


STRONG_NEW_PASSWORD = "New-password1!"


class RequestStub:
    def __init__(self, session=None):
        self.session = dict(session or {})


def test_change_account_password_updates_hash_and_invalidates_old_password(monkeypatch):
    stored_hash = hash_password("old-password")
    captured = {}

    monkeypatch.setattr(
        account_repository,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": stored_hash},
    )
    monkeypatch.setattr(account_repository, "add_log", lambda *args, **kwargs: None)

    def fake_execute_write(query, params=None, returning=False):
        captured["password_hash"] = params[0]

    monkeypatch.setattr(account_repository, "execute_write", fake_execute_write)

    repositories.change_account_password(7, "old-password", STRONG_NEW_PASSWORD)

    assert not verify_password("old-password", captured["password_hash"])
    assert verify_password(STRONG_NEW_PASSWORD, captured["password_hash"])


def test_change_account_password_rejects_weak_new_password(monkeypatch):
    stored_hash = hash_password("old-password")
    monkeypatch.setattr(
        account_repository,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": stored_hash},
    )
    monkeypatch.setattr(account_repository, "execute_write", lambda *args, **kwargs: pytest.fail("weak password must not be saved"))

    with pytest.raises(ValueError, match=PASSWORD_POLICY_MESSAGE):
        repositories.change_account_password(7, "old-password", "new-password")


def test_change_account_password_rejects_wrong_current_password(monkeypatch):
    monkeypatch.setattr(
        account_repository,
        "fetch_one",
        lambda query, params=None: {"account_id": 7, "name": "Demo Reseller", "password_hash": hash_password("right-password")},
    )

    with pytest.raises(ValueError, match="Current password is incorrect"):
        repositories.change_account_password(7, "wrong-password", STRONG_NEW_PASSWORD)


def test_profile_password_fields_render_live_rule_tracking():
    reseller_profile = open("app/templates/portals/reseller/profile.html", encoding="utf-8").read()
    team_profile = open("app/templates/portals/team-leader/profile.html", encoding="utf-8").read()
    app_js = open("app/static/js/forms.js", encoding="utf-8").read()
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


def test_profile_picture_controls_render_for_all_portals():
    owner_profile = open("app/templates/portals/owner/profile.html", encoding="utf-8").read()
    reseller_profile = open("app/templates/portals/reseller/profile.html", encoding="utf-8").read()
    team_profile = open("app/templates/portals/team-leader/profile.html", encoding="utf-8").read()
    profile_partial = open("app/templates/portals/_profile_picture.html", encoding="utf-8").read()
    base_template = open("app/templates/portals/base.html", encoding="utf-8").read()
    portal_css = open("app/static/css/portal_base.css", encoding="utf-8").read()

    for template in (owner_profile, reseller_profile, team_profile):
        assert 'include "portals/_profile_picture.html"' in template

    assert "data-profile-picture-open" in profile_partial
    assert "data-profile-picture-modal" in profile_partial
    assert "profile-picture-file-button" in profile_partial
    assert 'action="/portal/{{ role_key }}/profile/picture"' in profile_partial
    assert 'accept="image/png,image/jpeg,image/webp"' in profile_partial
    assert 'name="profile_image_position_x"' in profile_partial
    assert 'name="profile_image_position_y"' in profile_partial
    assert 'name="profile_image_scale"' in profile_partial
    assert 'action="/portal/{{ role_key }}/profile/picture/remove"' in profile_partial
    assert "role.profile_image_path" in base_template
    assert "role.profile_image_position_x" in base_template
    assert 'data-lucide="user-round"' in base_template
    assert 'data-lucide="user-round"' in profile_partial
    assert "role.name[:1]|upper" not in base_template
    assert "profile_name[:1]|upper" not in profile_partial
    assert ".profile-picture-panel" in portal_css
    assert ".profile-picture-camera" in portal_css
    assert ".profile-picture-file-button" in portal_css
    assert ".profile-picture-modal" in portal_css
    assert ".portal-user-avatar" in portal_css
    assert ".profile-picture-placeholder" in portal_css


@pytest.mark.parametrize("role_key", ["owner", "team-leader", "sales-supervisor", "reseller"])
def test_profile_picture_upload_route_updates_account(monkeypatch, role_key):
    calls = []

    async def fake_save(account_id, upload):
        calls.append(("save", account_id, upload.filename, upload.content_type))
        return "uploads/profiles/account-7-demo.png"

    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 7)
    monkeypatch.setattr(shared_portal, "save_profile_image_upload", fake_save)
    monkeypatch.setattr(shared_portal, "remove_profile_image_file", lambda path: calls.append(("remove", path)))
    monkeypatch.setattr(
        repositories,
        "update_account_profile_image",
        lambda account_id, path, **kwargs: calls.append(("update", account_id, path, kwargs)) or {
            "profile_image_path": path,
            "profile_image_position_x": kwargs["position_x"],
            "profile_image_position_y": kwargs["position_y"],
            "profile_image_scale": kwargs["scale"],
        },
    )

    response = TestClient(main.app).post(
        f"/portal/{role_key}/profile/picture",
        data={
            "profile_image_position_x": "32",
            "profile_image_position_y": "64",
            "profile_image_scale": "1.25",
        },
        files={"profile_picture": ("avatar.png", b"image-bytes", "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert f"/portal/{role_key}/profile" in response.headers["location"]
    assert ("save", 7, "avatar.png", "image/png") in calls
    assert (
        "update",
        7,
        "uploads/profiles/account-7-demo.png",
        {"position_x": "32", "position_y": "64", "scale": "1.25"},
    ) in calls


def test_reseller_password_route_rejects_mismatched_confirmation(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 7)
    monkeypatch.setattr(repositories, "request_reseller_password_change", lambda *args, **kwargs: pytest.fail("otp request must not run"))

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
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 7)
    monkeypatch.setattr(
        repositories,
        "request_reseller_password_change",
        lambda *args: calls.append(args) or {
            "otp_id": 22,
            "account_id": 7,
            "name": "Demo Reseller",
            "email": "reseller@example.test",
            "otp_code": "123456",
        },
    )
    monkeypatch.setattr(shared_portal, "send_password_change_otp", lambda **kwargs: (True, "sent"))

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
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 7)
    monkeypatch.setattr(
        repositories,
        "request_reseller_password_change",
        lambda *args: {
            "otp_id": 22,
            "account_id": 7,
            "name": "Demo Reseller",
            "email": "reseller@example.test",
            "otp_code": "123456",
        },
    )
    monkeypatch.setattr(shared_portal, "send_password_change_otp", lambda **kwargs: (False, "Email failed."))
    monkeypatch.setattr(repositories, "cancel_reseller_password_change", lambda *args: cancelled.append(args))

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
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 7)
    monkeypatch.setattr(repositories, "confirm_reseller_password_change", lambda *args: calls.append(args))

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
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 7)
    monkeypatch.setattr(repositories, "update_reseller_profile", lambda *args: calls.append(args))

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


def test_development_login_establishes_portal_session_without_otp(monkeypatch):
    account = {
        "account_id": 7,
        "account_type": "owner",
        "role_key": "owner",
        "name": "Owner",
        "email": "owner@example.test",
    }
    consent_calls = []
    monkeypatch.setattr(auth_routes, "LOGIN_OTP_ENABLED", False)
    monkeypatch.setattr(repositories, "authenticate_account", lambda email, password: account)
    monkeypatch.setattr(repositories, "record_user_consent", lambda *args: consent_calls.append(args))
    monkeypatch.setattr(repositories, "add_log", lambda *args, **kwargs: None)

    response = TestClient(main.app).post(
        "/login",
        data={"email": "owner@example.test", "password": "demo1234", "consent": "yes"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/owner/dashboard"
    assert consent_calls == [(7, CONSENT_VERSION, "password_login")]


def test_login_uses_email_otp_when_enabled(monkeypatch):
    calls = []
    account = {
        "account_id": 7,
        "account_type": "owner",
        "role_key": "owner",
        "name": "Owner",
        "email": "owner@example.test",
    }
    monkeypatch.setattr(auth_routes, "LOGIN_OTP_ENABLED", True)
    monkeypatch.setattr(repositories, "authenticate_account", lambda email, password: account)
    monkeypatch.setattr(repositories, "request_login_otp", lambda account_id: {"otp_id": 4, "otp_code": "123456", "account": account})
    monkeypatch.setattr(auth_routes, "send_login_otp", lambda **kwargs: calls.append(kwargs) or (True, "sent"))

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
    monkeypatch.setattr(auth_routes, "LOGIN_OTP_ENABLED", True)
    monkeypatch.setattr(repositories, "confirm_login_otp", lambda account_id, otp_code: account)
    monkeypatch.setattr(repositories, "record_user_consent", lambda *args: consent_calls.append(args))
    monkeypatch.setattr(repositories, "add_log", lambda *args, **kwargs: None)

    request = RequestStub({
        "pending_login_account_id": 7,
        "pending_login_email": "owner@example.test",
        "pending_login_consent_version": "v1",
        "pending_login_consent_source": "password_otp",
    })
    response = asyncio.run(auth_routes.submit_login_otp(request, "123456"))

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/owner/dashboard"
    assert request.session["role_key"] == "owner"
    assert "portal_expires_at" in request.session
    assert consent_calls == [(7, "v1", "password_otp")]


def test_portal_session_expires_after_timestamp():
    request = RequestStub({"role_key": "owner", "portal_expires_at": 1})

    response = portal_dependencies.require_portal_session(request, "owner")

    assert response.status_code == 303
    assert request.session == {}


def test_inquiry_followups_are_not_dispatched_by_web_requests():
    from app import worker

    assert not hasattr(main, "dispatch_due_inquiry_followups")
    assert callable(worker.enqueue_due_inquiry_followups)


def test_team_leader_password_route_sends_otp(monkeypatch):
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 9)
    monkeypatch.setattr(
        repositories,
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
    monkeypatch.setattr(shared_portal, "send_password_change_otp", lambda **kwargs: (True, "sent"))

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
                    "status": "assigned",
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

    monkeypatch.setattr(inquiry_repository, "get_transaction_cursor", fake_transaction)

    with pytest.raises(ValueError, match="already used"):
        repositories.add_reseller_from_inquiry(2, approving_team_leader_account_id=4)

    assert cursor.updated is False


def test_new_inquiry_is_assigned_and_linked_to_sales_supervisor_portal(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, params=None):
            captured["insert_params"] = params

        def fetchone(self):
            return {
                "inquiry_id": 22,
                "name": "Demo Applicant",
                "contact_number": "09170000000",
                "email": "applicant@example.test",
                "business_name": "Demo Store",
                "message": "Interested",
                "status": "assigned",
                "assigned_team_leader_account_id": 14,
                "created_at": None,
            }

    @contextmanager
    def fake_transaction():
        yield Cursor()

    monkeypatch.setattr(inquiry_repository, "get_transaction_cursor", fake_transaction)
    monkeypatch.setattr(
        inquiry_repository,
        "next_sales_supervisor",
        lambda preferred_account_id=None: {
            "account_id": 14,
            "name": "Sales Supervisor",
            "team_leader_role": "sales-supervisor",
        },
    )
    monkeypatch.setattr(inquiry_repository, "_add_log_cursor", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        inquiry_repository,
        "_create_notification_cursor",
        lambda *args, **kwargs: captured.update(notification=kwargs),
    )

    inquiry = repositories.add_inquiry(
        "Demo Applicant",
        "Demo Store",
        "applicant@example.test",
        "09170000000",
        "Interested",
        assigned_team_leader_account_id=4,
    )

    assert captured["insert_params"][-2] == 14
    assert inquiry["assigned_team_leader_account_id"] == 14
    assert captured["notification"]["recipient_account_id"] == 14
    assert captured["notification"]["target_url"] == "/portal/sales-supervisor/inquiries"


def test_sales_supervisor_is_preferred_for_inquiry_assignment(monkeypatch):
    monkeypatch.setattr(
        account_repository,
        "fetch_all",
        lambda *args, **kwargs: [
            {"account_id": 11, "name": "Supervisor A", "team_leader_role": "sales-supervisor"},
            {"account_id": 12, "name": "Supervisor B", "team_leader_role": "sales-supervisor"},
        ],
    )
    monkeypatch.setattr(account_repository, "fetch_one", lambda *args, **kwargs: {"total": 1})

    assert repositories.next_sales_supervisor(12)["account_id"] == 12
    assert repositories.next_sales_supervisor(4)["account_id"] == 12


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
                        "status": "assigned",
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

    monkeypatch.setattr(inquiry_repository, "get_transaction_cursor", fake_transaction)
    monkeypatch.setattr(inquiry_repository, "_create_activation_token_cursor", lambda *args: "activation-token")
    monkeypatch.setattr(inquiry_repository, "_add_log_cursor", lambda *args, **kwargs: None)
    monkeypatch.setattr(inquiry_repository, "_create_notification_cursor", lambda *args, **kwargs: None)
    monkeypatch.setattr(inquiry_repository, "_enqueue_outbox_cursor", lambda *args, **kwargs: None)

    result = repositories.add_reseller_from_inquiry(3, approving_team_leader_account_id=4)

    assert cursor.approved is True
    assert cursor.inserted_reseller is True
    assert cursor.inserted_account is True
    assert result["account_email"] == "reseller@example.test"


def test_reviewed_inquiry_hides_approval_actions(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales-supervisor")
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 4)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_inquiries", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
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

    response = TestClient(main.app).get("/portal/sales-supervisor/inquiries")

    assert response.status_code == 200
    assert "/portal/sales-supervisor/inquiries/2/approve" not in response.text
    assert "Reviewed" in response.text


def test_pending_inquiry_rejection_requires_reason_dialog(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales-supervisor")
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 4)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_inquiries", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
        "list_inquiries",
        lambda *args, **kwargs: [{
            "inquiry_id": 8,
            "business_name": "Pending Store",
            "name": "Pending Applicant",
            "email": "pending@example.test",
            "contact_number": "09170000000",
            "message": "Review me",
            "rejection_reason": None,
            "status": "assigned",
        }],
    )

    response = TestClient(main.app).get("/portal/sales-supervisor/inquiries")

    assert response.status_code == 200
    assert "data-inquiry-reject-open" in response.text
    assert 'name="rejection_reason"' in response.text
    assert 'minlength="5"' in response.text
    assert "Reject and email" in response.text


def test_order_approval_requires_payment_proof(monkeypatch):

    class Cursor:
        def __init__(self):
            self.result = None

        def execute(self, query, params=None):
            if "FROM orders o" in query:
                self.result = {
                    "order_id": 7,
                    "order_type": "reseller",
                    "status": "pending",
                    "created_by_account_id": 9,
                }
            elif "account_type = 'team_leader'" in query:
                self.result = {"account_id": 4}
            elif "SELECT name FROM accounts" in query:
                self.result = {"name": "Sales Leader"}
            elif "FROM order_payment_proofs" in query:
                self.result = None

        def fetchone(self):
            return self.result

    @contextmanager
    def fake_transaction():
        yield Cursor()

    monkeypatch.setattr(order_repository, "get_transaction_cursor", fake_transaction)

    with pytest.raises(ValueError, match="Proof of payment is required"):
        repositories.decide_order(7, "approve", team_leader_account_id=None)


def test_team_order_route_returns_error_when_proof_missing(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "require_team_leader_role", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 4)

    def fake_decide_order(*args, **kwargs):
        raise ValueError("Proof of payment is required before approving this order.")

    monkeypatch.setattr(repositories, "decide_order", fake_decide_order)

    response = TestClient(main.app).post(
        "/portal/team-leader/orders/7/approve",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Proof+of+payment+is+required" in response.headers["location"]


def test_cancel_reseller_order_releases_unpaid_reservation_and_records_reason(monkeypatch):
    queries = []
    order = {
        "order_id": 31,
        "status": "to_pay",
        "created_by_account_id": 7,
        "team_leader_account_id": 4,
        "payment_transaction_number": None,
        "payment_paid_at": None,
        "payment_amount": None,
        "payment_confirmed_at": None,
        "business_name": "Lipa Fresh Mart",
        "has_payment_proof": False,
    }

    class Cursor:
        result = None

        def execute(self, query, params=None):
            queries.append((query, params))
            if "AS has_payment_proof" in query:
                self.result = order
            elif "RETURNING order_id, status, cancellation_reason" in query:
                self.result = {
                    "order_id": 31,
                    "status": "cancelled",
                    "cancellation_reason": "Need to change items or quantities: Use three packs instead.",
                    "cancelled_at": None,
                }

        def fetchone(self):
            return self.result

    @contextmanager
    def fake_transaction():
        yield Cursor()

    logs = []
    messages = []
    notifications = []
    monkeypatch.setattr(order_repository, "get_transaction_cursor", fake_transaction)
    monkeypatch.setattr(
        order_repository,
        "_add_log_cursor",
        lambda *args, **kwargs: logs.append((args, kwargs)),
    )
    monkeypatch.setattr(
        order_repository,
        "_create_order_system_message_cursor",
        lambda *args, **kwargs: messages.append((args, kwargs)) or 1,
    )
    monkeypatch.setattr(
        order_repository,
        "_create_notification_cursor",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    result = repositories.cancel_reseller_order(
        7,
        31,
        "change_items",
        "Use three packs instead.",
    )

    assert result["status"] == "cancelled"
    assert any("DELETE FROM order_stock_reservations" in query for query, _ in queries)
    assert any("SET status = 'cancelled'" in query for query, _ in queries)
    assert logs[0][1]["action"] == "cancelled_reseller_order"
    assert messages[0][1]["event_type"] == "cancelled"
    assert notifications[0][1]["recipient_account_id"] == 4


def test_cancel_reseller_order_rejects_any_payment_evidence(monkeypatch):
    class Cursor:
        def execute(self, query, params=None):
            self.query = query

        def fetchone(self):
            return {
                "order_id": 31,
                "status": "pending",
                "created_by_account_id": 7,
                "team_leader_account_id": 4,
                "payment_transaction_number": None,
                "payment_paid_at": None,
                "payment_amount": None,
                "payment_confirmed_at": None,
                "business_name": "Lipa Fresh Mart",
                "has_payment_proof": True,
            }

    @contextmanager
    def fake_transaction():
        yield Cursor()

    monkeypatch.setattr(order_repository, "get_transaction_cursor", fake_transaction)

    with pytest.raises(ValueError, match="submitted payment cannot be cancelled"):
        repositories.cancel_reseller_order(7, 31, "ordered_by_mistake")


def test_cancel_reseller_order_requires_details_for_other_reason(monkeypatch):
    with pytest.raises(ValueError, match="short explanation"):
        repositories.cancel_reseller_order(7, 31, "other", "")


def test_team_order_rejection_passes_required_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "require_team_leader_role", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 4)
    monkeypatch.setattr(
        repositories,
        "decide_order",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    response = TestClient(main.app).post(
        "/portal/team-leader/orders/7/reject",
        data={"rejection_reason": "The uploaded payment receipt is unreadable.", "payment_received": "no"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(
        (7, "reject"),
        {
            "team_leader_account_id": 4,
            "rejection_reason": "The uploaded payment receipt is unreadable.",
                "payment_received": False,
        },
    )]

