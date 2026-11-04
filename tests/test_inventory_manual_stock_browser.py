"""Product photo form in Product Details."""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401

from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_manual_stock_workspace_browser(lifecycle, monkeypatch):  # noqa: F811
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
                                      headers=headers, follow_redirects=False)
            response_headers = {key: value for key, value in response.headers.items()
                                if key not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}}
            if response.status_code == 303:
                location = response.headers['location']
                # Follow within TestClient; route interception does not preserve all redirect semantics.
                response = client.get(location)
                response_headers = {key: value for key, value in response.headers.items()
                                    if key not in {'content-length','content-encoding','transfer-encoding','set-cookie'}}
            route.fulfill(status=response.status_code, headers=response_headers, body=response.content)

        page.route("http://product-photo.test/**", bridge)
        page.goto("http://product-photo.test/portal/team-leader/inventory")
        page.wait_for_load_state("networkidle")
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 850})
            for name in ('opening-balance', 'deduct-material', 'dispatch'):
                trigger = page.locator(f'[data-inventory-modal-open="{name}"]')
                trigger.click()
                modal = page.locator(f'[data-inventory-modal="{name}"]')
                expect(modal).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                modal.locator('button[type="submit"]').click()
                expect(modal.locator('[aria-invalid="true"]').first).to_be_visible()
                page.keyboard.press('Escape')
                expect(modal).to_be_hidden()
                expect(trigger).to_be_focused()
        trigger = page.locator('[data-inventory-modal-open="opening-balance"]')
        trigger.click()
        modal = page.locator('[data-inventory-modal="opening-balance"]')
        modal.locator('[name="product_id"]').select_option(str(data['product']))
        modal.locator('[name="batch_code"]').fill('BROWSER-OPEN')
        modal.locator('[name="quantity"]').fill('5')
        from datetime import timedelta
        from app.business_time import business_today
        modal.locator('[name="expiry_date"]').fill(str(business_today()+timedelta(days=90)))
        modal.locator('[name="reason"]').fill('Browser opening count')
        modal.locator('button[type="submit"]').click()
        expect(page.get_by_text('Stock action recorded.', exact=True)).to_be_visible()
        assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_code='BROWSER-OPEN'")['quantity_available'] == 5
        page.locator('[data-inventory-modal-open="dispatch"]').click()
        modal = page.locator('[data-inventory-modal="dispatch"]')
        modal.locator('[name="product_id"]').select_option(str(data['product']))
        modal.locator('[name="quantity"]').fill('999')
        modal.locator('[name="reason"]').fill('Preserve this failed draft')
        modal.locator('button[type="submit"]').click()
        page.goto('http://product-photo.test/portal/team-leader/inventory?modal=dispatch')
        modal = page.locator('[data-inventory-modal="dispatch"]')
        expect(modal).to_be_visible()
        expect(modal.locator('[name="reason"]')).to_have_value('Preserve this failed draft')
        page.screenshot(path='outputs/manual-stock-dialog-mobile.png', full_page=True)
        page.keyboard.press('Escape')
        page.goto('http://product-photo.test/portal/team-leader/raw-materials?type=removed')
        expect(page.get_by_role('link', name='Removed materials', exact=True)).to_have_attribute('aria-current','page')
        page.goto(f"http://product-photo.test/portal/team-leader/product-details?product_id={data['product']}")
        expect(page.get_by_text('Remove batch', exact=True).first).to_be_visible()
        page.screenshot(path='outputs/manual-stock-mobile.png', full_page=True)
        card = page.locator('.lifecycle-batch-card').filter(has_text='BROWSER-OPEN')
        card.locator('summary').click()
        form = card.locator('form')
        form.locator('[name="reason"]').fill('Damaged opening batch')
        form.get_by_role('button', name='Remove batch', exact=True).click()
        expect(page.get_by_role('dialog', name='Confirm stock action')).to_be_visible()
        page.get_by_role('button', name='Cancel', exact=True).click()
        assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_code='BROWSER-OPEN'")['quantity_available'] == 5
        form.get_by_role('button', name='Remove batch', exact=True).click()
        page.get_by_role('dialog', name='Confirm stock action').get_by_role('button', name='Remove batch', exact=True).click()
        expect(page.get_by_text('Stock action recorded.', exact=True)).to_be_visible()
        assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_code='BROWSER-OPEN'")['quantity_available'] == 0
        assert not errors, errors
        browser.close()
