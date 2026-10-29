"""Inventory accepts a complete matching return or reports a mismatch to Sales."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, security_controls, web_sessions
from app import repositories
from app.business_time import business_now
from app.database import fetch_all, fetch_one
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, complete_refund  # noqa: F401
from test_return_request_options import sign_in
from test_product_lifecycle import refund_proof, substitute

pytestmark = pytest.mark.postgres


def approved(d, resolution="replace"):
    case = create_case(d, resolution)
    returns.review_product_return(case, d["sales"], True, "Approved original products and quantities after proof review")
    return case


@pytest.mark.parametrize("resolution", ["refund", "replace"])
def test_complete_receipt_is_atomic_traceable_and_clears_approved_resolution(lifecycle, resolution):  # noqa: F811
    d = lifecycle
    case = approved(d, resolution)
    before = fetch_all("SELECT batch_id,quantity_available FROM inventory_batches ORDER BY batch_id")
    key = str(uuid4())
    for _ in range(2):
        returns.inspect_product_return(case, d["inventory"], "complete", "Every approved pack, batch, and photo matches", key)
    sales = returns.get_product_return(case, d["sales"])
    assert sales["status"] == "settlement_pending"
    assert sales["verified_by_account_id"] == d["inventory"]
    assert sales["sales_authorized"] and sales["receipt_confirmed"]
    assert sales["settlement_authorized_by_account_id"] == d["sales"]
    assert sales["lines"][0]["received_quantity"] == sales["lines"][0]["quantity"] == 2
    assert sales["receipts"][0]["location"] == "Discarded inventory"
    assert fetch_one("SELECT count(*) n FROM product_return_receipts WHERE return_id=%s", (case,))["n"] == 1
    assert fetch_one("SELECT count(*) n FROM product_return_events WHERE return_id=%s AND action='inventory_receipt_complete'", (case,))["n"] == 1
    assert fetch_all("SELECT batch_id,quantity_available FROM inventory_batches ORDER BY batch_id") == before
    if resolution == "replace":
        with pytest.raises(ValueError, match="acceptance"):
            returns.reserve_replacement(case, d["inventory"], "Must wait for reseller acceptance")
        returns.accept_replacement_quote(case, d["reseller"], sales["quote_revision"])
        returns.reserve_replacement(case, d["inventory"], "Reserved valid stock after complete receipt")
        returns.release_replacement(case, d["inventory"], "Replacement handed to the reseller")
    else:
        complete_refund(d, case)
    assert returns.get_product_return(case, d["sales"])["status"] == "completed"


def test_mismatch_notifies_sales_once_blocks_release_and_requires_sales_review(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    key = str(uuid4())
    remarks = "Two packs expected but only one arrived; batch differs from the supporting photo"
    for _ in range(2):
        returns.inspect_product_return(case, d["inventory"], "mismatch", remarks, key)
    state = returns.get_product_return(case, d["sales"])
    assert state["status"] == "discrepancy" and not state["verified_at"] and not state["sales_authorized"]
    assert state["lines"][0]["received_quantity"] == 0 and not state["receipts"]
    notices = fetch_all("""SELECT n.title,n.message FROM notifications n JOIN notification_recipients r USING(notification_id)
        WHERE n.source_type='product_returns' AND n.source_id=%s AND r.account_id=%s
        AND n.title='Return receipt mismatch—Sales review required'""", (case, d["sales"]))
    assert len(notices) == 1 and remarks in notices[0]["message"]
    with pytest.raises(ValueError):
        returns.reserve_replacement(case, d["inventory"], "Release must stay blocked on a mismatch")
    with pytest.raises(ValueError, match="resolve the mismatch"):
        returns.inspect_product_return(case, d["inventory"], "complete", "Attempt before Sales reviewed the mismatch", str(uuid4()))
    line = state["lines"][0]
    returns.review_product_return(case, d["sales"], True, "Reviewed proof and approved the corrected one-pack request",
                                 revised_quantities=[(line["return_line_id"], 1)], customer_message="Sales reviewed the mismatch and approved the corrected one-pack request.")
    returns.inspect_product_return(case, d["inventory"], "complete", "The revised complete request now matches the received pack", str(uuid4()))
    checked = returns.get_product_return(case, d["sales"])
    assert checked["lines"][0]["received_quantity"] == checked["lines"][0]["quantity"] == 1
    assert checked["sales_authorized"]


def test_complete_receipt_preserves_additional_payment_and_concurrent_idempotency_gates(lifecycle):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    d = lifecycle
    case = create_case(d, "replace")
    returns.review_product_return(case, d["sales"], True, "Approved the higher-value replacement selection", [(substitute(250), 1)])
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(returns.inspect_product_return, case, d["inventory"], "complete",
                                "Complete products and proof matched", key) for _ in range(2)]
        for job in jobs:
            job.result(timeout=30)
    assert fetch_one("SELECT count(*) n FROM product_return_receipts WHERE return_id=%s", (case,))["n"] == 1
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved the accepted replacement after full receipt")
    assert not returns.get_product_return(case, d["inventory"])["inventory_can_release"]
    with pytest.raises(ValueError, match="additional payment"):
        returns.release_replacement(case, d["inventory"], "Payment must still be verified by Sales")
    returns.record_return_payment(case, d["reseller"], [refund_proof()])
    returns.verify_return_payment(case, d["sales"], "EXTRA" + uuid4().hex, 50, "Verified the additional replacement collection")
    returns.release_replacement(case, d["inventory"], "Handed over the replacement after Sales payment clearance")
    assert returns.get_product_return(case, d["sales"])["status"] == "completed"


def test_inventory_logs_include_return_product_order_destination_and_existing_receipts(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    total_before = repositories.count_inventory_movements()
    stock_rows_before = fetch_one("SELECT count(*) n FROM inventory_movements")["n"]
    key = str(uuid4())
    for _ in range(2):
        returns.inspect_product_return(case, d["inventory"], "complete", "All products and proof matched in full", key)
    rows = repositories.list_inventory_movements(category="returns")
    assert len(rows) == repositories.count_inventory_movements(category="returns") == 1
    assert rows[0]["entity"] == "Test Product" and rows[0]["quantity_delta"] == 2
    assert rows[0]["return_id"] == case and rows[0]["order_id"] == d["order"]
    assert rows[0]["destination"] == "Discarded inventory" and rows[0]["batch_code"] == "TEST-1"
    assert repositories.count_inventory_movements() == total_before + 1
    assert fetch_one("SELECT count(*) n FROM inventory_movements")["n"] == stock_rows_before
    assert repositories.count_inventory_movements(q="Discarded inventory", category="returns") == 1
    assert repositories.list_inventory_movements(q=str(d["order"]), category="returns")[0]["return_id"] == case
    legacy = approved(d)
    line = returns.get_product_return(legacy, d["inventory"])["lines"][0]
    returns.receive_product_return(legacy, d["inventory"], [(line["return_line_id"], 2)],
                                  "Legacy returns freezer", "Historical receipt into its actual location", str(uuid4()))
    newest = repositories.list_inventory_movements(category="returns", page=1, page_size=1)
    older = repositories.list_inventory_movements(category="returns", page=2, page_size=1)
    assert newest[0]["return_id"] == legacy and newest[0]["destination"] == "Legacy returns freezer"
    assert older[0]["return_id"] == case
    assert repositories.list_inventory_movements(category="returns", sort="oldest")[0]["return_id"] == case
    assert repositories.count_inventory_movements(category="returns") == 2


def test_http_requires_complete_attestation_rejects_partial_receipts_and_manual_location(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = approved(d)
    session = {"account_id": d["inventory"], "role_key": "team-leader", "team_leader_role": "inventory",
               "portal_expires_at": int(business_now().timestamp()) + 600, "csrf_token": "inventory-receipt-token"}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    with TestClient(main.app, headers={"Origin": "http://testserver"}) as client:
        page = client.get(f"/portal/team-leader/returns?case={case}")
        assert page.status_code == 200
        assert 'name="location"' not in page.text and 'name="quantity"' not in page.text
        assert "Everything is received and matches" in page.text
        line = returns.get_product_return(case, d["inventory"])["lines"][0]
        partial = client.post("/portal/team-leader/returns/actions/receive", data={
            "return_id": case, "line_id": line["return_line_id"], "quantity": 1,
            "location": "A user-supplied location", "remarks": "Only one of two packs arrived", "operation_key": str(uuid4()),
            "csrf_token": session["csrf_token"],
        }, follow_redirects=False)
        assert "Partial receipts" in parse_qs(urlsplit(partial.headers["location"]).query)["error"][0]
        payload = {"return_id": case, "outcome": "complete", "operation_key": str(uuid4()), "location": "Ignored location", "csrf_token": session["csrf_token"], "review_version": returns.get_product_return(case, d["inventory"])["receipt_review_version"]}
        unchecked = client.post("/portal/team-leader/returns/actions/inspect", data=payload, follow_redirects=False)
        assert "error" in parse_qs(urlsplit(unchecked.headers["location"]).query)
        assert returns.get_product_return(case, d["sales"])["lines"][0]["received_quantity"] == 0
        returns.review_product_return(case, d["sales"], True, "Sales revised the replacement approval while Inventory was reviewing")
        outdated = client.post("/portal/team-leader/returns/actions/inspect", data={**payload, "proof_matches": "1"}, follow_redirects=False)
        assert "approved request changed" in parse_qs(urlsplit(outdated.headers["location"]).query)["error"][0]
        payload["review_version"] = returns.get_product_return(case, d["inventory"])["receipt_review_version"]
        done = client.post("/portal/team-leader/returns/actions/inspect", data={**payload, "proof_matches": "1"})
        assert done.status_code == 200
        checked = returns.get_product_return(case, d["sales"])
        assert checked["receipts"][0]["location"] == "Discarded inventory"
        assert checked["sales_authorized"]
    session.update(account_id=d["sales"], team_leader_role="sales")
    with TestClient(main.app, headers={"Origin": "http://testserver"}) as client:
        denied = client.post("/portal/team-leader/returns/actions/inspect", data={**payload, "proof_matches": "1"})
        assert denied.status_code in {403, 404}


@pytest.mark.e2e
def test_inventory_modal_complete_or_mismatch_workflow_on_phone_and_desktop(lifecycle, monkeypatch):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    case = approved(d)
    sign_in(monkeypatch, d["inventory"], "team-leader", "inventory")
    output = Path("outputs/inventory-receipt-decision")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://receipt-decision.test") as client, sync_playwright() as p:
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

        page.route("http://receipt-decision.test/**", bridge)
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto(f"http://receipt-decision.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
            dialog = page.get_by_role("dialog", name=f"Return #{case}", exact=True)
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role("heading", name="Check products against the request", exact=True)).to_be_visible()
            expect(dialog.locator('input[name="location"], input[name="quantity"]')).to_have_count(0)
            expect(dialog.locator(".inventory-return-evidence img")).to_have_count(1)
            assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
            assert "PHP" not in dialog.inner_text()
            page.screenshot(path=str(output / f"check-{width}.png"), full_page=True)
            if width == 375:
                dialog.locator(".is-mismatch > summary").click()
                dialog.get_by_label("Missing products or proof mismatch").fill("Only one pack arrived; two approved packs were expected")
                dialog.get_by_role("button", name="Report mismatch to Sales", exact=True).click()
                page.wait_for_load_state("networkidle")
                expect(page.get_by_role("heading", name="Sales has been notified", exact=True)).to_be_visible()
                page.screenshot(path=str(output / "reported-mismatch.png"), full_page=True)
                returns.review_product_return(case, d["sales"], True, "Sales resolved the mismatch and confirmed both approved packs will be returned", customer_message="Please return both approved packs for Inventory to check.")
            else:
                dialog.locator(".is-complete > summary").click()
                dialog.get_by_label("I checked every approved product, quantity, batch, and supporting photo. Everything matches.").check()
                dialog.get_by_role("button", name="Confirm complete receipt", exact=True).click()
                page.wait_for_load_state("networkidle")
                expect(page.get_by_role("heading", name="Complete receipt confirmed", exact=True)).to_be_visible()
                expect(page.get_by_role("heading", name="Prepare the replacement", exact=True)).to_be_visible()
                page.screenshot(path=str(output / "confirmed-complete.png"), full_page=True)
        current = returns.get_product_return(case, d["sales"])
        returns.accept_replacement_quote(case, d["reseller"], current["quote_revision"])
        page.goto(f"http://receipt-decision.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
        page.get_by_role("button", name="Reserve replacement stock", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.get_by_label("Release remarks").fill("Handed the two replacement packs to the reseller")
        page.get_by_role("button", name="Confirm replacement released", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Replacement released", exact=True)).to_be_visible()
        assert returns.get_product_return(case, d["sales"])["status"] == "completed"
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto("http://receipt-decision.test/portal/team-leader/logs", wait_until="networkidle")
            balance = page.locator(".movement-row").first.locator(".movement-row__balance")
            expect(balance.get_by_text("Total stock after", exact=True)).to_be_visible()
            expect(balance).to_contain_text("28")
            expect(page.locator(".movement-list")).not_to_contain_text("Batch balance after")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"total-stock-log-{width}.png"), full_page=True)
            page.get_by_role("navigation", name="Movement categories").get_by_role("link", name="Returns", exact=True).click()
            expect(page.locator(".movement-row--return")).to_have_count(1)
            row = page.locator(".movement-row--return")
            expect(row).to_contain_text("Test Product")
            expect(row).to_contain_text(f"Order #{d['order']}")
            expect(row).to_contain_text("Discarded inventory")
            expect(row.get_by_text("Discarded inventory", exact=True)).to_be_visible()
            expect(page.get_by_role("navigation", name="Movement categories").get_by_role("link", name="Returns", exact=True)).to_be_visible()
            expect(row).to_contain_text("Non-sellable inventory")
            expect(row).not_to_contain_text("Stock in")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"return-log-{width}.png"), full_page=True)
            row.get_by_role("link", name=f"Return #{case}", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("dialog", name=f"Return #{case}", exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
