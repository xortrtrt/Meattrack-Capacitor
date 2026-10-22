from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main, portal_config, repositories
from app.capabilities import OWNER_SECTION_CAPABILITIES
from app.portals import owner, shared
from app.repositories import accounts


def make_request(query="", path="/portal/owner/resellers"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "server": ("testserver", 80), "path": path, "query_string": query.encode(), "headers": [], "session": {}})


def test_system_page_and_retry_route_removed(monkeypatch):
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    assert "system" not in {item[0] for item in portal_config.portal_nav["owner"]}
    assert "system" not in OWNER_SECTION_CAPABILITIES
    assert ("owner", "system") not in owner.SECTION_LOADERS
    client = TestClient(main.app)
    assert client.get("/portal/owner/system").status_code == 404
    assert client.post("/portal/owner/system/outbox/1/retry", data={"reason": "Retry failure"}).status_code == 404
    assert callable(repositories.report_system_status)


@pytest.mark.parametrize("section", ["audit", "security"])
def test_removed_owner_pages_are_unavailable(monkeypatch, section):
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    assert section not in {item[0] for item in portal_config.portal_nav["owner"]}
    assert section not in OWNER_SECTION_CAPABILITIES
    assert ("owner", section) not in owner.SECTION_LOADERS
    client = TestClient(main.app)
    assert client.get(f"/portal/owner/{section}").status_code == 404
    if section == "security":
        for action in ("request", "confirm"):
            assert client.post(f"/portal/owner/security/step-up/{action}", data={"otp_code": "123456"}).status_code == 404


def test_owner_verification_does_not_load_security_logs(monkeypatch):
    monkeypatch.setattr(owner, "owner_step_up_active", lambda request: False)
    def unexpected_read(*args, **kwargs):
        raise AssertionError("Verification must not load monitoring data")
    for name in ("list_active_sessions", "list_security_events", "list_activity_logs", "count_activity_logs"):
        monkeypatch.setattr(repositories, name, unexpected_read)
    assert owner.verification_context(make_request("challenge=1")) == {
        "owner_step_up_active": False, "owner_step_up_pending": True,
    }


def test_audit_export_is_unavailable():
    with pytest.raises(ValueError, match="not available"):
        repositories.owner_export_rows("audit")


@pytest.mark.parametrize("query,status,leader,sort,page", [
    ("q=shop&status=active&leader=4&sort=newest&page=2", "active", "4", "newest", 2),
    ("leader=unassigned&page=99", "", "unassigned", "business_asc", 3),
    ("status=invalid&leader=bad&sort=DROP&page=bad", "", "", "business_asc", 1),
])
def test_reseller_filters_and_pagination(monkeypatch, query, status, leader, sort, page):
    calls = []
    monkeypatch.setattr(repositories, "count_owner_resellers", lambda **kw: calls.append(("count", kw)) or 25)
    monkeypatch.setattr(repositories, "list_owner_resellers", lambda **kw: calls.append(("list", kw)) or [])
    monkeypatch.setattr(repositories, "list_team_leader_accounts", lambda: [])
    result = owner.resellers_context(make_request(query))
    criteria = calls[0][1]
    assert criteria["status"] == status
    assert criteria["leader"] == leader
    assert calls[1][1] == {**criteria, "sort": sort, "page": page, "page_size": 10}
    assert result["resellers_page"]["pagination"]["page"] == page


@pytest.mark.parametrize("leader", ["", "4", "unassigned"])
def test_reseller_repository_list_count_share_predicates_and_safe_sort(monkeypatch, leader):
    captured = []
    monkeypatch.setattr(accounts, "fetch_all", lambda sql, params: captured.append((sql, params)) or [])
    monkeypatch.setattr(accounts, "fetch_one", lambda sql, params: captured.append((sql, params)) or {"total": 0})
    accounts.list_owner_resellers(q="shop", status="active", leader=leader, sort="DROP TABLE resellers", page=2)
    accounts.count_owner_resellers(q="shop", status="active", leader=leader)
    list_sql, list_params = captured[0]
    count_sql, count_params = captured[1]
    assert list_sql.split(" WHERE ")[1].split(" ORDER BY ")[0] == count_sql.split(" WHERE ")[1].rstrip(";")
    assert list_params[:-2] == count_params
    assert list_params[-2:] == (10, 10)
    assert "DROP TABLE" not in list_sql
    assert "lower(r.business_name) ASC" in list_sql
    if leader == "unassigned":
        assert "r.team_leader_account_id IS NULL" in list_sql


def test_pagination_links_retain_all_filters():
    request = make_request("q=shop&status=active&leader=unassigned&sort=newest&page=2")
    template = shared.templates().get_template("portals/owner/_directory_pagination.html")
    rendered = template.render(request=request, section_title="Resellers", directory_page=shared.paged([], 25, 2, 10))
    from html import unescape
    from re import findall
    links = findall('href="([^"]+)"', rendered)
    assert len(links) == 2
    for href in links:
        params = parse_qs(urlparse(unescape(href)).query)
        assert params["q"] == ["shop"]
        assert params["leader"] == ["unassigned"]
        assert params["status"] == ["active"]
        assert params["sort"] == ["newest"]


def test_report_modal_shows_full_product_details_and_missing_notes(monkeypatch):
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kw: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kw: 0)
    monkeypatch.setitem(shared.PORTAL_SECTION_LOADERS, ("owner", "reports"), lambda request: {
        "sales_reports_page": shared.paged([], 1, 1, 10, q="", sort=""),
        "sales_reports": [{"sales_report_id": 7, "report_source": "team_leader", "submitted_by": "Alex", "period_start": date(2026, 10, 1), "period_end": date(2026, 10, 19), "total_sales": 129.75, "total_orders": 1, "notes": "", "items": [{"name": "Bacon", "quantity_sold": 1, "unit": "pack", "unit_price": 129.75, "line_total": 129.75}]}],
    })
    response = TestClient(main.app).get("/portal/owner/reports")
    assert response.status_code == 200
    assert "No notes were provided." in response.text
    assert "Bacon" in response.text
    assert "129.75" in response.text
    assert 'data-owner-modal-open="owner-report-7"' in response.text


@pytest.mark.parametrize("activation,account_type,role_action,security_action,activation_action,transfer_action", [
    ("active", "owner", False, True, False, False),
    ("pending", "owner", False, False, True, False),
    ("active", "team_leader", True, True, False, True),
    ("active", "reseller", False, True, False, False),
])
def test_account_modal_preserves_action_eligibility(activation, account_type, role_action, security_action, activation_action, transfer_action):
    request = make_request(path="/portal/owner/accounts")
    template = shared.templates().get_template("portals/owner/users_access.html")
    rendered = template.render(
        request=request, role_key="owner", section_title="Users & access", can_create_owner_account=True,
        can_manage_team_leader_roles=True, team_leaders=[], accounts_page={**shared.paged([], 1, 1, 10, q="", type="", sort=""), "type_options": []},
        accounts=[{"account_id": 9, "name": "Alex", "email": "alex@example.test", "status": "active", "account_type": account_type, "team_leader_role": "sales", "activation_status": activation}],
    )
    assert ('action="/portal/owner/accounts/9/role"' in rendered) is role_action
    assert ('action="/portal/owner/accounts/9/password-reset"' in rendered) is security_action
    assert ('action="/portal/owner/accounts/9/sessions/revoke"' in rendered) is security_action
    assert ('action="/portal/owner/accounts/9/status"' in rendered) is security_action
    assert ('action="/portal/owner/accounts/9/activation"' in rendered) is activation_action
    assert ('action="/portal/owner/accounts/9/transfer"' in rendered) is transfer_action
    assert "Reseller team assignments" not in rendered


def test_owner_verification_renders_without_removed_pages(monkeypatch):
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args: 0)
    response = TestClient(main.app).get("/portal/owner/verify?challenge=1")
    assert response.status_code == 200
    assert '/portal/owner/verify/step-up/request' in response.text
    assert '/portal/owner/verify/step-up/confirm' in response.text
    assert '/portal/owner/security' not in response.text
    assert '/portal/owner/audit' not in response.text
    assert 'Active sessions' not in response.text
    assert 'Authentication events' not in response.text
