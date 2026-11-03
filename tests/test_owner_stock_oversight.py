from datetime import timedelta
from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main, portal_config, repositories, security_controls
from app.business_time import business_today
from app.capabilities import has_capability
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns, superuser
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import create_case, lifecycle  # noqa: F401
from test_role_dashboards import session_for


def test_owner_inventory_has_two_main_tabs_and_read_only_capabilities():
    request = Request({"type": "http", "session": {"role_key": "owner"}})
    for section in [
        "inventory",
        "products",
        "materials",
        "movements",
        "alerts",
        "batches",
        "expired-stock",
        "replacements",
        "product-details",
    ]:
        workspace = portal_config.owner_workspace_for(section)
        assert workspace["parent"] == "inventory"
        assert workspace["tabs"] == (("products", "Products & prices"), ("inventory", "Stock oversight"))
    assert has_capability(request, "inventory.prices")
    assert has_capability(request, "inventory.view_all")
    for capability in ["inventory.manage", "inventory.override", "returns.receive"]:
        assert not has_capability(request, capability)
    request.session.update(role_key="team-leader", team_leader_role="inventory")
    assert has_capability(request, "inventory.manage")
    assert has_capability(request, "returns.receive")


@pytest.mark.postgres
def test_owner_stock_pages_are_read_only_and_direct_writes_fail(request, monkeypatch):
    d = request.getfixturevalue("lifecycle")
    session, _ = session_for(monkeypatch, d, "owner")
    session["csrf_token"] = "stock-oversight-token"
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    before = fetch_one("SELECT sum(quantity_available) AS quantity FROM inventory_batches")
    with TestClient(main.app) as client:
        for section in ["inventory", "materials", "movements", "alerts", "batches", "expired-stock", "replacements", "products"]:
            page = client.get("/portal/owner/" + section)
            assert page.status_code == 200
            if section != "products":
                for action in [
                    "/inventory/production",
                    "/inventory/corrections",
                    '/receipt"',
                    "/expired-stock/actions/",
                    "/alerts/1/review",
                ]:
                    assert action not in page.text
        data = {
            "csrf_token": session["csrf_token"],
            "product_id": d["product"],
            "item_id": d["product"],
            "quantity": "1",
            "quantity_delta": "1",
            "batch_code": "FORBIDDEN",
            "expiry_date": str(business_today() + timedelta(days=10)),
            "reason": "Owner cannot edit stock",
            "status": "reviewed",
        }
        for path in [
            "/inventory/production",
            "/inventory/corrections",
            "/materials/1/receipt",
            "/alerts/1/review",
            "/alerts/1/status",
            "/expired-stock/actions/pullout",
            "/expired-stock/actions/dispose",
            "/returns/actions/receive",
            "/returns/actions/reserve",
            "/returns/actions/release",
        ]:
            response = client.post("/portal/owner" + path, data=data, follow_redirects=False)
            assert response.status_code == 403, path
        response = client.post(
            "/portal/owner/products",
            data={
                "csrf_token": session["csrf_token"],
                "product_id": d["product"],
                "base_price": "325",
                "reason": "Approved retail price update",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "Product pricing updated" in unquote_plus(response.headers["location"])
    assert repositories.product_by_id(d["product"])["base_price"] == 325
    assert fetch_one("SELECT sum(quantity_available) AS quantity FROM inventory_batches") == before


@pytest.mark.postgres
def test_owner_repository_stock_writes_roll_back_but_inventory_can_receive(request):
    d = request.getfixturevalue("lifecycle")
    material = repositories.add_raw_inventory_item(
        "Oversight material", "Meat", "kg", "2", actor_account_id=d["inventory"]
    )
    material_id = material["raw_material_id"]
    with pytest.raises(ValueError, match="read-only"):
        repositories.add_raw_inventory_quantity(material_id, "1", actor_account_id=d["owner"])
    with pytest.raises(ValueError, match="read-only"):
        superuser.compensate_inventory(material_id, "1", d["owner"], "Forbidden stock correction")
    with pytest.raises(ValueError, match="read-only"):
        repositories.add_product_batch(
            d["product"], "OWNER-FORBIDDEN", "1", business_today() + timedelta(days=10), actor_account_id=d["owner"]
        )
    with pytest.raises(ValueError, match="read-only"):
        product_returns.confirm_expired_pullout(
            d["batches"][0], d["owner"], "1", "Expired holding", "Forbidden owner pullout", "oversight-owner-pullout"
        )
    assert (
        fetch_one("SELECT quantity_available FROM inventory_items WHERE item_id=%s", (material_id,))[
            "quantity_available"
        ]
        == 2
    )
    repositories.add_raw_inventory_quantity(material_id, "1", actor_account_id=d["inventory"])
    assert (
        fetch_one("SELECT quantity_available FROM inventory_items WHERE item_id=%s", (material_id,))[
            "quantity_available"
        ]
        == 3
    )


@pytest.mark.postgres
def test_owner_replacement_oversight_filters_requests_and_keeps_converted_history(request, monkeypatch):
    d = request.getfixturevalue("lifecycle")
    replacement = create_case(d, "replace", 1)
    converted = create_case(d, "replace", 1)
    refund = create_case(d, "refund", 1)
    # Model a historical conversion without changing the operational workflow.
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE product_returns SET resolution='refund', refund_converted_at=now(), "
            "refund_converted_by_account_id=%s, refund_conversion_reason='Historical stock shortage' WHERE return_id=%s",
            (d["owner"], converted),
        )
    rows = product_returns.list_product_returns(d["owner"], replacement_requests_only=True)
    assert {row["return_id"] for row in rows} == {replacement, converted}
    assert all(row["list_total"] == 2 for row in rows)
    assert {row["return_id"] for row in product_returns.list_product_returns(d["owner"])} == {
        replacement, converted, refund
    }
    assert not product_returns.list_product_returns(
        d["owner"], "completed", replacement_requests_only=True
    )
    assert not product_returns.list_product_returns(
        d["owner"], search="absent reseller", replacement_requests_only=True
    )
    session_for(monkeypatch, d, "owner")
    with TestClient(main.app) as client:
        page = client.get("/portal/owner/replacements?page=99999")
        assert page.status_code == 200
        assert f"View replacement request {replacement}" in page.text
        assert f"View replacement request {converted}" in page.text
        assert f"View replacement request {refund}" not in page.text
        assert "Converted to refund" in page.text
        assert "Page 1 of 1" in page.text
        empty = client.get("/portal/owner/replacements?status=completed")
        assert empty.status_code == 200
        assert "No replacement requests match these filters." in empty.text


@pytest.mark.postgres
@pytest.mark.e2e
def test_owner_inventory_workspace_browser(request, monkeypatch):
    from pathlib import Path
    from urllib.parse import urlsplit
    from playwright.sync_api import expect, sync_playwright
    from app.repositories import stock_alerts

    d = request.getfixturevalue("lifecycle")
    session_for(monkeypatch, d, "owner")
    replacement_id = create_case(d, "replace", 1)
    stock_alerts.reconcile_stock_alerts()
    output = Path("outputs/owner-stock-oversight")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with TestClient(main.app, base_url="http://oversight.test") as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.emulate_media(reduced_motion="reduce")
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            url = urlsplit(route.request.url)
            response = client.get(url.path + ("?" + url.query if url.query else ""))
            route.fulfill(
                status=response.status_code,
                body=response.content,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
                },
            )

        page.route("http://oversight.test/**", bridge)
        for width in [1440, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1000})
            for section in ["inventory", "materials", "alerts", "movements", "products", "batches", "expired-stock", "replacements"]:
                response = page.goto(f"http://oversight.test/portal/owner/{section}", wait_until="networkidle")
                assert response.status == 200
                top = page.get_by_role("navigation", name="Workspace sections", exact=True)
                expect(top.locator("a")).to_have_count(2)
                expect(top.locator('a[aria-current="page"]')).to_contain_text(
                    "Products & prices" if section == "products" else "Stock oversight"
                )
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (section, width)
                if section != "products":
                    expect(page.get_by_role("navigation", name="Stock oversight views")).to_be_visible()
                    assert (
                        page.locator(
                            'form[action*="/inventory/production"],form[action*="/inventory/corrections"],form[action$="/receipt"],form[action*="/expired-stock/actions/"],form[action*="/alerts/"]'
                        ).count()
                        == 0
                    )
                else:
                    assert page.locator('form[action="/portal/owner/products"]').count() > 0
                if section in {"expired-stock", "replacements"}:
                    subtabs = page.get_by_role("navigation", name="Stock oversight views")
                    expect(subtabs.locator('a[aria-current="page"]')).to_have_text(
                        "Expired" if section == "expired-stock" else "Replacement"
                    )
                if section == "replacements":
                    expect(page.get_by_role("link", name=f"View replacement request {replacement_id}", exact=True)).to_be_visible()
                if section in ["inventory", "materials", "products", "alerts", "expired-stock", "replacements"]:
                    page.screenshot(path=str(output / f"{section}-{width}.png"), full_page=True)
            page.goto("http://oversight.test/portal/owner/inventory", wait_until="networkidle")
            page.get_by_role("navigation", name="Workspace sections", exact=True).get_by_role(
                "link", name="Products & prices", exact=True
            ).focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("heading", name="Product and price management")).to_be_visible()
        assert not errors
        browser.close()
