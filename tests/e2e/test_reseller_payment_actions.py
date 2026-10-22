from datetime import timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from app import main  # noqa: F401 -- registers the application's template filters
from app.business_time import business_now
from app.repositories.orders import RESELLER_ORDER_CANCELLATION_REASONS
from app.web import templates


def receipt_page():
    form = templates.env.get_template("portals/reseller/_receipt_payment_form.html").render(
        order={"order_id": 91, "payment_due_at": business_now() + timedelta(minutes=15)},
    )
    styles = "\n".join(Path(path).read_text(encoding="utf-8") for path in [
        "app/static/css/portal_base.css", "app/static/css/portals/reseller.css",
    ])
    return f'''<!doctype html><html><head><style>{styles}</style></head>
        <body class="role-reseller"><details class="order-history-card" open>
        <summary>Order #91</summary><aside class="order-card-actions">{form}</aside></details></body></html>'''


@pytest.mark.parametrize("rejected_first", [False, True])
def test_receipt_confirmation_submits_selected_file_and_payment_details(rejected_first):
    html = receipt_page()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("http://meattrack.test/history", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.route("**/receipt-scan", lambda route: route.fulfill(json={
            "ok": True, "scan_token": "signed-token", "transaction_number": "TXN123456",
            "payment_amount": "500.00", "payment_date": "2026-10-15", "payment_time": "10:30",
        }))
        submissions = []

        def submit_payment(route):
            submissions.append(route.request.post_data_buffer)
            if rejected_first and len(submissions) == 1:
                route.fulfill(status=400, json={
                    "ok": False, "error": "This payment transaction number has already been used for another order.",
                })
            else:
                route.fulfill(json={"ok": True, "redirect_url": "/pending"})

        page.route("**/91/payment", submit_payment)
        page.route("http://meattrack.test/pending", lambda route: route.fulfill(body="Payment submitted"))
        page.goto("http://meattrack.test/history", wait_until="networkidle")
        page.add_script_tag(content=Path("app/static/js/core.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=Path("app/static/js/forms.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=Path("app/static/js/portal.js").read_text(encoding="utf-8"))
        page.locator("[data-receipt-files]").set_input_files({
            "name": "receipt.png", "mimeType": "image/png", "buffer": b"receipt-content",
        })
        expect(page.locator("[data-receipt-confirm]")).to_be_enabled()
        page.locator("[data-receipt-confirm]").check()
        page.locator("[data-receipt-accept]").click()
        expect(page.locator("[data-receipt-modal]")).to_be_hidden()
        expect(page.locator("[data-receipt-submit]")).to_be_enabled()
        page.locator("[data-receipt-submit]").click()
        if rejected_first:
            expect(page.locator("[data-receipt-card-status]")).to_contain_text("already been used")
            expect(page.locator("[data-receipt-submit]")).to_be_enabled()
            expect(page.locator("[data-receipt-submit]")).to_have_css("pointer-events", "auto", timeout=1500)
            assert page.locator("[data-receipt-files]").evaluate("input => input.files[0].name") == "receipt.png"
            expect(page.locator("[data-receipt-token]")).to_have_value("signed-token")
            expect(page.locator("[data-receipt-confirm]")).to_be_checked()
            page.locator("[data-receipt-edit]").click()
            page.locator("[data-receipt-transaction]").fill("CORRECTED123456")
            page.locator("[data-receipt-confirm]").check()
            page.locator("[data-receipt-accept]").click()
            page.locator("[data-receipt-submit]").click()
        page.wait_for_url("**/pending")
        assert len(submissions) == (2 if rejected_first else 1)
        for body in submissions:
            assert b'filename="receipt.png"' in body
            assert b"receipt-content" in body
            for value in [b"signed-token", b"500.00", b"2026-10-15", b"10:30", b"yes"]:
                assert value in body
        assert (b"CORRECTED123456" if rejected_first else b"TXN123456") in submissions[-1]
        browser.close()


@pytest.mark.parametrize("scan_state", ["complete", "incomplete", "failed", "restored_file"])
def test_unconfirmed_receipt_can_be_reopened_and_confirmed_without_reuploading(scan_state):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        scans = []

        def scan_receipt(route):
            scans.append(route.request)
            if scan_state == "failed" and len(scans) == 1:
                route.fulfill(status=400, json={"ok": False, "error": "Please try reading the receipt again."})
            else:
                route.fulfill(json={
                    "ok": True, "scan_token": "signed-token", "transaction_number": "TXN123456",
                    "payment_amount": "500.00", "payment_date": "2026-10-15",
                    "payment_time": "" if scan_state == "incomplete" else "10:30",
                })

        page.route("http://meattrack.test/history", lambda route: route.fulfill(body=receipt_page(), content_type="text/html"))
        page.route("**/receipt-scan", scan_receipt)
        page.goto("http://meattrack.test/history", wait_until="networkidle")
        script = Path("app/static/js/portal.js").read_text(encoding="utf-8")
        if scan_state != "restored_file":
            page.add_script_tag(content=script)
        page.locator("[data-receipt-files]").set_input_files({
            "name": "receipt.png", "mimeType": "image/png", "buffer": b"receipt-content",
        })
        if scan_state == "restored_file":
            page.add_script_tag(content=script)
        if scan_state == "failed":
            expect(page.locator("[data-receipt-status]")).to_contain_text("try reading")
        else:
            expect(page.locator("[data-receipt-confirm]")).to_be_enabled()
        page.get_by_role("button", name="Review later", exact=True).click()
        expect(page.locator("[data-receipt-submit]")).to_be_disabled()
        review = page.locator("[data-receipt-review]")
        expect(review).to_be_visible()
        expect(review).to_be_enabled()
        review.click()
        expect(page.locator("[data-receipt-confirm]")).to_be_enabled()
        if scan_state == "incomplete":
            page.locator("[data-receipt-time]").fill("10:30")
        page.locator("[data-receipt-confirm]").check()
        page.locator("[data-receipt-accept]").click()
        expect(page.locator("[data-receipt-modal]")).to_be_hidden()
        expect(page.locator("[data-receipt-submit]")).to_be_enabled()
        assert page.locator("[data-receipt-files]").evaluate("input => input.files[0].name") == "receipt.png"
        assert len(scans) == (2 if scan_state == "failed" else 1)
        browser.close()


def test_cancellation_modal_works_after_filtering_order_history():
    action = templates.env.get_template("portals/reseller/_order_cancel_action.html").render(
        order={"order_id": 91, "status": "to_pay"},
        cancellation_reasons=RESELLER_ORDER_CANCELLATION_REASONS,
    )
    html = f"""<!doctype html><html><body>
        <nav class="order-history-tabs" data-async-filter-target="[data-async-filter-results]">
          <a href="?view=to_pay">To pay</a>
        </nav>
        <div data-async-filter-results>{action}</div>
    </body></html>"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("http://meattrack.test/history*", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.route("**/91/cancel", lambda route: route.fulfill(body="Cancelled"))
        page.goto("http://meattrack.test/history", wait_until="networkidle")
        page.add_script_tag(content=Path("app/static/js/portal.js").read_text(encoding="utf-8"))
        page.get_by_role("link", name="To pay").click()
        page.wait_for_url("**/history?view=to_pay")
        page.locator("[data-order-cancel-open]").click()
        expect(page.locator("[data-order-cancel-modal]")).to_be_visible()
        page.locator("[data-order-cancel-reason]").select_option("ordered_by_mistake")
        with page.expect_request("**/91/cancel") as submitted:
            page.get_by_role("button", name="Confirm cancellation").click()
        assert "cancellation_reason=ordered_by_mistake" in submitted.value.post_data
        browser.close()
