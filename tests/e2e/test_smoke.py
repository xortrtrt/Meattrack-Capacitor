from __future__ import annotations

import base64
import os
import re
from urllib.parse import urljoin

import pytest
from playwright.sync_api import Page, expect, sync_playwright


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


def test_public_and_supervisor_profile_smoke(page: Page) -> None:
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: console_errors.append(str(error)))

    response = page.goto(f"{BASE_URL}/", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator("body")).to_have_class(re.compile(r"\blanding-page\b"))
    expect(page.locator('script[src*="/static/js/public-site.js"]')).to_have_count(1)

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
