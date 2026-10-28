"""Inventory sees stock custody; Sales sees the linked financial settlement."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions
from app.business_time import business_now
from app.database import fetch_one
from app.repositories import product_returns as returns
from app.portals import product_returns as portal
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, receive, refund_proof, complete_refund  # noqa: F401
from test_order_financial_alignment import verified, replacement

pytestmark = pytest.mark.postgres


def sign_in(monkeypatch, d, role):
    session = {
        "account_id": d[role], "role_key": "team-leader", "team_leader_role": role,
        "portal_expires_at": int(business_now().timestamp()) + 600,
    }
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)


def assert_no_money(value):
    forbidden = {
        "credit", "replacement_total", "payment_amount", "payment_reference", "unit_price", "line_total",
        "refund", "refund_status", "order_refund_id", "order_total", "additional_due", "unused_credit", "amount",
    }
    if isinstance(value, dict):
        assert not forbidden.intersection(value)
        for item in value.values():
            assert_no_money(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_money(item)


def test_inventory_reads_stock_only_while_sales_retains_same_case_financials(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = verified(d)
    inventory = returns.get_product_return(case, d["inventory"])
    sales = returns.get_product_return(case, d["sales"])
    assert_no_money(inventory)
    assert_no_money(returns.list_product_returns(d["inventory"]))
    assert inventory["return_id"] == sales["return_id"] == case
    assert inventory["order_id"] == sales["order_id"] == d["order"]
    assert inventory["inventory_status"] == "completed"
    assert sales["status"] == "settlement_pending" and sales["credit"] == sales["refund"]["amount"] == 200
    assert inventory["lines"][0]["received_quantity"] == sales["lines"][0]["received_quantity"] == 2
    assert len(returns.list_product_returns(d["inventory"], "completed")) == 1
    assert not returns.list_product_returns(d["inventory"], "awaiting_receipt")
    with pytest.raises(ValueError, match="cannot perform"):
        returns.return_settlement_totals(d["inventory"])

    actual_summary = returns.return_settlement_totals

    def financial_summary(actor):
        assert actor != d["inventory"], "Inventory must not load financial summaries"
        return actual_summary(actor)

    monkeypatch.setattr(returns, "return_settlement_totals", financial_summary)
    for role in ("inventory", "sales"):
        sign_in(monkeypatch, d, role)
        with TestClient(main.app) as client:
            page = client.get(f"/portal/team-leader/returns?case={case}")
            assert page.status_code == 200
            if role == "inventory":
                for label in ("PHP", "Original sales", "Net collections", "Refund #", "Original purchase credit"):
                    assert label not in page.text
                assert "Batch and custody records" in page.text and "Stock history" in page.text
            else:
                assert "Original sales" in page.text and "Refund #" in page.text and "PHP 200.00" in page.text


def test_inventory_replacement_release_uses_sales_clearance_without_payment_data(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = replacement(d, 250)
    returns.record_return_payment(case, d["reseller"], [refund_proof()])
    pending = returns.get_product_return(case, d["inventory"])
    assert pending["inventory_can_reserve"] and not pending["inventory_can_release"]
    assert_no_money(pending)
    proof = fetch_one("SELECT attachment_id FROM return_attachments WHERE return_id=%s AND purpose='payment'", (case,))
    sign_in(monkeypatch, d, "inventory")
    with TestClient(main.app) as client:
        assert client.get(f"/portal/return-attachments/{proof['attachment_id']}").status_code == 404
        response = client.post("/portal/team-leader/returns/actions/release", data={"return_id": case, "remarks": "Attempt before Sales clearance"})
        assert response.status_code == 200 and "Sales clearance is required" in response.text
        assert "additional payment" not in response.text.lower() and "PHP" not in response.text
        for path in ("refunds", "reports", "orders"):
            assert client.get(f"/portal/team-leader/{path}").status_code in {403, 404}
    sign_in(monkeypatch, d, "sales")
    with TestClient(main.app) as client:
        assert client.get(f"/portal/return-attachments/{proof['attachment_id']}").status_code == 200
    returns.verify_return_payment(case, d["sales"], "TOPUP" + uuid4().hex, 50, "Confirmed exact replacement collection")
    ready = returns.get_product_return(case, d["inventory"])
    assert ready["inventory_can_release"] and ready["inventory_status"] == "awaiting_replacement_release"
    assert_no_money(ready)
    returns.release_replacement(case, d["inventory"], "Released the cleared replacement stock")
    assert returns.get_product_return(case, d["inventory"])["inventory_status"] == "completed"
    assert returns.return_settlement_totals(d["sales"])["additional_collections"] == 50


def test_inventory_completion_stays_physical_while_sales_tracks_outstanding_difference(lifecycle):  # noqa: F811
    d = lifecycle
    case = replacement(d, 150)
    returns.release_replacement(case, d["inventory"], "Released cheaper replacement stock")
    stock = returns.get_product_return(case, d["inventory"])
    sales = returns.get_product_return(case, d["sales"])
    assert stock["inventory_status"] == "completed" and sales["status"] == "settlement_pending"
    assert sales["refund"]["amount"] == 50 and sales["refund"]["status"] == "pending"
    before = json.dumps(stock, default=str, sort_keys=True)
    complete_refund(d, case)
    after = returns.get_product_return(case, d["inventory"])
    assert_no_money(after)
    assert after["inventory_status"] == "completed"
    assert stock["lines"] == after["lines"] and stock["receipt_lines"] == after["receipt_lines"]
    assert "refund_completed" not in json.dumps(after, default=str) and "refund_pending" not in before
    assert returns.get_product_return(case, d["sales"])["status"] == "completed"


def test_inventory_notifications_and_catalog_do_not_expose_financial_data(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = verified(d)
    complete_refund(d, case)
    # An older notification may still contain finance text. Its Inventory
    # presentation must be stock-only without deleting the original audit row.
    repositories.create_notification(
        recipient_account_id=d["inventory"], category="inventory", title="Refund PHP 200.00",
        message="Transfer BANKSECRET123 completed", target_url=f"/portal/team-leader/returns?case={case}",
    )
    sign_in(monkeypatch, d, "inventory")
    with TestClient(main.app) as client:
        for path in ("dashboard", "inventory", "finished-products", "notifications", f"product-details?product_id={d['product']}"):
            response = client.get("/portal/team-leader/" + path)
            assert response.status_code == 200, response.text[:500]
            assert "PHP" not in response.text and "BANKSECRET123" not in response.text
            assert 'name="base_price"' not in response.text
    notifications = repositories.list_notification_history(d["inventory"])
    assert any("BANKSECRET123" in row["message"] for row in notifications)  # Preserve the audit source.
    assert not any("refund" in row["title"].lower() for row in notifications if row["title"].startswith("Returned stock"))


def test_inventory_context_never_carries_hidden_financial_fields(lifecycle):  # noqa: F811
    from starlette.requests import Request
    from app.portals import shared
    d = lifecycle
    case = verified(d)
    scope = {
        "type": "http", "method": "GET", "path": "/portal/team-leader/returns", "query_string": f"case={case}".encode(),
        "headers": [], "server": ("testserver", 80), "scheme": "http",
        "session": {"account_id": d["inventory"], "role_key": "team-leader", "team_leader_role": "inventory"},
    }
    context = portal.returns_context(Request(scope))
    assert context["return_summary"] == {} and context["replacement_products"] == []
    assert context["return_inventory_summary"]["received_packs"] == 2
    assert_no_money(context["return_case"])
    assert_no_money(context["return_cases"])
    catalog = shared.portal_section_context("team-leader", "finished-products", Request(scope))
    assert all("base_price" not in row for row in catalog["finished_products"])


@pytest.mark.e2e
def test_inventory_and_sales_render_separate_returns_in_browser(lifecycle, monkeypatch):  # noqa: F811
    from playwright.sync_api import sync_playwright, expect
    d = lifecycle
    case = verified(d)
    output = Path("outputs/inventory-sales-separation")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://separation.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def handle(route):
            parts = urlsplit(route.request.url)
            response = client.get(parts.path + ("?" + parts.query if parts.query else ""))
            route.fulfill(status=response.status_code, headers={
                key: value for key, value in response.headers.items()
                if key not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
            }, body=response.content)

        page.route("http://separation.test/**", handle)
        for role in ("inventory", "sales"):
            sign_in(monkeypatch, d, role)
            for width in (375, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"http://separation.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
                dialog = page.get_by_role("dialog", name=f"Return #{case}", exact=True)
                expect(dialog).to_be_visible()
                expect(page.get_by_role("button", name="Close return details", exact=True)).to_be_focused()
                if role == "inventory":
                    expect(dialog).to_contain_text("Batch and custody records")
                    assert "PHP" not in page.locator("body").inner_text()
                    assert "refund" not in dialog.inner_text().lower()
                else:
                    expect(dialog).to_contain_text("PHP 200.00")
                    expect(dialog).to_contain_text("Refund #")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
                page.screenshot(path=str(output / f"{role}-{width}.png"), full_page=True)
                page.keyboard.press("Escape")
                expect(dialog).not_to_be_visible()
        assert not errors, errors
        browser.close()
