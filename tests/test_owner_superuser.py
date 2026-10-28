from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main, portal_config, repositories
from app.capabilities import (
    OWNER_SECTION_CAPABILITIES,
    PORTAL_SECTION_CAPABILITIES,
    has_capability,
)
from app.portals import owner as owner_portal
from app.portals import shared as shared_portal
from app.repositories import superuser


def request_for(role_key: str, team_leader_role: str = "sales"):
    return SimpleNamespace(session={"role_key": role_key, "team_leader_role": team_leader_role})


def test_active_owners_have_every_capability_and_other_roles_do_not_gain_overrides():
    owner = request_for("owner")
    sales_leader = request_for("team-leader", "sales")

    assert has_capability(owner, "accounts.manage_all")
    assert has_capability(owner, "inventory.override")
    assert has_capability(owner, "system.jobs.manage")
    assert not has_capability(sales_leader, "accounts.manage_all")
    assert not has_capability(sales_leader, "inventory.override")


def test_every_owner_navigation_section_declares_a_capability():
    owner_sections = {slug for slug, _label, _icon in portal_config.portal_nav["owner"]}
    owner_sections.update(portal_config.portal_secondary_sections["owner"])

    assert owner_sections == set(OWNER_SECTION_CAPABILITIES)


def test_every_non_owner_navigation_section_declares_a_capability():
    expected = {
        "sales-supervisor": portal_config.team_leader_nav_by_role["sales-supervisor"],
        "team-leader:sales": portal_config.team_leader_nav_by_role["sales"],
        "team-leader:inventory": portal_config.team_leader_nav_by_role["inventory"],
        "reseller": portal_config.portal_nav["reseller"],
    }
    for role, navigation in expected.items():
        declared = set(PORTAL_SECTION_CAPABILITIES[role])
        visible = {slug for slug, _label, _icon in navigation}
        visible.add("notifications")
        if role == "reseller":
            # The legacy returns destination redirects into the Order History tab.
            visible.update({"checkout", "returns"})
        if role == "team-leader:inventory":
            visible.add("product-details")
        assert visible == declared


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"order.payment_window_minutes": 0}, "Payment window"),
        ({"inventory.near_expiry_days": 91}, "Expiry windows"),
        ({"forecast.default_horizon_days": 366}, "Forecast horizon"),
        ({"business.hours": "x" * 501}, "cannot exceed"),
    ],
)
def test_business_settings_reject_invalid_values_before_database_access(values, message):
    with pytest.raises(ValueError, match=message):
        superuser.update_business_settings(values, 1, "Owner governance update")


def test_owner_step_up_window_expires_server_side(monkeypatch):
    now = owner_portal.business_now()
    active = SimpleNamespace(session={"owner_step_up_until": int(now.timestamp()) + 30})
    expired = SimpleNamespace(session={"owner_step_up_until": int(now.timestamp()) - 1})

    assert owner_portal.owner_step_up_active(active)
    assert not owner_portal.owner_step_up_active(expired)


def test_csv_cells_are_guarded_against_spreadsheet_formula_execution():
    assert owner_portal._csv_cell("=HYPERLINK('bad')").startswith("'")
    assert owner_portal._csv_cell({"safe": True}) == '{"safe": true}'


@pytest.mark.parametrize("action", ["transcript", "admin"])
def test_removed_owner_conversation_actions_are_unavailable(action):
    response = TestClient(main.app).post(
        f"/portal/owner/conversations/00000000-0000-0000-0000-000000000001/{action}",
        data={"reason": "Review customer request", "action": "close"},
    )
    assert response.status_code == 404


def test_removed_owner_conversations_page_is_unavailable(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    response = TestClient(main.app).get("/portal/owner/conversations")
    assert response.status_code == 404


def test_owner_migration_contains_immutable_correction_and_retry_guards():
    migration = Path("database/migrations/027_owner_superuser.sql").read_text(encoding="utf-8")

    assert "owner_correction" in migration
    assert "ux_forecast_runs_single_retry" in migration
    assert "ADD COLUMN IF NOT EXISTS" in migration
    assert "owner_step_up_challenges" in migration


@pytest.mark.parametrize(
    ("section", "context"),
    [
        ("operations", {"operations_summary": {}, "recent_orders": [], "recent_inquiries": [], "alerts": [], "team_workload": []}),
        ("inquiries", {"inquiries_page": {"filters": {"q": "", "status": ""}}, "inquiries": []}),
        ("orders", {"orders_page": {"filters": {"q": "", "status": ""}}, "orders": [{"order_id": 1, "reseller": "Test reseller", "status": "pending", "total_amount": 250, "items": [{"name": "Longganisa", "quantity": 2}], "payment_proofs": []}]}),
        ("inventory", {"products": [], "raw_materials": [], "product_recipes": [], "alerts": []}),
        ("materials", {"materials_page": {"filters": {"q": ""}}, "materials": []}),
        ("batches", {"batches_page": {"filters": {}}, "batches": [], "finished_products": []}),
        ("movements", {"movements_page": {"filters": {}}, "movements": [], "inventory_items": [], "inventory_batches": []}),
        ("alerts", {"alerts": []}),
        ("resellers", {"resellers_page": {"pagination": {"total": 1}, "filters": {}}, "reseller_assignments": [{"reseller_id": 2, "business_name": "Example store", "contact_person": "Alex", "email": "alex@example.test", "reseller_status": "active", "team_leader_name": "Sales Leader", "team_leader_account_id": 4}], "team_leaders": [{"account_id": 4, "name": "Sales Leader"}]}),
        ("settings", {"business_settings": dict(superuser.SETTING_DEFAULTS)}),
    ],
)
def test_owner_superuser_sections_render(monkeypatch, section, context):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setitem(shared_portal.PORTAL_SECTION_LOADERS, ("owner", section), lambda request: context)

    response = TestClient(main.app).get(f"/portal/owner/{section}")

    assert response.status_code == 200
    assert ("owner-orders-page" if section == "orders" else "owner-superuser-page") in response.text
    if section == "operations":
        assert '/portal/owner/audit' not in response.text
        assert 'Recent high-risk owner actions' not in response.text
    if section == "resellers":
        assert "owner-resellers-hero" in response.text
        assert "owner-reseller-status-rail is-active" in response.text
        assert "Example store" in response.text
        assert '/portal/owner/resellers/2/team-leader' in response.text
        assert 'name="reason"' in response.text
        assert 'name="return_section" value="resellers"' in response.text
    if section == "orders":
        assert '/portal/owner/orders/1/approve' in response.text
        assert 'name="reason"' in response.text
        assert 'data-order-action-form' not in response.text


@pytest.mark.postgres
@pytest.mark.parametrize("section", [slug for slug, _label, _icon in portal_config.portal_nav["owner"]])
def test_owner_pages_with_real_database(monkeypatch, section):
    """Exercise real queries and populated templates without changing business records."""
    from app import web_sessions
    from app.business_time import business_now
    from app.database import fetch_one

    account = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'owner' AND is_active LIMIT 1")
    if not account:
        pytest.skip("An active owner is required for the portal smoke test")
    session = {
        "role_key": "owner", "account_id": account["account_id"],
        "portal_expires_at": int(business_now().timestamp()) + 600,
    }
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    with TestClient(main.app) as client:
        response = client.get(f"/portal/owner/{section}", follow_redirects=False)
    assert response.status_code == 200, response.text[:500]


@pytest.mark.postgres
@pytest.mark.parametrize("resource", ["accounts", "resellers", "orders", "refunds", "movements", "reports", "forecasts"])
def test_owner_export_queries_with_real_database(resource):
    assert isinstance(superuser.owner_export_rows(resource), list)


@pytest.mark.postgres
def test_conversation_filters_and_pagination_match_database():
    from app.database import fetch_one

    expected = fetch_one("SELECT COUNT(*) AS total FROM chat_conversations WHERE status = 'closed'")
    assert superuser.count_conversation_metadata(status="closed") == expected["total"]
    first = superuser.list_conversation_metadata(limit=2, status="closed", page=1)
    second = superuser.list_conversation_metadata(limit=2, status="closed", page=2)
    assert all(row["status"] == "closed" for row in first + second)
    assert not ({row["conversation_id"] for row in first} & {row["conversation_id"] for row in second})
    assert superuser.count_conversation_metadata(q="' OR TRUE --") == 0
    assert superuser.list_conversation_metadata(q="' OR TRUE --") == []
