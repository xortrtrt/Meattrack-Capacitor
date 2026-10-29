"""Expired-stock pull-outs assign their destination without a location field."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, security_controls, web_sessions, worker
from app.business_time import business_now
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("supplied_location", [None, "User-supplied location"])
def test_pullout_form_uses_automatic_destination(lifecycle, monkeypatch, supplied_location):  # noqa: F811
    d = lifecycle
    worker.scan_batch_expiry()
    session = dict(account_id=d["inventory"], role_key="team-leader", team_leader_role="inventory",
                   portal_expires_at=int(business_now().timestamp()) + 600, csrf_token="pullout-token")
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    with TestClient(main.app, headers={"Origin": "http://testserver"}) as client:
        page = client.get("/portal/team-leader/expired-stock")
        assert page.status_code == 200
        assert 'name="location"' not in page.text
        assert 'name="quantity"' in page.text and 'name="remarks"' in page.text
        payload = dict(batch_id=d["batches"][0], quantity=4, remarks="Expired goods physically segregated",
                       operation_key=str(uuid4()), csrf_token=session["csrf_token"])
        if supplied_location is not None:
            payload["location"] = supplied_location
        for _ in range(2):
            response = client.post("/portal/team-leader/expired-stock/actions/pullout", data=payload, follow_redirects=False)
            assert response.status_code == 303 and "error=" not in response.headers["location"]
    holding = fetch_one("SELECT location,quantity_remaining FROM expired_holdings WHERE source='pullout'")
    assert holding["location"] == "Expired holding" and holding["quantity_remaining"] == 4
    assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))["quantity_available"] == 6
    assert fetch_one("SELECT COUNT(*) n FROM expired_stock_movements WHERE action='pullout'")["n"] == 1
