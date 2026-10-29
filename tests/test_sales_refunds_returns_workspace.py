"""Sales reviews returns and follows money refunds from one scoped workspace."""

from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, security_controls, web_sessions
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from app.portals.shared import notification_target_for
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, receive, complete_refund  # noqa: F401
from test_order_financial_alignment import paid_pending_order, settle, verified
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("resolution", ["refund", "replace"])
def test_return_approval_posts_rendered_csrf_and_refresh_recovers(lifecycle, monkeypatch, resolution):  # noqa: F811
    import re
    d = lifecycle
    case = create_case(d, resolution)
    session = {
        "account_id": d["sales"], "role_key": "team-leader", "team_leader_role": "sales",
        "portal_expires_at": int(business_now().timestamp()) + 600,
        "csrf_token": "return-approval-session-token",
    }
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    with TestClient(main.app) as client:
        url = f"/portal/team-leader/refunds?view=returns&case={case}"
        page = client.get(url)
        assert page.status_code == 200
        forms = re.findall(r'<form[^>]*action="(/portal/team-leader/refunds/returns/actions/[^\"]+)"[^>]*>(.*?)</form>', page.text, re.S)
        assert forms
        for action, markup in forms:
            assert f'name="csrf_token" value="{session["csrf_token"]}"' in markup
        approve_url = next(action for action, markup in forms if action.endswith("/approve"))
        data = {"return_id": str(case), "remarks": "Approved the requested products after evidence review", "operation_key": str(uuid4())}
        for invalid in ({}, {"csrf_token": "wrong-token"}):
            denied = client.post(approve_url, data={**data, **invalid}, headers={"Origin": "http://testserver"})
            assert denied.status_code == 403
            assert returns.get_product_return(case, d["sales"])["status"] == "submitted"
        recovered = client.get(approve_url)
        assert recovered.status_code == 200
        assert urlsplit(str(recovered.url)).path == "/portal/team-leader/refunds"
        assert parse_qs(urlsplit(str(recovered.url)).query)["view"] == ["returns"]
        assert returns.get_product_return(case, d["sales"])["status"] == "submitted"
        reopened = client.get(approve_url + f"?case={case}")
        assert reopened.status_code == 200
        assert parse_qs(urlsplit(str(reopened.url)).query)["case"] == [str(case)]
        approved = client.post(approve_url, data={**data, "csrf_token": session["csrf_token"]}, headers={"Origin": "http://testserver"}, follow_redirects=False)
        assert approved.status_code == 303
        assert returns.get_product_return(case, d["sales"])["status"] == "awaiting_receipt"
        refreshed = client.get(approved.headers["location"])
        assert refreshed.status_code == 200
        assert parse_qs(urlsplit(str(refreshed.url)).query)["case"] == [str(case)]
        assert client.get(str(refreshed.url)).status_code == 200
        session.clear()
        signed_out = client.get(approve_url, follow_redirects=False)
        assert signed_out.status_code == 303
        assert urlsplit(signed_out.headers["location"]).path == "/login"


def test_sales_legacy_links_notifications_and_return_actions_use_refunds(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = create_case(d)
    sign_in(monkeypatch, d["sales"], "team-leader", "sales")
    with TestClient(main.app) as client:
        legacy = client.get(f"/portal/team-leader/returns?case={case}&status=submitted", follow_redirects=False)
        target = urlsplit(legacy.headers["location"])
        assert legacy.status_code == 303 and target.path == "/portal/team-leader/refunds"
        assert parse_qs(target.query) == {"view": ["returns"], "case": [str(case)], "status": ["submitted"]}
        page = client.get(legacy.headers["location"])
        assert page.status_code == 200
        nav = page.text.split('id="portal-nav"', 1)[1].split("</nav>", 1)[0]
        assert "Refunds &amp; returns" in nav and '/portal/team-leader/returns' not in nav
        assert 'aria-label="Sales return action queues"' in page.text
        assert "js/product_returns.js" in page.text
        assert 'action="/portal/team-leader/refunds/returns/actions/approve"' in page.text
        approved = client.post("/portal/team-leader/refunds/returns/actions/approve", data={
            "return_id": case, "remarks": "Approved original delivered products after review",
            "operation_key": str(uuid4()),
        }, follow_redirects=False)
        assert approved.status_code == 303
        assert urlsplit(approved.headers["location"]).path == "/portal/team-leader/refunds"
        assert parse_qs(urlsplit(approved.headers["location"]).query)["view"] == ["returns"]
        assert returns.get_product_return(case, d["sales"])["status"] == "awaiting_receipt"
        blocked = client.post("/portal/team-leader/refunds/returns/actions/proceed", data={
            "return_id": case, "remarks": "Must wait for Inventory verification", "operation_key": str(uuid4()),
        }, follow_redirects=False)
        assert "error" in parse_qs(urlsplit(blocked.headers["location"]).query)
        receive(d, case, authorize=False)
        confirmed = client.post("/portal/team-leader/refunds/returns/actions/proceed", data={
            "return_id": case, "remarks": "Confirmed the complete verified return for settlement",
            "operation_key": str(uuid4()),
        })
        assert confirmed.status_code == 200
        assert returns.get_product_return(case, d["sales"])["sales_authorized"]
        refund = fetch_one("SELECT order_refund_id FROM order_refunds WHERE return_id=%s", (case,))["order_refund_id"]
        money = client.get(f"/portal/team-leader/refunds?refund={refund}")
        assert money.status_code == 200 and "Receipt #" in money.text
        assert f'refunds?view=returns&case={case}' in unescape(money.text)
        assert client.post(f"/portal/team-leader/refunds/{refund}/start").status_code in {403, 404}
    notice = notification_target_for("team-leader", "sales", f"/portal/team-leader/returns?case={case}")
    assert urlsplit(notice).path == "/portal/team-leader/refunds"
    assert parse_qs(urlsplit(notice).query) == {"case": [str(case)], "view": ["returns"]}
    sign_in(monkeypatch, d["inventory"], "team-leader", "inventory")
    with TestClient(main.app) as client:
        assert client.get(f"/portal/team-leader/returns?case={case}").status_code == 200
        assert client.get("/portal/team-leader/refunds").status_code in {403, 404}
        assert client.post("/portal/team-leader/refunds/returns/actions/proceed", data={"return_id": case}).status_code in {403, 404}
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role)
            VALUES('team_leader','Other sales',%s,'hash','sales') RETURNING account_id""", (uuid4().hex + "@sales.test",))
        other = cur.fetchone()["account_id"]
    sign_in(monkeypatch, other, "team-leader", "sales")
    with TestClient(main.app) as client:
        assert client.get(f"/portal/team-leader/refunds?view=returns&case={case}").status_code == 404
        assert client.get(f"/portal/team-leader/refunds?refund={refund}").status_code == 404


@pytest.mark.e2e
def test_dashboard_separates_completed_pending_and_cancellation_refunds_in_browser(lifecycle, monkeypatch):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    cancellation = paid_pending_order(d)
    repositories.decide_order(cancellation, "reject", d["sales"], rejection_reason="Cancelled before dispatch", payment_received=True)
    cancellation_refund = fetch_one("SELECT order_refund_id FROM order_refunds WHERE order_id=%s", (cancellation,))["order_refund_id"]
    settle(d, cancellation_refund)
    case = verified(d)
    complete_refund(d, case)
    verified(d)  # Outstanding product refund must not reduce retained sales yet.
    sign_in(monkeypatch, d["sales"], "team-leader", "sales")
    output = Path("outputs/sales-refunds-returns")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://sales-workspace.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            parts = urlsplit(request.url)
            response = client.request(request.method, parts.path + ("?" + parts.query if parts.query else ""),
                content=request.post_data_buffer, headers={k: v for k, v in request.headers.items()
                    if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={k: v
                for k, v in response.headers.items() if k not in
                {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://sales-workspace.test/**", bridge)
        for width in (375, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto("http://sales-workspace.test/portal/team-leader/dashboard", wait_until="networkidle")
            financials = page.locator(".dashboard-financials")
            for label, amount in [("Original fulfilled sales", "PHP 1,000.00"), ("Money refunded", "PHP 800.00"), ("Retained sales", "PHP 800.00")]:
                expect(financials.locator(".dashboard-financial-totals > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")).to_have_text(amount)
            for label, amount in [("Cancelled orders", "PHP 600.00"), ("Returned products", "PHP 200.00"), ("Pending refund obligations", "PHP 200.00")]:
                expect(financials.locator(".dashboard-financial-breakdown dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")).to_have_text(amount)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"dashboard-{width}.png"), full_page=True)
            page.get_by_role("link", name="View refunds", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('.sales-resolution-tabs [aria-current="page"]')).to_have_text("Money refunds")
            expect(page.locator(".refund-hero")).to_have_count(0)
            expect(page.get_by_text("Open return review and replacements", exact=True)).to_have_count(0)
            page.get_by_role("link", name="Product returns", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('.sales-resolution-tabs [aria-current="page"]')).to_have_text("Product returns")
            row = page.locator(".sales-return-ledger .lifecycle-case").first
            rows = page.locator(".sales-return-ledger .lifecycle-case-items")
            content_width = rows.evaluate("el => el.clientWidth - parseFloat(getComputedStyle(el).paddingLeft) - parseFloat(getComputedStyle(el).paddingRight)")
            assert abs(row.bounding_box()["width"] - content_width) <= 2
            page.screenshot(path=str(output / f"returns-{width}.png"), full_page=True)
            page.get_by_role("combobox", name="Filter by status", exact=True).select_option("completed")
            page.get_by_role("button", name="Filter", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert "view=returns" in page.url and "status=completed" in page.url
            page.locator(f'[data-return-open="{case}"]').click()
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("dialog", name=f"Return #{case}", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            page.get_by_role("link", name="Money refunds", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('[data-refund-workspace]')).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"workspace-{width}.png"), full_page=True)
        assert not errors, errors
        browser.close()
