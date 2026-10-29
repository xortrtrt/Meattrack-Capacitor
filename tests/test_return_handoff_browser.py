"""Exercise the connected portals and the existing monetary refund screen."""

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from app.database import fetch_one
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, receive  # noqa: F401
from test_return_workflow_handoff import approved, confirm

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_three_portals_continue_existing_refund_and_show_replacement_batches(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = approved(d)
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    output = Path("outputs/return-handoff")
    output.mkdir(parents=True, exist_ok=True)

    def sign_in(role):
        session.clear()
        session.update(account_id=d[role], role_key="reseller" if role == "reseller" else
                       "sales-supervisor" if role == "supervisor" else "team-leader",
                       team_leader_role="sales-supervisor" if role == "supervisor" else role,
                       portal_expires_at=int(business_now().timestamp()) + 600)

    with TestClient(main.app, base_url="http://handoff.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())

        def bridge(route):
            request = route.request
            parts = urlsplit(request.url)
            response = client.request(request.method, parts.path + ("?" + parts.query if parts.query else ""),
                content=request.post_data_buffer, headers={key: value for key, value in request.headers.items()
                    if key not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={key: value
                for key, value in response.headers.items() if key not in
                {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://handoff.test/**", bridge)
        sign_in("inventory")
        page.goto(f"http://handoff.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
        page.locator('.inventory-receipt-choice.is-complete > summary').click()
        page.get_by_label("I checked every approved product, quantity, batch, and supporting photo. Everything matches.").check()
        page.get_by_role("button", name="Confirm complete receipt", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert "PHP" not in page.locator("body").inner_text()
        sign_in("sales")
        page.goto(f"http://handoff.test/portal/team-leader/returns?case={case}", wait_until="networkidle")
        expect(page.get_by_role("heading", name="Return Review and Settlement", exact=True)).to_be_visible()
        expect(page.locator('form[action$="/proceed"]')).to_have_count(0)
        expect(page.get_by_role("dialog")).to_contain_text("Inventory confirmed a complete matching receipt")
        refund = fetch_one("SELECT order_refund_id FROM order_refunds WHERE return_id=%s", (case,))["order_refund_id"]
        sign_in("supervisor")
        page.goto(f"http://handoff.test/portal/sales-supervisor/refunds?refund={refund}", wait_until="networkidle")
        page.get_by_role("button", name="Start processing", exact=True).click()
        page.wait_for_load_state("networkidle")
        # The in-process bridge follows redirects internally; restore the
        # canonical URL before testing links/forms that use query-only hrefs.
        page.goto(f"http://handoff.test/portal/sales-supervisor/refunds?refund={refund}&pane=transfer", wait_until="networkidle")
        page.locator('input[name="refund_method"]').fill("SIMULATED bank transfer")
        page.locator('input[name="transaction_reference"]').fill("SIM" + uuid4().hex)
        page.locator('input[name="transferred_at"]').fill((business_now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"))
        page.locator('textarea[name="internal_note"]').fill("SIMULATED test transfer; no real funds were sent")
        proof = BytesIO()
        Image.new("RGB", (64, 64), (42, 140, 80)).save(proof, format="PNG")
        page.locator('input[name="proof"]').set_input_files({"name": "simulated-transfer.png", "mimeType": "image/png", "buffer": proof.getvalue()})
        page.get_by_role("button", name="Confirm refund completed", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert fetch_one("SELECT status FROM order_refunds WHERE order_refund_id=%s", (refund,))["status"] == "completed"

        replacement = approved(d, "replace")
        receive(d, replacement, authorize=False)
        confirm(d, replacement)
        returns.accept_replacement_quote(replacement, d["reseller"], 1)
        returns.reserve_replacement(replacement, d["inventory"], "Reserved eligible replacement delivery")
        returns.release_replacement(replacement, d["inventory"], "Handed over traced replacement packs")
        for role in ("inventory", "sales", "reseller"):
            sign_in(role)
            for width in (375, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                prefix = "reseller" if role == "reseller" else "team-leader"
                page.goto(f"http://handoff.test/portal/{prefix}/returns?case={replacement}", wait_until="networkidle")
                dialog = page.get_by_role("dialog", name=f"Return #{replacement}", exact=True)
                expect(dialog).to_be_visible()
                expect(dialog).to_contain_text("TEST-2")
                if role == "inventory":
                    assert "PHP" not in page.locator("body").inner_text()
                    expect(dialog).to_contain_text("Released batches—actual stock taken")
                elif role == "reseller":
                    expect(dialog).to_contain_text("Your replacement delivery batches")
                    assert "Holding #" not in dialog.inner_text() and "Original sales" not in page.locator("body").inner_text()
                else:
                    dialog.locator("summary", has_text="Approval and Inventory handoff").click()
                    expect(dialog).to_contain_text("Movement #")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
                page.screenshot(path=str(output / f"{role}-{width}.png"), full_page=True)
        assert not errors, errors
        browser.close()
