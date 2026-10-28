"""Return history lives in Order History, with requests inside a modal."""
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo  # noqa: F401
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


def test_returns_tab_legacy_links_and_request_redirects(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        legacy = client.get(f"/portal/reseller/returns?order={d['order']}&status=submitted", follow_redirects=False)
        target = urlsplit(legacy.headers["location"])
        assert legacy.status_code == 303 and target.path == "/portal/reseller/history"
        assert parse_qs(target.query) == {"view": ["returns"], "order": [str(d["order"])], "status": ["submitted"]}
        page = client.get("/portal/reseller/history?view=returns")
        assert page.status_code == 200
        assert 'data-auto-open="false"' in page.text
        assert 'aria-current="page"><i data-lucide="undo-2"></i> My Returns</a>' in page.text
        assert '<input type="hidden" name="view" value="returns">' in page.text
        assert f'<option value="{d["order"]}">Order #{d["order"]}' in page.text
        navigation = page.text.split('id="portal-nav"', 1)[1].split("</nav>", 1)[0]
        assert "My Returns" not in navigation
        assert 'class="active"' in navigation and '/portal/reseller/history' in navigation
        assert "js/product_returns.js" in page.text and "css/product_lifecycle.css" in page.text
        invalid = client.post("/portal/reseller/returns/actions/submit", data={
            "order_id": d["order"], "allocation_id": d["allocation"], "quantity": 0,
            "resolution": "refund", "remarks": "Testing invalid empty quantities", "operation_key": str(uuid4()),
        }, follow_redirects=False)
        error_url = invalid.headers["location"]
        assert urlsplit(error_url).path == "/portal/reseller/history"
        assert parse_qs(urlsplit(error_url).query)["view"] == ["returns"]
        failed = client.get(error_url)
        assert failed.status_code == 200 and 'data-auto-open="true"' in failed.text
        assert 'returns-dialog-notice is-error' in failed.text
        photo = supporting_photo()
        created = client.post("/portal/reseller/returns/actions/submit", files={"evidence": (photo["filename"], photo["content"], photo["content_type"])}, data={
            "order_id": d["order"], "allocation_id": d["allocation"], "quantity": 2,
            "resolution": "refund", "remarks": "Request two delivered packs for eligibility review", "operation_key": str(uuid4()),
        }, follow_redirects=False)
        query = parse_qs(urlsplit(created.headers["location"]).query)
        assert created.status_code == 303 and query["view"] == ["returns"] and "case" in query
        tracked = client.get(f"/portal/reseller/returns?case={query['case'][0]}&status=submitted")
        assert tracked.status_code == 200 and 'data-return-dialog=' in tracked.text
        assert urlsplit(str(tracked.url)).path == "/portal/reseller/history"
        assert 'name="status"' in tracked.text and 'value="submitted" selected' in tracked.text
    assert fetch_one("SELECT count(*) n FROM order_refunds WHERE order_id=%s", (d["order"],))["n"] == 0


@pytest.mark.e2e
def test_order_history_returns_modal_on_mobile_and_desktop(lifecycle, monkeypatch):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    output = Path("outputs/reseller-returns-tab")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://returns-tab.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
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

        page.route("http://returns-tab.test/**", bridge)
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto("http://returns-tab.test/portal/reseller/history?view=fulfilled", wait_until="networkidle")
            page.get_by_role("link", name="My Returns", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('.order-history-tabs a[aria-current="page"]')).to_have_text("My Returns")
            expect(page.locator('.portal-nav a[href="/portal/reseller/returns"]')).to_have_count(0)
            request = page.locator("[data-return-request-dialog]")
            expect(request).not_to_be_visible()
            opener = page.get_by_role("button", name="Request a return / refund", exact=True)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            opener.click()
            expect(request).to_be_visible()
            expect(request.get_by_role("combobox", name="Order", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(request).not_to_be_visible()
            expect(opener).to_be_focused()
            expect(page).not_to_have_url(re.compile(r"[?&]request="))
            assert "request=" not in page.url and "view=returns" in page.url
            opener.click()
            request.get_by_role("combobox", name="Order", exact=True).select_option(str(d["order"]))
            request.get_by_role("button", name="Continue", exact=True).click()
            page.wait_for_load_state("networkidle")
            request = page.locator("[data-return-request-dialog]")
            expect(request).to_be_visible()
            expect(request.get_by_role("radio", name="Refund", exact=True)).to_be_checked()
            expect(request.locator(".return-request-section")).to_have_count(3)
            expect(request.get_by_text("Enter packs for at least one product. Leave the rest at 0.", exact=True)).to_be_visible()
            page.get_by_role("radio", name="Replacement", exact=True).check()
            expect(page.get_by_role("radio", name="Replacement", exact=True)).to_be_checked()
            page.get_by_role("radio", name="Refund", exact=True).check()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"request-{width}.png"), full_page=True)
            actions = request.locator(".return-request-actions")
            actions.scroll_into_view_if_needed()
            cancel = actions.get_by_role("button", name="Cancel", exact=True)
            submit = actions.get_by_role("button", name="Request return", exact=True)
            expect(cancel).to_be_visible()
            expect(submit).to_be_visible()
            cancel_box, submit_box = cancel.bounding_box(), submit.bounding_box()
            assert cancel_box["height"] >= 44 and submit_box["height"] >= 44
            assert submit_box["x"] - cancel_box["x"] - cancel_box["width"] >= 8
            page.screenshot(path=str(output / f"actions-{width}.png"), full_page=True)
            cancel.click()
            expect(request).not_to_be_visible()
            expect(page).not_to_have_url(re.compile(r"[?&]order="))
            opener.click()
            expect(request).to_be_visible()
            assert f"order={d['order']}" in page.url
            page.mouse.click(0, 0)
            expect(request).not_to_be_visible()
            expect(opener).to_be_focused()
            expect(page).not_to_have_url(re.compile(r"[?&]order="))
            assert "order=" not in page.url
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            assert not page.locator("html").evaluate("el => el.classList.contains('return-dialog-is-open')")
            page.screenshot(path=str(output / f"history-{width}.png"), full_page=True)
        opener.click()
        page.get_by_label("Return quantity for Test Product batch TEST-1").fill("2")
        page.get_by_label("Reason and remarks").fill("Return delivered products for Sales eligibility review")
        upload = page.get_by_label("Supporting photos")
        expect(upload).to_have_attribute("required", "")
        page.get_by_role("button", name="Request return", exact=True).click()
        expect(upload).to_be_focused()
        assert upload.evaluate("el => el.validity.valueMissing")
        assert fetch_one("SELECT count(*) n FROM product_returns WHERE order_id=%s", (d["order"],))["n"] == 0
        photo = supporting_photo()
        page.get_by_label("Supporting photos").set_input_files({"name": photo["filename"], "mimeType": photo["content_type"], "buffer": photo["content"]})
        page.get_by_role("button", name="Request return", exact=True).click()
        page.wait_for_load_state("networkidle")
        case = fetch_one("SELECT return_id,status FROM product_returns WHERE order_id=%s", (d["order"],))
        assert case and case["status"] == "submitted"
        details = page.locator("[data-return-dialog]")
        expect(details).to_be_visible()
        page.keyboard.press("Escape")
        expect(details).not_to_be_visible()
        expect(page.locator(f'[data-return-open="{case["return_id"]}"]')).to_be_focused()
        # Use a canonical GET after the in-process POST redirect bridge.
        page.goto("http://returns-tab.test/portal/reseller/history?view=returns", wait_until="networkidle")
        page.get_by_role("combobox", name="Show requests", exact=True).select_option("submitted")
        page.get_by_role("button", name="Show requests", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert "view=returns" in page.url and "status=submitted" in page.url
        page.locator(f'[data-return-open="{case["return_id"]}"]').click()
        expect(page.locator("[data-return-dialog]")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page).not_to_have_url(re.compile(r"[?&]case="))
        assert "status=submitted" in page.url and "case=" not in page.url
        page.get_by_role("link", name="All orders", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.get_by_role("link", name="Fulfilled", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.get_by_role("link", name="My Returns", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(f'[data-return-open="{case["return_id"]}"]')).to_be_visible()
        expect(page.locator("[data-return-request-dialog]")).not_to_be_visible()
        assert not errors, errors
        browser.close()
