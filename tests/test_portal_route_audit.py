"""Render all role navigation and key detail pages against an isolated database."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, portal_config, web_sessions
from app.repositories import product_returns as returns
from app.business_time import business_now
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case  # noqa: F401

pytestmark = pytest.mark.postgres


def test_role_navigation_and_details_render_without_server_errors(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case_id = create_case(d, "replace")
    returns.review_product_return(case_id, d["sales"], True, "Approved audit fixture replacement")
    current = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(current))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    failures = []
    for key, role, leader in [("owner", "owner", None), ("supervisor", "sales-supervisor", "sales-supervisor"),
                              ("sales", "team-leader", "sales"), ("inventory", "team-leader", "inventory"),
                              ("reseller", "reseller", None)]:
        current.clear()
        current.update(account_id=d[key], role_key=role, team_leader_role=leader,
                       account_name="Audit account", account_email="audit@example.test",
                       portal_expires_at=int((business_now()+timedelta(hours=1)).timestamp()))
        paths = [f"/portal/{role}/{section}" for section, *_ in portal_config.portal_nav_for(role, leader)]
        paths += [f"/portal/{role}/notifications", f"/portal/{role}/returns?case={case_id}"]
        if role == "owner":
            paths += [f"/portal/owner/{section}" for section in ["operations", "materials", "batches", "movements", "alerts", "products", "expired-stock"]]
        if key in {"owner", "inventory"}:
            paths += [f"/portal/{role}/product-details?product_id={d['product']}"]
        if key == "reseller":
            paths += ["/portal/reseller/history?view=returns", f"/portal/reseller/history?view=returns&case={case_id}", "/portal/reseller/checkout"]
        with TestClient(main.app) as client:
            for path in paths:
                try:
                    response = client.get(path, follow_redirects=False)
                    if response.status_code not in {200, 303, 307}:
                        failures.append(f"{key}: {path} returned {response.status_code}")
                except Exception as exc:
                    failures.append(f"{key}: {path}: {type(exc).__name__}: {exc}")
    assert not failures, "\n".join(failures)
