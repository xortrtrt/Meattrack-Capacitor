"""Non-expired request eligibility and traceable collection figures."""

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions
from app.business_time import business_now, business_today
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import _seed_fulfillment, strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, receive, supporting_photo  # noqa: F401

pytestmark = pytest.mark.postgres


def sign_in(monkeypatch, actor_id, role, specialization=None):
    session = dict(account_id=actor_id, role_key=role, team_leader_role=specialization,
                   portal_expires_at=int(business_now().timestamp()) + 600)
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)


@pytest.mark.parametrize("resolution", ["refund", "replace"])
def test_non_expired_fulfilled_products_offer_both_choices_and_remain_subject_to_review(lifecycle, monkeypatch, resolution):  # noqa: F811
    d = lifecycle
    expiry = business_today() + timedelta(days=3)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s", (expiry, d["batches"][0]))
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        response = client.get(f"/portal/reseller/returns?order={d['order']}")
        assert response.status_code == 200
        assert 'type="radio" name="resolution" value="refund"' in response.text
        assert 'type="radio" name="resolution" value="replace"' in response.text
        assert 'aria-label="Return quantity for Test Product batch TEST-1"' in response.text
        assert "Subject to Sales approval." in response.text
    case = returns.create_product_return(d["order"], d["reseller"], resolution, [(d["allocation"], 2)], "Product issue discovered before expiry; requesting eligibility review", attachments=[supporting_photo()])
    state = fetch_one("SELECT status,approved_by_account_id FROM product_returns WHERE return_id=%s", (case,))
    assert state["status"] == "submitted" and state["approved_by_account_id"] is None
    assert fetch_one("SELECT count(*) n FROM order_refunds WHERE return_id=%s", (case,))["n"] == 0
    before = fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))
    returns.review_product_return(case, d["sales"], True, "Reviewed product issue and approved these returned quantities")
    receive(d, case, authorize=False)
    assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],)) == before
    holding = fetch_one("SELECT source,expiry_date,quantity_remaining FROM expired_holdings WHERE return_line_id IN (SELECT return_line_id FROM product_return_lines WHERE return_id=%s)", (case,))
    assert holding["source"] == "return" and holding["expiry_date"] == expiry and holding["quantity_remaining"] == 2
    assert not returns.get_product_return(case, d["sales"])["sales_authorized"]
    if resolution == "refund":
        refund = returns.get_product_return(case, d["sales"])["refund"]["order_refund_id"]
        with pytest.raises(ValueError, match="Sales confirmation"):
            repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")


def test_all_claimed_quantities_show_existing_request_guidance_instead_of_empty_request_form(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    returns.create_product_return(d["order"], d["reseller"], "refund", [(d["allocation"], 10)], "Full original delivery requested for Sales review", attachments=[supporting_photo()])
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        response = client.get(f"/portal/reseller/returns?order={d['order']}")
        assert response.status_code == 200 and "Check your existing return requests" in response.text
        assert 'action="/portal/reseller/returns/actions/submit"' not in response.text


def test_collection_breakdown_explains_small_payment_without_rewriting_order_value(strict_inventory_database, monkeypatch):  # noqa: F811
    sales, product, batches, orders = _seed_fulfillment(strict_inventory_database, batch_quantities=[20], order_quantities=[6])
    order = orders[0]
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE order_items SET unit_price=70 WHERE order_id=%s", (order,))
        cur.execute("UPDATE orders SET total_amount=420,payment_amount=50 WHERE order_id=%s", (order,))
        cur.execute("SELECT created_by_account_id FROM orders WHERE order_id=%s", (order,))
        reseller = cur.fetchone()["created_by_account_id"]
    repositories.decide_order(order, "fulfill", team_leader_account_id=sales)
    totals = returns.return_settlement_totals(sales)
    assert totals["original_sales"] == 420 and totals["original_collections"] == totals["net_collections"] == 50
    assert totals["payment_difference_total"] == 370 and totals["reconciliation_count"] == 1
    source = totals["source_orders"][0]
    assert source["order_id"] == order and source["order_value"] == 420
    assert source["original_collections"] == 50 and source["additional_collections"] == source["completed_refunds"] == 0
    sign_in(monkeypatch, sales, "team-leader", "sales")
    with TestClient(main.app) as client:
        response = client.get("/portal/team-leader/returns")
        assert response.status_code == 200
        assert "PHP 420.00" in response.text and "PHP 50.00" in response.text
        for removed in ("Where these collections came from", "Settlement breakdown", "not the order value or profit",
                        "Open existing refund workflow"):
            assert removed not in response.text
        assert 'aria-label="Return cases"' in response.text and "Requires reconciliation" in response.text
    sign_in(monkeypatch, reseller, "reseller")
    with TestClient(main.app) as client:
        response = client.get(f"/portal/reseller/returns?order={order}")
        assert 'action="/portal/reseller/returns/actions/submit"' in response.text
        assert "PHP 370.00" not in response.text and "recorded payment is" not in response.text
        assert "Subject to Sales approval." in response.text
    assert fetch_one("SELECT payment_amount FROM orders WHERE order_id=%s", (order,))["payment_amount"] == 50


def test_return_request_order_lookup_does_not_disclose_another_customer_order(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO accounts(account_type,reseller_id,name,email,password_hash)
            SELECT 'reseller',reseller_id,'Other requestor',%s,'hash' FROM accounts WHERE account_id=%s
            RETURNING account_id""", (uuid4().hex + "@return-options.test", d["reseller"]))
        other = cur.fetchone()["account_id"]
    sign_in(monkeypatch, other, "reseller")
    with TestClient(main.app) as client:
        assert client.get(f"/portal/reseller/returns?order={d['order']}").status_code == 404


@pytest.mark.e2e
def test_non_expired_request_choices_and_collection_sources_in_browser(strict_inventory_database, monkeypatch):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    sales, _, _, orders = _seed_fulfillment(strict_inventory_database, batch_quantities=[20], order_quantities=[6])
    order = orders[0]
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE order_items SET unit_price=70 WHERE order_id=%s", (order,))
        cur.execute("UPDATE orders SET total_amount=420,payment_amount=50 WHERE order_id=%s", (order,))
        cur.execute("SELECT created_by_account_id FROM orders WHERE order_id=%s", (order,))
        reseller = cur.fetchone()["created_by_account_id"]
    repositories.decide_order(order, "fulfill", team_leader_account_id=sales)
    output = Path("outputs/return-request-options")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://options.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 375, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                content=request.post_data_buffer, headers={k: v for k, v in request.headers.items()
                    if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={k: v
                for k, v in response.headers.items() if k not in
                {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://options.test/**", bridge)
        sign_in(monkeypatch, reseller, "reseller")
        page.goto(f"http://options.test/portal/reseller/returns?order={order}", wait_until="networkidle")
        page.get_by_role("radio", name="Refund", exact=True).check()
        page.get_by_role("radio", name="Replacement", exact=True).check()
        page.get_by_role("radio", name="Refund", exact=True).check()
        page.get_by_label("Return quantity for Test Product batch TEST-1").fill("6")
        page.get_by_label("Reason and remarks").fill("Non-expired product issue submitted for Sales eligibility review")
        photo = supporting_photo()
        page.get_by_label("Supporting photos").set_input_files({"name": photo["filename"], "mimeType": photo["content_type"], "buffer": photo["content"]})
        expect(page.get_by_role("button", name="Request return", exact=True)).to_be_enabled()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        page.screenshot(path=str(output / "reseller-request-375.png"), full_page=True)
        page.get_by_role("button", name="Request return", exact=True).click()
        page.wait_for_load_state("networkidle")
        case = fetch_one("SELECT return_id,status FROM product_returns WHERE order_id=%s", (order,))
        assert case and case["status"] == "submitted"
        assert fetch_one("SELECT count(*) n FROM order_refunds WHERE order_id=%s", (order,))["n"] == 0
        sign_in(monkeypatch, sales, "team-leader", "sales")
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto("http://options.test/portal/team-leader/returns", wait_until="networkidle")
            expect(page.locator(".collection-source-breakdown")).to_have_count(0)
            expect(page.locator(".lifecycle-financial-details")).to_have_count(0)
            expect(page.locator(".lifecycle-returns-list")).to_be_visible()
            expect(page.get_by_role("link", name="Open existing refund workflow", exact=True)).to_have_count(0)
            section = page.locator(".lifecycle-metrics--returns")
            expect(section).to_contain_text("PHP 420.00")
            expect(section).to_contain_text("PHP 50.00")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"sales-sources-{width}.png"), full_page=True)
        assert not errors, errors
        browser.close()
