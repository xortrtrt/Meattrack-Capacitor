from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from app import portal_config, web_sessions
from app.business_time import business_now
from app.config import SESSION_COOKIE_NAME
from app.database import fetch_one


BASE_URL = os.getenv("E2E_BASE_URL", "")
pytestmark = [pytest.mark.e2e, pytest.mark.postgres, pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not configured")]


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_owner_navigation_and_workspace_layouts(width):
    owner = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'owner' AND is_active LIMIT 1")
    if not owner:
        pytest.skip("An active owner is required")
    token = web_sessions._new_token()
    web_sessions._persist_session(token, {
        "account_id": owner["account_id"], "role_key": "owner",
        "portal_expires_at": int(business_now().timestamp()) + 600,
    }, 600)
    screenshots = os.getenv("E2E_SCREENSHOT_DIR")
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": width, "height": 1000})
            context.add_cookies([{"name": SESSION_COOKIE_NAME, "value": token, "url": BASE_URL}])
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            selected_sections = set(filter(None, os.getenv("E2E_OWNER_SECTIONS", "").split(",")))
            for section, _label, _icon in portal_config.portal_nav["owner"]:
                if selected_sections and section not in selected_sections:
                    continue
                response = page.goto(f"{BASE_URL}/portal/owner/{section}", wait_until="networkidle")
                assert response and response.status == 200, f"{section}: {response.status if response else 'No response'}"
                assert page.locator(".portal-main").count() == 1, section
                assert page.locator("body.role-owner").count() == 1, section
                if section == "dashboard" and width > 860:
                    sidebar = page.locator(".portal-sidebar")
                    toolbar = sidebar.locator(":scope > .portal-topbar")
                    assert toolbar.count() == 1
                    before = toolbar.bounding_box()["y"]
                    sidebar.evaluate("el => el.scrollTop = 120")
                    assert toolbar.bounding_box()["y"] < before - 50
                    sidebar.evaluate("el => el.scrollTop = 0")
                if section == "orders":
                    assert page.locator(".owner-order-card").count() <= 6
                    assert page.locator("dialog[open]").count() == 0
                    if page.locator(".owner-order-card").count():
                        opener = page.locator(".owner-order-card-summary").first
                        row_height = page.locator(".owner-order-card").first.bounding_box()["height"]
                        opener.click()
                        dialog = page.locator("dialog[open]")
                        expect(dialog).to_be_visible()
                        dialog.evaluate("el => Promise.allSettled(el.getAnimations().map(animation => animation.finished))")
                        assert dialog.locator(".owner-order-product-list").is_visible()
                        assert page.locator(".owner-order-card").first.bounding_box()["height"] == row_height
                        page.keyboard.press("Tab")
                        assert page.evaluate("document.activeElement.closest('dialog') !== null")
                        if screenshots:
                            target = Path(screenshots)
                            target.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(target / f"owner-orders-modal-{width}.png"))
                        page.keyboard.press("Escape")
                        expect(dialog).to_have_count(0)
                        expect(opener).to_be_focused()
                        opener.click()
                        page.get_by_role("button", name="Close order details").click()
                        expect(page.locator("dialog[open]")).to_have_count(0)
                        opener.click()
                        page.mouse.click(2, 2)
                        expect(page.locator("dialog[open]")).to_have_count(0)
                        page.emulate_media(reduced_motion="reduce")
                        opener.click()
                        assert page.locator("dialog[open]").evaluate("el => getComputedStyle(el).animationName") == "none"
                        page.keyboard.press("Escape")
                        expect(page.locator("dialog[open]")).to_have_count(0)
                        page.emulate_media(reduced_motion="no-preference")
                        page.get_by_label("Sort orders", exact=True).select_option("oldest")
                        page.wait_for_url("**sort=oldest**")
                        page.locator(".owner-order-card-summary").first.click()
                        expect(page.locator("dialog[open]")).to_be_visible()
                        page.keyboard.press("Escape")
                        page.get_by_role("link", name="Clear filters", exact=True).click()
                        page.wait_for_load_state("networkidle")
                if section == "products" and page.locator('input[name="base_price"]').count():
                    price = page.locator('input[name="base_price"]').first
                    original_price = price.input_value()
                    price.fill("129.75")
                    assert price.evaluate("el => el.validity.valid")
                    price.fill("129.755")
                    assert price.evaluate("el => el.validity.stepMismatch")
                    price.fill(original_price)
                overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                if overflow:
                    offenders = page.evaluate("""() => [...document.querySelectorAll('main, main *')]
                        .filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                        .slice(0, 12).map(el => ({tag: el.tagName, class: el.className,
                            right: el.getBoundingClientRect().right}))""")
                    pytest.fail(f"{section} overflows at {width}px: {offenders}")
                if section == "refunds" and page.locator(".refund-case").count():
                    page.locator(".refund-case").first.click()
                    page.wait_for_load_state("networkidle")
                    assert page.locator(".refund-pane-tabs").is_visible()
                if section == "accounts":
                    expect(page.get_by_role("heading", name="Reseller team assignments", exact=True)).to_have_count(0)
                    create_button = page.locator('[data-owner-modal-open="owner-create-account"]')
                    create_button.click()
                    dialog = page.locator("dialog[open]")
                    expect(dialog).to_be_visible()
                    expect(dialog.get_by_label("Email", exact=True)).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(dialog).to_have_count(0)
                    expect(create_button).to_be_focused()
                    manage = page.locator('.owner-user-row [data-owner-modal-open]').first
                    if manage.count():
                        manage.click()
                        expect(page.locator("dialog[open]")).to_be_visible()
                        for summary in page.locator("dialog[open] .owner-account-admin > summary").all():
                            summary.click()
                        if screenshots:
                            target = Path(screenshots)
                            target.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(target / f"owner-account-modal-{width}.png"))
                        page.get_by_role("button", name="Close account details").click()
                        expect(page.locator("dialog[open]")).to_have_count(0)
                        expect(manage).to_be_focused()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                if section == "reports" and page.locator(".owner-report-summary").count():
                    opener = page.locator(".owner-report-summary").first
                    opener.click()
                    dialog = page.locator("dialog[open]")
                    expect(dialog).to_be_visible()
                    expect(dialog.get_by_role("heading", name="Reported products", exact=True)).to_be_visible()
                    page.keyboard.press("Tab")
                    assert page.evaluate("document.activeElement.closest('dialog') !== null")
                    page.keyboard.press("Escape")
                    expect(dialog).to_have_count(0)
                    expect(opener).to_be_focused()
                    opener.click()
                    page.get_by_role("button", name="Close report details").click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    opener.click()
                    page.mouse.click(2, 2)
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    page.emulate_media(reduced_motion="reduce")
                    opener.click()
                    assert page.locator("dialog[open]").evaluate("el => getComputedStyle(el).animationName") == "none"
                    if screenshots:
                        target = Path(screenshots)
                        target.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(target / f"owner-report-modal-{width}.png"))
                    page.keyboard.press("Escape")
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    page.emulate_media(reduced_motion="no-preference")
                if section == "resellers":
                    expect(page.locator(".owner-resellers-hero")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), section
                    if page.locator(".owner-reseller-assignment").count():
                        page.locator(".owner-reseller-assignment summary").first.click()
                        expect(page.locator(".owner-reseller-assignment form").first).to_be_visible()
                        expect(page.locator(".owner-reseller-assignment input[name='reason']").first).to_be_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), section
                    page.get_by_label("Search resellers", exact=True).fill("__no_matching_reseller__")
                    page.get_by_role("button", name="Find resellers", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    expect(page.locator(".owner-reseller-card")).to_have_count(0)
                    expect(page.get_by_text("No reseller accounts found", exact=True)).to_be_visible()
                    page.get_by_role("link", name="Clear filters", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    assert page.locator(".owner-reseller-card").count() <= 10
                if screenshots and section in {"dashboard", "operations", "orders", "materials", "refunds", "inventory", "products", "batches", "movements", "accounts", "resellers", "forecasts", "profile", "settings"}:
                    target = Path(screenshots)
                    target.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(target / f"owner-{section}-{width}.png"), full_page=True)
            context.close()
            browser.close()
        assert not errors, errors
    finally:
        web_sessions._delete_session(token)
