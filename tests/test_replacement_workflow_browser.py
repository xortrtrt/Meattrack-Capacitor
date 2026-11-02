"""Exercise the new reseller -> Supervisor -> Inventory journey with synthetic records."""

from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions, receipt_ocr
from app.business_time import business_now, business_today
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo, substitute  # noqa: F401

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


@pytest.mark.parametrize("width", [1440, 390])
def test_new_replacement_transaction_in_return_modal_and_linked_messages(lifecycle, monkeypatch, width):  # noqa: F811
    d = lifecycle
    substitute(300)
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, *args: session.update(data))
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    monkeypatch.setattr(
        receipt_ocr,
        "scan_payment_proofs",
        lambda attachments: dict(
            transaction_number="TOPUPBROWSER123",
            payment_amount="100.00",
            payment_date=business_today().isoformat(),
            payment_time=business_now().strftime("%H:%M"),
            confidence=0.9,
        ),
    )
    output = Path("outputs/replacement-workflow")
    output.mkdir(parents=True, exist_ok=True)

    def as_role(role):
        session.clear()
        session.update(
            account_id=d[role],
            role_key="reseller"
            if role == "reseller"
            else "sales-supervisor"
            if role == "supervisor"
            else "team-leader",
            team_leader_role="sales-supervisor" if role == "supervisor" else role,
            account_name="Workflow test user",
            account_email="workflow@example.test",
            portal_expires_at=int(business_now().timestamp()) + 1200,
        )

    with TestClient(main.app, base_url="http://replacement.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(
                req.method,
                url.path + ("?" + url.query if url.query else ""),
                content=req.post_data_buffer,
                headers={k: v for k, v in req.headers.items() if k not in {"host", "content-length", "cookie"}},
                follow_redirects=True,
            )
            route.fulfill(
                status=response.status_code,
                body=response.content,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
                },
            )

        page.route("http://replacement.test/**", bridge)
        as_role("reseller")
        page.goto(
            f"http://replacement.test/portal/reseller/history?view=returns&order={d['order']}", wait_until="networkidle"
        )
        dialog = page.locator("[data-return-request-dialog]")
        expect(dialog).to_be_visible()
        feedback = dialog.locator("[data-return-request-feedback]")
        submit = dialog.locator("[data-return-request-submit]")
        submit.click()
        expect(feedback).to_have_text("Enter at least one pack to return.")
        expect(submit).not_to_have_class("button is-loading")
        expect(submit).to_be_enabled()
        page.locator(f"#return-quantity-{d['allocation']}").fill("2")
        page.locator("#return-replacement").check()
        expect(submit).to_have_text("Review price comparison")
        expect(dialog.locator("[data-replacement-catalog]")).not_to_have_attribute("open", "")
        submit.click()
        expect(feedback).to_have_text("Choose at least one replacement pack.")
        expect(dialog.locator("[data-replacement-catalog]")).to_have_attribute("open", "")
        search = dialog.get_by_role("searchbox", name="Search products")
        search.fill("No matching test product")
        expect(dialog.locator("[data-replacement-empty]")).to_be_visible()
        search.fill("Substitute 300")
        search.press("Enter")
        expect(dialog.locator("[name=comparison_token]")).to_have_value("")
        expect(dialog.locator("[data-replacement-product]:visible")).to_have_count(1)
        page.get_by_label("Replacement packs for Substitute 300").fill("1")
        expect(dialog.locator("[data-replacement-selected]")).to_contain_text("Substitute 300")
        # A failed comparison must permit retry instead of stranding a loading button.
        preview_url = "http://replacement.test/portal/reseller/returns/replacement-preview"
        page.route(preview_url, lambda route: route.fulfill(status=503, json={"error": "Comparison temporarily unavailable. Retry."}), times=1)
        submit.click()
        expect(feedback).to_have_text("Comparison temporarily unavailable. Retry.")
        expect(submit).to_be_enabled()
        expect(submit).not_to_have_class("button is-loading")
        submit.click()
        expect(dialog.locator("[name=comparison_token]")).not_to_have_value("")
        expect(submit).to_have_text("Request return")
        expect(dialog.locator(".return-comparison-totals")).to_contain_text("Extra payment")
        expect(dialog.locator("[data-replacement-catalog]")).not_to_have_attribute("open", "")
        submit.click()
        expect(dialog.locator("[name=remarks]")).to_be_focused()
        expect(feedback).to_contain_text("Reason and remarks")
        expect(submit).to_be_enabled()
        dialog.locator("[name=remarks]").fill("Synthetic damaged packs for replacement")
        submit.click()
        expect(feedback).to_contain_text("Supporting photos")
        expect(submit).to_be_enabled()
        photo = supporting_photo()
        dialog.locator("[name=evidence]").set_input_files(
            {"name": "simulated-damage.png", "mimeType": "image/png", "buffer": photo["content"]}
        )
        # Quantity edits invalidate the reviewed comparison, and require fresh totals.
        page.locator(f"#return-quantity-{d['allocation']}").fill("1")
        expect(dialog.locator("[name=comparison_token]")).to_have_value("")
        page.locator(f"#return-quantity-{d['allocation']}").fill("2")
        page.get_by_role("button", name="Review price comparison", exact=True).click()
        expect(dialog.locator("[name=comparison_token]")).not_to_have_value("")
        expect(dialog.locator("[data-replacement-comparison]")).to_contain_text("Extra payment")
        assert submit.evaluate("el => { const r=el.getBoundingClientRect(); const d=el.closest('dialog').getBoundingClientRect(); return r.top>=d.top && r.bottom<=d.bottom && r.height>=44; }")
        assert dialog.evaluate("el => el.scrollWidth<=el.clientWidth+1")
        page.screenshot(path=str(output / f"comparison-{width}.png"), full_page=True)
        dialog.get_by_role("button", name="Request return", exact=False).click()
        page.wait_for_load_state("networkidle")
        case = fetch_one("SELECT return_id FROM product_returns ORDER BY return_id DESC LIMIT 1")["return_id"]
        page.get_by_role("tab", name="Replacement & Delivery", exact=True).click()
        expect(page.get_by_role("heading", name="Your return and replacement", exact=True)).to_be_visible()
        expect(page.locator('[data-return-panel="replacement"]')).to_contain_text("Original paid price")
        expect(page.locator('[data-return-panel="replacement"]')).to_contain_text("Catalog price")

        as_role("sales")
        page.goto(f"http://replacement.test/portal/team-leader/returns?queue=all&case={case}", wait_until="networkidle")
        tracking = page.get_by_role("dialog", name=f"Return #{case}", exact=True)
        expect(tracking.locator("[data-return-next-step]")).to_contain_text("Review request")
        expect(tracking).to_contain_text("Tracking only")
        expect(tracking.locator("details")).to_have_count(0)
        expect(tracking.locator("form[method=post]")).to_have_count(0)
        for title in ("Request summary", "Return timeline", "Products to return", "Replacement selection", "Price comparison", "Supporting photos", "Activity timeline"):
            expect(tracking.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(tracking.locator('[aria-current="step"]')).to_contain_text("Review request")
        expect(tracking.locator(".sales-case-money")).to_contain_text("PHP 100.00")
        assert tracking.evaluate("el => el.scrollWidth<=el.clientWidth+1")
        page.screenshot(path=str(output / f"sales-tracking-cards-{width}.png"), full_page=True)
        tracking.get_by_role("button", name="Close details", exact=True).click()
        expect(tracking).not_to_be_visible()
        expect(page).not_to_have_url(re.compile(r"[?&]case="))

        as_role("supervisor")
        page.goto(f"http://replacement.test/portal/sales-supervisor/returns?case={case}", wait_until="networkidle")
        workspace = page.locator("[data-return-details]")
        expect(workspace).to_be_visible()
        expect(workspace.locator(".rd-progress > li")).to_have_count(5)
        expect(page.locator("[data-return-dialog]")).to_have_count(0)
        expect(workspace.get_by_role("heading", name="Financial summary", exact=True)).to_be_visible()
        expect(workspace.locator('[data-return-panel="overview"] .rd-financial-total')).to_contain_text("PHP 0.00")
        # Tabs are keyboard accessible; evidence uses the original authorized URLs.
        page.get_by_role("tab", name="Overview", exact=True).focus()
        page.keyboard.press("ArrowRight")
        expect(page.get_by_role("tab", name="Review Decision", exact=True)).to_be_focused()
        page.get_by_role("tab", name="Overview", exact=True).click()
        workspace.locator('[data-return-photo]').first.click()
        expect(page.get_by_role("dialog", name="Supporting photo", exact=True)).to_be_visible()
        page.get_by_role("button", name="Close photo", exact=True).click()
        expect(page.get_by_role("dialog", name="Supporting photo", exact=True)).not_to_be_visible()
        assert workspace.evaluate("el => el.scrollWidth<=el.clientWidth+1")
        page.screenshot(path=str(output / f"supervisor-page-{width}.png"), full_page=True)
        page.get_by_role("tab", name="Review Decision", exact=True).click()
        page.locator("[data-return-conversation]").click()
        expect(page.locator("[data-replacement-conversation]")).to_be_visible()
        page.get_by_label("Message", exact=True).fill("I am reviewing your replacement request.")
        page.get_by_role("button", name="Send message", exact=True).click()
        expect(page.locator("[data-replacement-message-log]")).to_contain_text(
            "I am reviewing your replacement request."
        )
        page.screenshot(path=str(output / f"conversation-{width}.png"), full_page=True)
        page.get_by_role("link", name="Open return details").click()
        page.get_by_role("tab", name="Review Decision", exact=True).click()
        approve = page.locator('form[action$="/approve"]')
        approve.locator("[name=remarks]").fill("Supervisor approves the recorded requested products")
        page.get_by_role("button", name="Approve return request", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Approve return request?", exact=True)
        expect(confirmation).to_be_visible()
        confirmation.get_by_role("button", name="Cancel", exact=True).click()
        assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "submitted"
        page.get_by_role("button", name="Approve return request", exact=True).click()
        page.get_by_role("dialog", name="Approve return request?", exact=True).get_by_role("button", name="Confirm approval", exact=True).click()
        page.wait_for_load_state("networkidle")

        as_role("inventory")
        page.goto(f"http://replacement.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
        timeline = page.locator(".return-workflow")
        expect(timeline.get_by_role("heading", name="Return timeline")).to_be_visible()
        expect(timeline.locator('[aria-current="step"]')).to_have_attribute("data-workflow-step", "receipt")
        assert "payment" not in timeline.inner_text().lower() and "PHP" not in timeline.inner_text()
        assert timeline.evaluate("el => el.scrollWidth<=el.clientWidth+1")
        page.locator(".inventory-receipt-choice.is-complete > summary").click()
        page.locator("[name=proof_matches]").check()
        page.get_by_role("button", name="Confirm complete receipt", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("button", name="Prepare and mark ready")).to_have_count(0)
        assert "PHP" not in page.locator("body").inner_text()

        as_role("reseller")
        page.goto(f"http://replacement.test/portal/reseller/history?view=returns&case={case}", wait_until="networkidle")
        expect(page.locator('[data-workflow-step="payment_sent"]')).to_have_attribute("aria-current", "step")
        expect(page.locator(".return-next-step")).to_have_count(0)
        page.get_by_role("tab", name="Payment & Refunds", exact=True).click()
        payment = page.locator("[data-receipt-checkout]")
        expect(payment).to_be_visible()
        payment.locator("[name=payment_proofs]").set_input_files(
            {"name": "simulated-payment.png", "mimeType": "image/png", "buffer": photo["content"]}
        )
        expect(payment.locator("[name=receipt_scan_token]")).not_to_have_value("")
        expect(payment.locator("[data-receipt-confirm]")).to_be_enabled()
        payment.locator("[data-receipt-confirm]").check()
        payment.locator("[data-receipt-accept]").click()
        page.screenshot(path=str(output / f"payment-modal-{width}.png"), full_page=True)
        payment.get_by_role("button", name="Submit payment", exact=True).click()
        page.wait_for_load_state("networkidle")

        as_role("supervisor")
        page.goto(f"http://replacement.test/portal/sales-supervisor/returns?case={case}", wait_until="networkidle")
        page.get_by_role("tab", name="Settlement", exact=True).click()
        verify = page.locator('form[action$="/verify-payment"]')
        expect(verify).to_be_visible()
        verify.locator("[name=funds_received]").check()
        verify.locator("[name=remarks]").fill("Simulated test funds confirmed; no actual money sent")
        verify.get_by_role("button", name="Confirm funds received", exact=True).click()
        page.get_by_role("dialog", name="Confirm funds received?", exact=True).get_by_role("button", name="Confirm funds received", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert fetch_one("SELECT payment_verified_at FROM product_returns WHERE return_id=%s", (case,))[
            "payment_verified_at"
        ]

        as_role("inventory")
        page.goto(f"http://replacement.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
        page.get_by_role("button", name="Prepare and mark ready", exact=True).click()
        page.wait_for_load_state("networkidle")
        release = page.locator('form[action$="/release"]')
        expect(release).to_be_visible()
        page.screenshot(path=str(output / f"ready-{width}.png"), full_page=True)
        release.locator("[name=remarks]").fill("Simulated warehouse dispatch to delivery team")
        release.get_by_role("button", name="Confirm replacement released", exact=True).click()
        page.wait_for_load_state("networkidle")

        as_role("supervisor")
        page.goto(f"http://replacement.test/portal/sales-supervisor/returns?case={case}", wait_until="networkidle")
        page.get_by_role("tab", name="Inventory & Replacement", exact=True).click()
        delivered = page.locator('form[action$="/delivered"]')
        delivered.locator("[name=remarks]").fill("Reseller received the simulated replacement delivery")
        delivered.get_by_role("button", name="Confirm replacement delivered", exact=True).click()
        page.get_by_role("dialog", name="Confirm replacement delivered?", exact=True).get_by_role("button", name="Confirm delivery", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "completed"
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        assert not errors
        browser.close()
