import re
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions
from app.business_time import business_now, business_today
from app.portals import activity_reports as portal, shared
from app.report_documents import SUMMARY_LABELS


@pytest.fixture
def report_client(monkeypatch):
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, seconds: session.update(data))
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    monkeypatch.setattr(shared, "require_team_leader_role", lambda *args: None)
    monkeypatch.setattr(shared, "session_account_id", lambda *args: 7)
    for name in ("list_sales_reports", "list_notifications"):
        monkeypatch.setattr(repositories, name, lambda *args, **kwargs: [])
    for name in ("count_sales_reports", "unread_notification_count"):
        monkeypatch.setattr(repositories, name, lambda *args, **kwargs: 0)
    today = business_today().isoformat()
    payload = dict(version=1, author={"account_id": 7, "name": "Sales"}, period_start=business_today().replace(day=1).isoformat(), period_end=today,
                   captured_at=business_now().isoformat(), summary={key: 0 for key in SUMMARY_LABELS},
                   sales=[], orders=[], returns=[], refunds=[])
    monkeypatch.setattr(portal.reports, "build_report", lambda *args: payload)
    with TestClient(main.app) as client:
        yield client, payload, session


def preview_token(client):
    response = client.get("/portal/team-leader/reports/preview")
    assert response.status_code == 200
    assert "No sales records" in response.text
    return re.search('name="preview_token" value="([^"]+)"', response.text)[1]


@pytest.mark.parametrize("kind", ["tampered", "other_account", "expired"])
def test_invalid_preview_tokens_never_submit(report_client, monkeypatch, kind):
    client, _, _ = report_client
    token = preview_token(client)
    if kind == "tampered":
        token += "x"
    elif kind == "other_account":
        token = portal.signer.dumps({"actor": 8})
    else:
        monkeypatch.setattr(portal.signer, "loads", lambda *args, **kwargs: (_ for _ in ()).throw(portal.BadSignature("Expired")))
    monkeypatch.setattr(portal.reports, "submit_report", lambda *args: pytest.fail("Invalid token reached submission"))
    response = client.post("/portal/team-leader/reports", data={"preview_token": token}, follow_redirects=False)
    assert response.status_code == 303 and "error=" in response.headers["location"]


def test_stale_submission_preserves_all_notes_and_refreshes_preview(report_client, monkeypatch):
    client, _, session = report_client
    token = preview_token(client)

    def stale(*args):
        raise portal.reports.StalePreview("Records changed. Review the refreshed report.")

    monkeypatch.setattr(portal.reports, "submit_report", stale)
    response = client.post("/portal/team-leader/reports", data={"preview_token": token,
        "notes": "Period narrative", "notes_returns": "Pending custody verification"})
    assert response.status_code == 200
    assert "Period narrative" in response.text and "Pending custody verification" in response.text
    assert session["activity_report_draft"]["notes"] == "Period narrative"


def test_download_stale_preview_returns_conflict(report_client, monkeypatch):
    client, payload, _ = report_client
    token = preview_token(client)
    monkeypatch.setattr(portal.reports, "build_report", lambda *args: {**payload, "author": {"account_id": 7, "name": "Changed"}})
    response = client.post("/portal/team-leader/reports/preview/xlsx", data={"preview_token": token})
    assert response.status_code == 409 and "Records changed" in response.text


def test_submission_uses_signed_period_and_ignores_client_totals(report_client, monkeypatch):
    client, payload, _ = report_client
    token = preview_token(client)
    calls = []
    monkeypatch.setattr(portal.reports, "submit_report", lambda *args: calls.append(args) or 42)
    response = client.post("/portal/team-leader/reports", data={"preview_token": token,
        "total_sales": "999999", "period_start": "2000-01-01", "notes": "Reviewed"}, follow_redirects=False)
    assert response.headers["location"] == "/portal/team-leader/reports/42"
    assert calls[0][0] == 7 and calls[0][1].isoformat() == payload["period_start"]
    assert calls[0][3] == portal.reports.fingerprint(payload)
    assert UUID(calls[0][4]).version == 4
