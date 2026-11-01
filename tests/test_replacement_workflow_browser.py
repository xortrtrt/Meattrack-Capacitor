"""Exercise the new reseller -> Supervisor -> Inventory journey with synthetic records."""

from pathlib import Path
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
        page.locator(f"#return-quantity-{d['allocation']}").fill("2")
        page.locator("#return-replacement").check()
        page.get_by_label("Replacement packs for Substitute 300").fill("1")
        dialog.locator("[name=remarks]").fill("Synthetic damaged packs for replacement")
        photo = supporting_photo()
        dialog.locator("[name=evidence]").set_input_files(
            {"name": "simulated-damage.png", "mimeType": "image/png", "buffer": photo["content"]}
        )
        page.get_by_role("button", name="Review price comparison", exact=True).click()
        expect(dialog.locator("[name=comparison_token]")).not_to_have_value("")
        expect(dialog.locator("[data-replacement-comparison]")).to_contain_text("Extra payment")
        page.screenshot(path=str(output / f"comparison-{width}.png"), full_page=True)
        dialog.get_by_role("button", name="Request return", exact=False).click()
        page.wait_for_load_state("networkidle")
        case = fetch_one("SELECT return_id FROM product_returns ORDER BY return_id DESC LIMIT 1")["return_id"]
        expect(page.get_by_role("heading", name="Recorded replacement comparison")).to_be_visible()

        as_role("supervisor")
        page.goto(f"http://replacement.test/portal/sales-supervisor/returns?case={case}", wait_until="networkidle")
        page.get_by_role("link", name="Open replacement conversation", exact=True).click()
        expect(page.locator("[data-replacement-conversation]")).to_be_visible()
        page.get_by_label("Message", exact=True).fill("I am reviewing your replacement request.")
        page.get_by_role("button", name="Send message", exact=True).click()
        expect(page.locator("[data-replacement-message-log]")).to_contain_text(
            "I am reviewing your replacement request."
        )
        page.screenshot(path=str(output / f"conversation-{width}.png"), full_page=True)
        page.get_by_role("link", name="Open return details").click()
        approve = page.locator('form[action$="/approve"]')
        approve.locator("[name=remarks]").fill("Supervisor approves the recorded requested products")
        approve.get_by_role("button", name="Approve return request", exact=True).click()
        page.wait_for_load_state("networkidle")

        as_role("inventory")
        page.goto(f"http://replacement.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
        page.locator(".inventory-receipt-choice.is-complete > summary").click()
        page.locator("[name=proof_matches]").check()
        page.get_by_role("button", name="Confirm complete receipt", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("button", name="Prepare and mark ready")).to_have_count(0)
        assert "PHP" not in page.locator("body").inner_text()

        as_role("reseller")
        page.goto(f"http://replacement.test/portal/reseller/history?view=returns&case={case}", wait_until="networkidle")
        payment = page.locator("[data-replacement-payment]")
        expect(payment).to_be_visible()
        payment.locator("[name=payment_proofs]").set_input_files(
            {"name": "simulated-payment.png", "mimeType": "image/png", "buffer": photo["content"]}
        )
        expect(payment.locator("[name=receipt_scan_token]")).not_to_have_value("")
        payment.locator("[name=payment_details_confirmed]").check()
        page.screenshot(path=str(output / f"payment-modal-{width}.png"), full_page=True)
        payment.get_by_role("button", name="Submit additional payment", exact=True).click()
        page.wait_for_load_state("networkidle")

        as_role("supervisor")
        page.goto(f"http://replacement.test/portal/sales-supervisor/returns?case={case}", wait_until="networkidle")
        verify = page.locator('form[action$="/verify-payment"]')
        expect(verify).to_be_visible()
        verify.locator("[name=funds_received]").check()
        verify.locator("[name=remarks]").fill("Simulated test funds confirmed; no actual money sent")
        verify.get_by_role("button", name="Confirm additional payment", exact=True).click()
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
        delivered = page.locator('form[action$="/delivered"]')
        delivered.locator("[name=remarks]").fill("Reseller received the simulated replacement delivery")
        delivered.get_by_role("button", name="Confirm replacement delivered", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "completed"
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        assert not errors
        browser.close()
