"""Dedicated details page uses actual application handlers in an isolated schema."""
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from legacy_return_fixtures import create_legacy_return, legacy_preview
from app import main
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo, substitute  # noqa: F401
from test_return_request_options import sign_in

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_rejection_validation_confirmation_tabs_and_back_filters(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    comparison = legacy_preview(d["order"], d["reseller"], [(d["allocation"], 2)], [(d["product"], 2)])
    case = create_legacy_return(d["order"], d["reseller"], "replace", [(d["allocation"], 2)],
        "Synthetic packaging issue for browser review", attachments=[supporting_photo()],
        replacement_items=[(d["product"], 2)], comparison_token=comparison["token"])
    sign_in(monkeypatch, d["supervisor"], "sales-supervisor", "sales-supervisor")
    output = Path("outputs/supervisor-details")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://supervisor-page.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 768, "height": 1024})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                content=request.post_data_buffer, headers={k:v for k,v in request.headers.items()
                    if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={k:v
                for k,v in response.headers.items() if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://supervisor-page.test/**", bridge)
        page.goto("http://supervisor-page.test/portal/sales-supervisor/returns?status=submitted", wait_until="networkidle")
        page.locator(f'[data-return-open="{case}"]').click()
        expect(page.locator("[data-return-details]")).to_be_visible()
        page.get_by_role("link", name="Return review", exact=True).click()
        expect(page).to_have_url("http://supervisor-page.test/portal/sales-supervisor/returns?status=submitted")
        page.locator(f'[data-return-open="{case}"]').click()
        for label in ("Review Decision", "Inventory & Replacement", "Settlement", "Activity History", "Overview"):
            page.get_by_role("tab", name=label, exact=True).click()
            assert page.locator('[data-return-panel]:visible').count() == 1
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        page.screenshot(path=str(output / "tablet-overview.png"), full_page=True)
        page.get_by_role("button", name="Reject request", exact=True).click()
        reason = page.get_by_label("Reason for the reseller", exact=True)
        expect(reason).to_be_focused()
        expect(page.locator('[data-return-confirm]')).not_to_be_visible()
        reason.fill("The submitted return does not meet the documented eligibility requirements.")
        page.get_by_label("Internal rejection notes", exact=True).fill("Synthetic browser validation decision")
        page.get_by_role("button", name="Reject request", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Reject return request?", exact=True)
        expect(confirmation).to_be_visible()
        confirmation.get_by_role("button", name="Cancel", exact=True).click()
        assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "submitted"
        page.get_by_role("button", name="Reject request", exact=True).click()
        confirmation.get_by_role("button", name="Confirm rejection", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "rejected"
        expect(page.get_by_role("heading", name="Recorded decision", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Approve return request", exact=True)).to_have_count(0)
        expect(page.locator("[data-return-details]")).to_contain_text("documented eligibility requirements")
        page.screenshot(path=str(output / "recorded-rejection.png"), full_page=True)
        # A different offer still goes through the existing authoritative pricing path.
        alternative = substitute(250)
        comparison = legacy_preview(d["order"], d["reseller"], [(d["allocation"], 2)], [(d["product"], 2)])
        revised = create_legacy_return(d["order"], d["reseller"], "replace", [(d["allocation"], 2)],
            "Synthetic request with an alternative selection", attachments=[supporting_photo()],
            replacement_items=[(d["product"], 2)], comparison_token=comparison["token"])
        page.goto(f"http://supervisor-page.test/portal/sales-supervisor/returns?case={revised}", wait_until="networkidle")
        page.get_by_role("tab", name="Review Decision", exact=True).click()
        page.locator('.sales-return-substitutions > summary').click()
        page.get_by_label("Replacement product 1", exact=True).select_option(str(alternative))
        page.locator('[name="replacement_quantity"]').first.fill("1")
        page.get_by_label("Internal review notes", exact=True).fill("Synthetic approval of alternative product")
        page.get_by_role("button", name="Approve return request", exact=True).click()
        approval = page.get_by_role("dialog", name="Approve return request?", exact=True)
        expect(approval.locator('[data-confirm-current-offer]')).not_to_be_visible()
        expect(approval.locator('[data-confirm-revised-offer]')).to_be_visible()
        approval.get_by_role("button", name="Confirm approval", exact=True).click()
        page.wait_for_load_state("networkidle")
        stored = fetch_one("SELECT credit,replacement_total,quote_revision,accepted_revision FROM product_returns WHERE return_id=%s", (revised,))
        assert stored["credit"] == 200 and stored["replacement_total"] == 250
        assert stored["quote_revision"] == 2 and stored["accepted_revision"] != 2
        expect(page.get_by_role("heading", name="Recorded decision", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Revise replacement offer", exact=True)).to_be_visible()
        page.get_by_role("button", name="Revise replacement offer", exact=True).click()
        page.get_by_label("Internal review notes", exact=True).fill("Synthetic follow-up revision review")
        page.get_by_role("button", name="Save revised request", exact=True).click()
        revision = page.get_by_role("dialog", name="Save revised request?", exact=True)
        expect(revision).to_be_visible()
        revision.get_by_role("button", name="Cancel", exact=True).click()
        assert fetch_one("SELECT quote_revision FROM product_returns WHERE return_id=%s", (revised,))["quote_revision"] == 2
        assert not errors, errors
        browser.close()
