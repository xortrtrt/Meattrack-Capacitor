"""Product photo form in Product Details."""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_inventory_tools import photo
from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_product_photo_update_from_details(lifecycle, monkeypatch):
    data = lifecycle
    session = dict(
        account_id=data["inventory"], role_key="team-leader", team_leader_role="inventory",
        portal_expires_at=int(business_now().timestamp()) + 600,
    )
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, values, *args: session.update(values))
    with TestClient(main.app, base_url="http://product-photo.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            parts = urlsplit(request.url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            headers = {key: value for key, value in request.headers.items() if key not in {"host", "content-length", "cookie"}}
            response = client.request(request.method, path, content=request.post_data_buffer,
                                      headers=headers, follow_redirects=True)
            response_headers = {key: value for key, value in response.headers.items()
                                if key not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}}
            route.fulfill(status=response.status_code, headers=response_headers, body=response.content)

        page.route("http://product-photo.test/**", bridge)
        page.goto(f"http://product-photo.test/portal/team-leader/product-details?product_id={data['product']}")
        page.wait_for_load_state("networkidle")
        form = page.locator("form[action$='/photo']")
        form.locator("[name=photo]").set_input_files({
            "name": "product.png", "mimeType": "image/png", "buffer": photo()["content"],
        })
        expect(page.get_by_alt_text("Selected product photo")).to_be_visible()
        form.get_by_role("button", name="Save photo").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("Product photo updated.", exact=True)).to_be_visible()
        assert fetch_one("SELECT 1 FROM inventory_product_images WHERE product_item_id=%s", (data["product"],))
        assert not errors, errors
        browser.close()
