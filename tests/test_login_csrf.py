from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from app import main, security_controls, web_sessions
from app.config import SESSION_COOKIE_NAME
from app.routers import auth


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.forms.append({"action": attrs.get("action"), "token": None})
        if tag == "input" and attrs.get("name") == "csrf_token":
            self.forms[-1]["token"] = attrs.get("value")


@pytest.fixture
def login_client(monkeypatch):
    sessions = {}
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(sessions.get(token, {})))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, expires: sessions.update({token: dict(data)}))
    monkeypatch.setattr(web_sessions, "_delete_session", lambda token: sessions.pop(token, None))
    monkeypatch.setattr(auth, "LOGIN_OTP_ENABLED", False)
    monkeypatch.setattr(auth, "enforce_password_login", lambda *args: None)
    monkeypatch.setattr(auth, "record_password_login", lambda *args: None)
    monkeypatch.setattr(auth.data, "record_user_consent", lambda *args: None)
    monkeypatch.setattr(auth.data, "add_log", lambda *args: None)
    return TestClient(main.app), sessions


@pytest.mark.parametrize("role", ["sales-supervisor", "owner", "reseller"])
def test_login_posts_server_rendered_csrf_without_javascript(login_client, monkeypatch, role):
    client, _ = login_client
    monkeypatch.setattr(auth.data, "authenticate_account", lambda *args: {
        "account_id": 42, "role_key": role, "name": "Test account", "email": "test@example.com",
        "team_leader_role": "sales-supervisor" if role == "sales-supervisor" else None,
    })
    page = client.get("/login")
    assert page.headers["cache-control"] == "no-store"
    token = Forms(page.text).forms[0]["token"]
    assert token
    response = client.post("/login", data={"csrf_token": token, "email": "test@example.com", "password": "test-only", "consent": "yes"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/portal/{role}/")


def test_expired_login_token_rejects_authentication_and_renders_retry_form(login_client, monkeypatch):
    client, sessions = login_client
    calls = []
    monkeypatch.setattr(auth.data, "authenticate_account", lambda *args: calls.append(args))
    old_token = Forms(client.get("/login").text).forms[0]["token"]
    sessions.clear()
    response = client.post("/login", data={"csrf_token": old_token, "email": "test@example.com", "password": "test-only", "consent": "yes"})
    assert response.status_code == 403
    assert not calls
    assert "Your sign-in page expired" in response.text
    assert response.headers["cache-control"] == "no-store"
    new_token = Forms(response.text).forms[0]["token"]
    assert new_token and new_token != old_token
    retry = client.post("/login", data={"csrf_token": new_token, "email": "test@example.com", "password": "test-only", "consent": "yes"}, follow_redirects=False)
    assert retry.status_code == 303
    assert len(calls) == 1


def test_login_json_csrf_errors_remain_machine_readable(login_client):
    client, _ = login_client
    response = client.post("/login", headers={"Accept": "application/json"}, data={})
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid or missing CSRF token."}


def test_otp_and_use_another_account_forms_have_tokens(login_client, monkeypatch):
    client, sessions = login_client
    client.get("/login")
    sessions[client.cookies.get(SESSION_COOKIE_NAME)]["pending_login_account_id"] = 42
    monkeypatch.setattr(auth, "LOGIN_OTP_ENABLED", True)
    forms = Forms(client.get("/login").text).forms
    assert [form["action"] for form in forms] == ["/login/otp", "/logout"]
    assert all(form["token"] for form in forms)
    assert forms[0]["token"] == forms[1]["token"]
