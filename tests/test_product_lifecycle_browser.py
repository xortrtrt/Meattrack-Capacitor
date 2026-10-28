"""Browser exercises use the actual app against an isolated PostgreSQL schema."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, repositories, web_sessions, worker
from app.business_time import business_now, business_today
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import create_case, lifecycle, receive, complete_refund, supporting_photo  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_return_forms_and_portal_layouts_in_browser(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    current_session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(current_session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, *args: current_session.update(data))
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    output = Path("outputs/lifecycle-design")
    output.mkdir(parents=True, exist_ok=True)

    def as_role(actor, role, specialization=None):
        current_session.clear()
        current_session.update(
            account_id=actor,
            role_key=role,
            team_leader_role=specialization,
            portal_expires_at=int(business_now().timestamp()) + 600,
        )

    with TestClient(main.app, base_url="http://lifecycle.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        network = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("requestfailed", lambda req: network.append((req.url, req.failure)))

        def handle(route):
            request = route.request
            parts = urlsplit(request.url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            headers = {
                key: value for key, value in request.headers.items() if key not in {"host", "content-length", "cookie"}
            }
            # Resolve redirects inside the in-process bridge; Chromium cannot
            # resolve the synthetic host used for this isolated database test.
            response = client.request(
                request.method, path, content=request.post_data_buffer, headers=headers, follow_redirects=True
            )
            if request.is_navigation_request():
                network.append(
                    (
                        request.method,
                        path,
                        response.status_code,
                        response.headers.get("location"),
                        response.text[:250] if response.status_code >= 400 else "",
                    )
                )
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
            }
            route.fulfill(status=response.status_code, headers=response_headers, body=response.content)

        page.route("http://lifecycle.test/**", handle)
        as_role(d["reseller"], "reseller")
        page.goto(f"http://lifecycle.test/portal/reseller/returns?order={d['order']}")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Request return", exact=True).click()
        expect(page.locator('textarea[name="remarks"]:invalid')).to_be_visible()
        page.get_by_label("Return quantity for Test Product batch TEST-1").fill("2")
        page.get_by_role("radio", name="Replacement", exact=True).check()
        page.get_by_label("Reason and remarks").fill("Expired two packs from original delivered batch")
        photo = supporting_photo()
        page.get_by_label("Supporting photos").set_input_files({"name": photo["filename"], "mimeType": photo["content_type"], "buffer": photo["content"]})
        page.get_by_role("button", name="Request return", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(output / "request-result.png"), full_page=True)
        assert page.get_by_role("heading", name="Return #1", exact=True).count(), (
            f"{page.url}\n{page.locator('body').inner_text()[:5000]}\n{errors}\n{network}"
        )
        expect(page.get_by_role("heading", name="Return #1", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "reseller-return-390.png"), full_page=True)

        as_role(d["sales"], "team-leader", "sales")
        page.goto("http://lifecycle.test/portal/team-leader/returns")
        page.wait_for_load_state("networkidle")
        expect(page.locator("[data-return-dialog]")).to_have_count(0)
        page.locator('[data-return-open="1"]').focus()
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("dialog", name="Return #1", exact=True)).to_be_visible()
        expect(page.locator(".return-request-step")).to_have_count(0)
        expect(page.locator(".sales-return-photo-grid img")).to_have_count(1)
        expect(page.locator(".sales-return-substitutions")).not_to_have_attribute("open", "")
        expect(page.locator(".sales-return-receipts")).not_to_have_attribute("open", "")
        page.screenshot(path=str(output / "sales-request-review-390.png"), full_page=True)
        page.get_by_label("Review remarks", exact=True).fill("Approved after checking expired batch details")
        page.get_by_role("button", name="Approve return request", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".lifecycle-case-detail .status-badge").first).to_have_text("Awaiting Receipt")
        page.screenshot(path=str(output / "sales-review-390.png"), full_page=True)

        as_role(d["inventory"], "team-leader", "inventory")
        page.goto("http://lifecycle.test/portal/team-leader/returns?case=1")
        page.wait_for_load_state("networkidle")
        receipt = page.locator('form[action$="/receive"]')
        receipt.locator('input[name="quantity"]').fill("2")
        receipt.get_by_label("Non-sellable holding location").fill("Expired freezer A")
        receipt.get_by_label("Receipt remarks").fill("Two expired packs physically received and checked")
        receipt.get_by_role("button", name="Record returned items received").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".lifecycle-case-detail .status-badge").first).to_have_text("Awaiting Inventory Verification")
        verification = page.locator('form[action$="/verify"]')
        verification.get_by_label("Verification remarks").fill("Final receipt and holding quantities checked")
        verification.get_by_role("button",name="Verify returned goods",exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".lifecycle-case-detail .status-badge").first).to_have_text("Awaiting Replacement Release")

        worker.scan_batch_expiry()
        # Give the supervisor a real assigned financial case to inspect.
        refund_case = create_case(d)
        returns.review_product_return(refund_case, d["sales"], True, "Approved additional expired return")
        receive(d, refund_case)
        snapshots = [
            ("reseller", d["reseller"], "reseller", None, "/portal/reseller/returns?case=1"),
            ("sales", d["sales"], "team-leader", "sales", "/portal/team-leader/returns?case=1"),
            ("inventory", d["inventory"], "team-leader", "inventory", "/portal/team-leader/expired-stock"),
            (
                "product-details",
                d["inventory"],
                "team-leader",
                "inventory",
                f"/portal/team-leader/product-details?product_id={d['product']}",
            ),
            (
                "supervisor",
                d["supervisor"],
                "sales-supervisor",
                "sales-supervisor",
                f"/portal/sales-supervisor/returns?case={refund_case}",
            ),
            ("owner", d["owner"], "owner", None, "/portal/owner/returns?case=1"),
        ]
        for label, actor, role, specialization, path in snapshots:
            as_role(actor, role, specialization)
            for width in (375, 390, 768, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto("http://lifecycle.test" + path)
                page.wait_for_load_state("networkidle")
                expect(page.locator(".lifecycle-workspace")).to_be_visible()
                if label in {"reseller", "sales", "owner", "supervisor"}:
                    dialog = page.locator("[data-return-dialog]")
                    expect(dialog).to_be_visible()
                    assert dialog.evaluate("el => el.matches(':modal')")
                    assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                    close = page.get_by_role("button", name="Close return details", exact=True)
                    expect(close).to_be_focused()
                    page.keyboard.press("Shift+Tab")
                    assert dialog.evaluate("el => el.contains(document.activeElement)")
                    page.keyboard.press("Escape")
                    expect(dialog).not_to_be_visible()
                    expect(page).not_to_have_url(re.compile(r"[?&]case="))
                    selected = page.locator(f'[data-return-open="{refund_case if label == "supervisor" else 1}"]')
                    expect(selected).to_be_focused()
                    if label in {"reseller", "sales"}:
                        expect(page.locator(".lifecycle-financial-details")).to_have_count(0)
                        if label == "reseller":
                            assert "Original sales" not in page.locator("body").inner_text()
                    else:
                        breakdown = page.locator(".lifecycle-financial-details summary")
                        breakdown.focus()
                        page.keyboard.press("Enter")
                        expect(page.locator(".lifecycle-financial-details")).to_have_attribute("open", "")
                        page.keyboard.press("Enter")
                        breakdown.blur()
                    selected.click()
                    expect(dialog).to_be_visible()
                    close.click()
                    expect(dialog).not_to_be_visible()
                    expect(page).not_to_have_url(re.compile(r"[?&]case="))
                    selected.click()
                    expect(dialog).to_be_visible()
                    page.mouse.click(1, 1)
                    expect(dialog).not_to_be_visible()
                    expect(page).not_to_have_url(re.compile(r"[?&]case="))
                    selected.click()
                    expect(dialog).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"), (
                    f"Horizontal overflow: {label} at {width}"
                )
                page.screenshot(path=str(output / f"{label}-{width}.png"), full_page=True)
        as_role(d["owner"], "owner")
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto("http://lifecycle.test/portal/owner/returns?status=rejected")
            page.wait_for_load_state("networkidle")
            expect(page.get_by_text("No return cases", exact=True)).to_be_visible()
            expect(page.locator("[data-return-dialog]")).to_have_count(0)
            page.screenshot(path=str(output / f"returns-empty-{width}.png"), full_page=True)
        as_role(d["inventory"], "team-leader", "inventory")
        page.goto("http://lifecycle.test/portal/team-leader/finished-products")
        page.wait_for_load_state("networkidle")
        page.get_by_role("link", name="Test Product", exact=False).last.click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Batches and expiration", exact=True)).to_be_visible()
        for number in range(4):
            repositories.add_product_batch(
                d["product"], f"BROWSER-PAGE-{number}", 4, business_today() + timedelta(days=20 + number)
            )
        for role, actor, specialization in (
            ("team-leader", d["inventory"], "inventory"),
            ("owner", d["owner"], None),
        ):
            as_role(actor, role, specialization)
            for width in (375, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"http://lifecycle.test/portal/{role}/product-details?product_id={d['product']}")
                page.wait_for_load_state("networkidle")
                batch_pages = page.get_by_role("navigation", name="Batches pages", exact=True)
                movement_pages = page.get_by_role("navigation", name="Movements pages", exact=True)
                warehouse = page.locator(".lifecycle-metric").filter(has_text="Warehouse stock").inner_text()
                expect(page.locator(".lifecycle-batch-card")).to_have_count(3)
                batch_pages.get_by_role("link", name="Next batches page", exact=True).focus()
                page.keyboard.press("Enter")
                expect(page).to_have_url(re.compile(r"batch_page=2"))
                page.wait_for_load_state("networkidle")
                assert "batch_page=2" in page.url and "product_id=" + str(d["product"]) in page.url
                expect(batch_pages.get_by_text("Page 2 of 2", exact=True)).to_be_visible()
                movement_pages.get_by_role("link", name="Next movements page", exact=True).click()
                expect(page).to_have_url(re.compile(r"movement_page=2"))
                page.wait_for_load_state("networkidle")
                assert "batch_page=2" in page.url and "movement_page=2" in page.url
                expect(batch_pages.get_by_text("Page 2 of 2", exact=True)).to_be_visible()
                expect(movement_pages.get_by_text("Page 2 of 2", exact=True)).to_be_visible()
                assert page.locator(".lifecycle-metric").filter(has_text="Warehouse stock").inner_text() == warehouse
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
                page.screenshot(path=str(output / f"product-pagination-{role}-{width}.png"), full_page=True)
                batch_pages.get_by_role("link", name="Previous batches page", exact=True).click()
                expect(page).to_have_url(re.compile(r"batch_page=1"))
                page.wait_for_load_state("networkidle")
                assert "batch_page=1" in page.url and "movement_page=2" in page.url
                movement_pages.get_by_role("link", name="Previous movements page", exact=True).click()
                expect(page).to_have_url(re.compile(r"movement_page=1"))
                page.wait_for_load_state("networkidle")
                expect(movement_pages.get_by_text("Page 1 of 2", exact=True)).to_be_visible()
            page.goto(
                f"http://lifecycle.test/portal/{role}/product-details?product_id={d['product']}&batch_page=bad&movement_page=-1"
            )
            page.wait_for_load_state("networkidle")
            expect(
                page.get_by_role("navigation", name="Batches pages").get_by_text("Page 1 of 2", exact=True)
            ).to_be_visible()
        refund_id=returns.get_product_return(refund_case,d["sales"])["refund"]["order_refund_id"]
        complete_refund(d,refund_case)
        as_role(d["sales"],"team-leader","sales")
        for width in (375,390,768,1440):
            page.set_viewport_size({"width":width,"height":900})
            page.goto(f"http://lifecycle.test/portal/team-leader/refunds?refund={refund_id}")
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("heading",name="Sales Refunds",exact=True,level=2)).to_be_visible()
            expect(page.get_by_text("View refund screenshot",exact=True)).to_be_visible()
            expect(page.get_by_role("button",name="Start processing",exact=True)).to_have_count(0)
            expect(page.locator('textarea[name="message"]')).to_have_count(0)
            assert "Simulated transfer confirmed against ledger" not in page.locator('body').inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
            page.screenshot(path=str(output/f"sales-refunds-{width}.png"),full_page=True)
        assert errors == [], errors
        browser.close()
