"""Login validation must finish before shared loading feedback blocks clicks."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import expect, sync_playwright


@pytest.mark.e2e
@pytest.mark.parametrize("width", [1440, 390])
def test_otp_validation_and_rejected_code_allow_retry(width):
    root = Path(__file__).resolve().parents[1]
    env = Environment(loader=FileSystemLoader(root / "app/templates"), autoescape=select_autoescape())
    env.filters["media_url"] = lambda value: f"/media/{value}"
    env.globals.update(csrf_token=lambda request: "test-token", url_for=lambda name, path: f"/static/{path}")

    def render(error=None):
        return env.get_template("login.html").render(
            request=None, login_otp_pending=True, pending_login_email="retry@example.test",
            otp_resend_after_seconds=60, error=error,
        )

    posts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            path = urlsplit(route.request.url).path
            if path.startswith("/static/"):
                asset = root / "app" / path.lstrip("/")
                if asset.is_file():
                    route.fulfill(path=str(asset))
                else:
                    route.fulfill(status=404)
            elif route.request.method == "POST":
                posts.append(route.request.post_data)
                route.fulfill(content_type="text/html", body=render("Incorrect OTP. Please try again."))
            elif path == "/login":
                route.fulfill(content_type="text/html", body=render())
            else:
                route.fulfill(status=204)

        page.route("**/*", serve)
        page.goto("http://login.test/login", wait_until="networkidle")
        otp = page.locator("#login-otp")
        button = page.get_by_role("button", name="Verify OTP", exact=True)
        for value in ["", "123", "abcdef"]:
            otp.fill(value)
            button.click()
            expect(otp).to_have_attribute("aria-invalid", "true")
            expect(otp).to_be_focused()
            assert button.evaluate("el => getComputedStyle(el).pointerEvents") != "none"
            assert "is-loading" not in (button.get_attribute("class") or "")
            assert posts == []
        otp.fill("111111")
        button.click()
        expect(page.get_by_role("alert")).to_contain_text("Incorrect OTP")
        otp.fill("222222")
        button.focus()
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        assert len(posts) == 2
        assert "otp_code=222222" in posts[-1]
        assert errors == []
        browser.close()
