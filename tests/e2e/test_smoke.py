from __future__ import annotations

import base64
import os
import re
from urllib.parse import urljoin

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app.business_time import business_today


BASE_URL = os.getenv("E2E_BASE_URL")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not configured"),
]

SUPERVISOR_EMAIL = os.getenv("E2E_SUPERVISOR_EMAIL", "supervisor.e2e@meattrack.test")
SUPERVISOR_PASSWORD = os.getenv("E2E_SUPERVISOR_PASSWORD", "Meattrack-E2E-2026!")
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        yield page
        context.close()
        browser.close()


def test_supervisor_report_defaults_to_current_month_and_remains_editable(page: Page) -> None:
    today = business_today()

    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.get_by_label("Email").fill(SUPERVISOR_EMAIL)
    page.get_by_label("Password").fill(SUPERVISOR_PASSWORD)
    page.locator('input[name="consent"]').check()
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url("**/portal/sales-supervisor/dashboard")

    response = page.goto(f"{BASE_URL}/portal/sales-supervisor/reports", wait_until="networkidle")
    assert response is not None and response.ok

    start_input = page.locator('input[name="period_start"]')
    end_input = page.locator('input[name="period_end"]')
    expect(start_input).to_have_value(today.replace(day=1).isoformat())
    expect(end_input).to_have_value(today.isoformat())
    expect(start_input).to_be_editable()
    expect(end_input).to_be_editable()

    start_input.fill(today.isoformat())
    expect(start_input).to_have_value(today.isoformat())


def test_supervisor_reseller_orders_start_collapsed_and_open_on_request(page: Page) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.get_by_label("Email").fill(SUPERVISOR_EMAIL)
    page.get_by_label("Password").fill(SUPERVISOR_PASSWORD)
    page.locator('input[name="consent"]').check()
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url("**/portal/sales-supervisor/dashboard")

    def add_order_when_account_is_empty(route) -> None:
        response = route.fetch()
        body = response.text()
        if 'class="sales-order-card"' not in body:
            order_card = """
            <div class="sales-order-card-list">
              <details class="sales-order-card" id="order-99030">
                <summary class="sales-order-card-summary">
                  <span class="sales-order-number">Order #99030</span>
                  <span class="sales-order-chevron" aria-hidden="true"></span>
                </summary>
                <div class="sales-order-card-body">Order details</div>
              </details>
            </div>
            """
            body = re.sub(
                r'<div class="sales-order-empty-state">.*?</div>',
                order_card,
                body,
                count=1,
                flags=re.DOTALL,
            )
        route.fulfill(response=response, body=body)

    page.route("**/portal/sales-supervisor/orders", add_order_when_account_is_empty)
    response = page.goto(f"{BASE_URL}/portal/sales-supervisor/orders", wait_until="networkidle")
    assert response is not None and response.ok

    first_order = page.locator(".sales-order-card").first
    expect(first_order).to_be_attached()
    expect(first_order).not_to_have_attribute("open", "")
    first_order.locator("summary").click()
    expect(first_order).to_have_attribute("open", "")


def test_supervisor_report_preview_has_accessible_sections_and_downloads(page: Page) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.get_by_label("Email").fill(SUPERVISOR_EMAIL)
    page.get_by_label("Password").fill(SUPERVISOR_PASSWORD)
    page.locator('input[name="consent"]').check()
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url("**/portal/sales-supervisor/dashboard")
    response = page.goto(f"{BASE_URL}/portal/sales-supervisor/reports", wait_until="networkidle")
    assert response is not None and response.ok
    page.get_by_role("button", name="Generate preview").click()
    page.wait_for_url("**/reports?**preview=1")
    expect(page.get_by_role("tab")).to_have_count(4)
    expect(page.locator('[data-report-metric="retained_sales"]')).to_be_visible()
    page.get_by_role("tab").first.focus()
    page.keyboard.press("End")
    expect(page.get_by_role("tab").last).to_have_attribute("aria-selected", "true")
    expect(page.locator('[data-report-panel="refunds"]')).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="Excel", exact=True).click()
    assert download.value.suggested_filename.endswith(".xlsx")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    expect(page.get_by_role("button", name="Submit to Owner", exact=True)).to_be_visible()


def test_public_and_supervisor_profile_smoke(page: Page) -> None:
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: console_errors.append(str(error)))
    page.on(
        "response",
        lambda response: console_errors.append(f"HTTP {response.status} {response.url}")
        if response.status >= 400
        else None,
    )

    response = page.goto(f"{BASE_URL}/", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator("body")).to_have_class(re.compile(r"\blanding-page\b"))
    expect(page.locator('script[src*="/static/js/public-site.js"]')).to_have_count(1)

    page.locator("[data-landing-product-open]").first.click()
    expect(page.locator("[data-landing-product-modal]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("[data-landing-product-modal]")).to_be_hidden()

    page.locator(".chatbot-toggle").click()
    chat_input = page.locator("[data-chatbot-form] input[name='message']")
    chat_input.fill("Hello")
    page.locator("[data-chatbot-form]").get_by_role("button", name="Send message").click()
    expect(page.locator("[data-chatbot-messages] .bot-message").last).to_contain_text(
        "Batangas Premium"
    )

    footer_text = page.locator(".landing-footer p")
    footer_text.scroll_into_view_if_needed()
    desktop_offset = footer_text.evaluate(
        "element => { const box = element.getBoundingClientRect(); "
        "return box.left + box.width / 2 - window.innerWidth / 2; }"
    )
    assert abs(desktop_offset) < 2

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="networkidle")
    mobile_menu = page.locator("[data-mobile-nav-toggle]")
    mobile_menu.click()
    expect(mobile_menu).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(mobile_menu).to_have_attribute("aria-expanded", "false")
    footer_text = page.locator(".landing-footer p")
    footer_text.scroll_into_view_if_needed()
    mobile_offset = footer_text.evaluate(
        "element => { const box = element.getBoundingClientRect(); "
        "return box.left + box.width / 2 - window.innerWidth / 2; }"
    )
    assert abs(mobile_offset) < 2
    page.set_viewport_size({"width": 1280, "height": 720})

    response = page.goto(f"{BASE_URL}/products", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator('link[href*="/static/css/"]').first).to_be_attached()

    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.get_by_label("Email").fill(SUPERVISOR_EMAIL)
    page.get_by_label("Password").fill(SUPERVISOR_PASSWORD)
    page.locator('input[name="consent"]').check()
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url("**/portal/sales-supervisor/dashboard")
    expect(page.locator("body")).to_have_class(re.compile(r"\brole-sales-supervisor\b"))

    response = page.goto(f"{BASE_URL}/portal/sales-supervisor/accounts", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="Account lifecycle")).to_be_visible()

    page.goto(f"{BASE_URL}/portal/sales-supervisor/profile", wait_until="networkidle")
    page.locator("[data-profile-picture-open]").click()
    file_input = page.locator("[data-profile-picture-file]")
    file_input.set_input_files(
        {"name": "profile.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    page.locator("[data-profile-picture-x]").evaluate("element => element.value = '37'")
    page.locator("[data-profile-picture-y]").evaluate("element => element.value = '62'")
    page.locator("[data-profile-picture-scale]").evaluate("element => element.value = '1.4'")
    page.locator(".profile-picture-editor-form").get_by_role("button", name="Save photo").click()
    page.wait_for_url("**/portal/sales-supervisor/profile?message=*")

    profile_image = page.locator(".profile-picture-preview img")
    expect(profile_image).to_be_visible()
    image_url = profile_image.get_attribute("src")
    assert image_url and "/static/uploads/profiles/" in image_url
    image_response = page.request.get(urljoin(BASE_URL, image_url))
    assert image_response.ok

    page.reload(wait_until="networkidle")
    expect(page.locator(".profile-picture-preview img")).to_have_attribute("src", image_url)
    preview_style = page.locator(".profile-picture-preview").get_attribute("style") or ""
    assert "--profile-x: 37" in preview_style
    assert "--profile-y: 62" in preview_style
    assert "--profile-scale: 1.4" in preview_style
    assert console_errors == []
