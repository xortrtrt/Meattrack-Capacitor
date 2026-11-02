"""Sales tasks, reseller offers and contextual chat using an isolated database."""
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, security_controls, web_sessions
from app.business_time import business_now
from app.database import fetch_one
from app.repositories import product_returns as returns
from test_return_request_options import sign_in
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, substitute, supporting_photo  # noqa: F401
from uuid import uuid4
from test_return_customer import complete_replacement

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_sales_and_reseller_tasks_chat_and_acknowledgment(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    new_case = create_case(d, "replace")
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    output = Path("outputs/return-customer-experience")
    output.mkdir(parents=True, exist_ok=True)

    def as_role(role):
        session.clear()
        session.update(account_id=d[role], role_key="reseller" if role == "reseller" else "sales-supervisor" if role=="supervisor" else "team-leader",
                       team_leader_role="sales-supervisor" if role=="supervisor" else "sales" if role == "sales" else None, csrf_token="customer-browser-token",
                       portal_expires_at=int(business_now().timestamp()) + 900)

    with TestClient(main.app, base_url="http://return-customer.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            req = route.request
            parts = urlsplit(req.url)
            response = client.request(req.method, parts.path + ("?" + parts.query if parts.query else ""),
                content=req.post_data_buffer, headers={k: v for k, v in req.headers.items()
                if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={k: v for k, v in response.headers.items()
                if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://return-customer.test/**", bridge)
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            as_role("sales")
            page.goto(f"http://return-customer.test/portal/team-leader/refunds?view=returns&case={new_case}", wait_until="networkidle")
            dialog = page.get_by_role("dialog", name=f"Return #{new_case}", exact=True)
            expect(dialog.locator("[data-return-next-step]")).to_contain_text("Review request")
            expect(dialog.get_by_role("button", name="Approve return request", exact=True)).to_be_visible()
            assert dialog.locator('button.button[type=submit]:visible').count() == 1
            assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
            page.screenshot(path=str(output / f"sales-review-{width}.png"), full_page=True)
            as_role("reseller")
            page.goto(f"http://return-customer.test/portal/reseller/history?view=returns&case={case}", wait_until="networkidle")
            dialog = page.locator("[data-return-details]")
            page.get_by_role("tab", name="Replacement & Delivery", exact=True).click()
            expect(dialog.get_by_role("heading", name="Completed return receipt", exact=True)).to_be_visible()
            expect(dialog.locator(".return-offer__difference").first).to_have_text("No additional payment")
            expect(dialog.get_by_role("link", name=f"Open conversation for Return #{case}", exact=True)).to_be_visible()
            assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"reseller-completed-{width}.png"), full_page=True)
        dialog.get_by_role("link", name="Report an issue", exact=True).click()
        page.wait_for_load_state("networkidle")
        draft = page.locator("[data-reseller-chat-form] textarea")
        expect(draft).to_be_editable()
        expect(draft).to_have_value(f"I have a problem with my replacement for Return #{case}: ")
        assert fetch_one("SELECT COUNT(*) n FROM reseller_messages")["n"] == 0
        assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 0
        page.screenshot(path=str(output / "chat-draft.png"), full_page=True)
        draft.fill("One replacement pack arrived with damaged packaging")
        page.get_by_role("button", name="Send message", exact=True).click()
        expect(page.locator("[data-return-chat-notice]")).to_contain_text("saved for Supervisor/Owner")
        assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups WHERE status='open'")["n"] == 1
        as_role("supervisor")
        page.goto(f"http://return-customer.test/portal/sales-supervisor/returns?case={case}", wait_until="networkidle")
        dialog = page.locator("[data-return-details]")
        page.get_by_role("tab", name="Review Decision", exact=True).click()
        expect(page.locator('[data-return-panel="activity"] .return-workflow')).to_contain_text("Resolve reported issue")
        expect(dialog).to_contain_text("One replacement pack arrived with damaged packaging")
        assert dialog.locator('button.button[type=submit]:visible').count() == 1
        page.screenshot(path=str(output / "sales-followup.png"), full_page=True)
        dialog.get_by_label("Resolution for the reseller").fill("We contacted you and resolved the packaging concern.")
        dialog.get_by_role("button", name="Resolve follow-up", exact=True).click()
        page.get_by_role("dialog", name="Resolve reported issue?", exact=True).get_by_role("button", name="Confirm resolution", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert fetch_one("SELECT status FROM return_customer_followups")["status"] == "resolved"
        as_role("reseller")
        page.goto(f"http://return-customer.test/portal/reseller/history?view=returns&case={case}", wait_until="networkidle")
        dialog = page.locator("[data-return-details]")
        dialog.get_by_role("button", name="Replacement received", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Confirm replacement received?", exact=True)
        expect(confirmation).to_be_visible()
        confirmation.get_by_role("button", name="Confirm receipt", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("button", name="Replacement received", exact=True)).to_have_count(0)
        assert returns.get_product_return(case, d["reseller"])["status"] == "completed"
        assert not errors, errors
        browser.close()


def test_revised_offers_payment_proof_and_unused_credit_are_clear(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    output = Path("outputs/return-customer-experience")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://offer.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def bridge(route):
            req = route.request
            parts = urlsplit(req.url)
            response = client.request(req.method, parts.path + ("?" + parts.query if parts.query else ""),
                content=req.post_data_buffer, headers={k: v for k, v in req.headers.items()
                if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={k: v for k, v in response.headers.items()
                if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://offer.test/**", bridge)
        for price in (100, 250):
            case = create_case(d, "replace")
            returns.review_product_return(case, d["sales"], True, "Approved the original products")
            returns.accept_replacement_quote(case, d["reseller"], 1)
            returns.review_product_return(case, d["supervisor"], True, "Revised the replacement selection", [(substitute(price), 1)])
            for width in (375, 1440):
                page.set_viewport_size({"width": width, "height": 1000})
                page.goto(f"http://offer.test/portal/reseller/history?view=returns&case={case}", wait_until="networkidle")
                dialog = page.locator("[data-return-details]")
                page.get_by_role("tab", name="Replacement & Delivery", exact=True).click()
                expect(dialog.locator(".return-offer").first).to_contain_text("Approved for return")
                expect(dialog.locator(".return-offer__changes").first).to_be_visible()
                difference = dialog.locator(".return-offer__difference").first
                expect(difference).to_contain_text("Amount to refund" if price == 100 else "Additional payment")
                expect(difference).to_contain_text("100.00" if price == 100 else "50.00")
                expect(dialog.get_by_role("button", name="Accept this replacement offer", exact=True)).to_be_visible()
                assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
                page.screenshot(path=str(output / f"revised-offer-{price}-{width}.png"), full_page=True)
            dialog.get_by_role("button", name="Accept this replacement offer", exact=True).click()
            confirmation = page.get_by_role("dialog", name="Accept replacement offer?", exact=True)
            confirmation.get_by_role("button", name="Cancel", exact=True).click()
            assert returns.get_product_return(case, d["reseller"])["accepted_revision"] is None
            dialog.get_by_role("button", name="Accept this replacement offer", exact=True).click()
            confirmation.get_by_role("button", name="Confirm acceptance", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert returns.get_product_return(case, d["reseller"])["accepted_revision"] == 2
            returns.inspect_product_return(case, d["inventory"], "complete", "All approved products matched", str(uuid4()))
            returns.reserve_replacement(case, d["inventory"], "Valid replacement stock reserved")
            if price == 250:
                returns.record_return_payment(case, d["reseller"], [supporting_photo()])
            else:
                returns.release_replacement(case, d["inventory"], "Released the lower-value replacement")
            page.goto(f"http://offer.test/portal/reseller/history?view=returns&case={case}", wait_until="networkidle")
            dialog = page.locator("[data-return-details]")
            page.get_by_role("tab", name="Activity History", exact=True).click()
            if price == 250:
                expect(dialog.locator('[data-workflow-step="payment_sent"]')).to_contain_text("Resubmit confirmed receipt details")
                expect(dialog.get_by_role("button", name="Send payment proof", exact=True)).not_to_be_visible()
                page.get_by_role("tab", name="Payment & Refunds", exact=True).click()
                expect(dialog.locator("[data-receipt-checkout]")).to_be_visible()
                expect(dialog.locator("[data-receipt-submit]")).to_be_disabled()
            else:
                expect(dialog.locator('[data-workflow-step="release"]')).to_have_class("is-done")
                expect(dialog.locator('[data-workflow-step="refund"]')).to_have_attribute("aria-current", "step")
                page.get_by_role("tab", name="Payment & Refunds", exact=True).click()
                expect(dialog.locator(".return-tracking-refund")).to_contain_text("PHP 100.00")
            page.screenshot(path=str(output / f"offer-outcome-{price}.png"), full_page=True)
        browser.close()
