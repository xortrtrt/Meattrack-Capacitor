"""Product photo form in Product Details."""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions, repositories
from app.business_time import business_now
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401

from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_product_recipe_editor_browser(lifecycle, monkeypatch):  # noqa: F811
    data = lifecycle
    session = dict(
        account_id=data["inventory"], role_key="team-leader", team_leader_role="inventory",
        portal_expires_at=int(business_now().timestamp()) + 600,
    )
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, values, *args: session.update(values))
    material = repositories.add_raw_inventory_item('Editor meat', 'meat', 'g', '5000', data['inventory'])['raw_material_id']
    other = repositories.add_raw_inventory_item('Editor seasoning', 'raw_material', 'g', '1000', data['inventory'])['raw_material_id']
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
        page.goto('http://product-photo.test/portal/team-leader/inventory?modal=product-recipes')
        modal = page.locator('[data-inventory-modal="product-recipes"]')
        expect(modal).to_be_visible()
        modal.locator('.recipe-card-summary').first.click()
        form = modal.locator('[data-recipe-editor]').first
        form.get_by_role('button', name='Save recipe and net content').click()
        expect(form.locator('[aria-invalid="true"]').first).to_be_visible()
        form.locator('[name="pack_size"]').fill('500')
        form.locator('[name="pack_size_unit"]').select_option('g')
        row = form.locator('[data-edit-recipe-row]').first
        row.locator('[name="material_item_id[]"]').select_option(str(material))
        row.locator('[name="quantity_required[]"]').fill('450')
        row.locator('[name="quantity_required_unit[]"]').select_option('g')
        form.get_by_role('button', name='Add ingredient').click()
        row = form.locator('[data-edit-recipe-row]').last
        row.locator('[name="material_item_id[]"]').select_option(str(other))
        row.locator('[name="quantity_required[]"]').fill('50')
        row.locator('[name="quantity_required_unit[]"]').select_option('g')
        for width in (1440, 390):
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert form.locator('select').first.bounding_box()['width'] > 100
            page.screenshot(path=f'outputs/recipe-editor-{width}.png',full_page=True)
        form.get_by_role('button', name='Save recipe and net content').click()
        expect(page.get_by_text('Recipe and declared net content updated.', exact=True)).to_be_visible()
        assert fetch_one('SELECT COUNT(*) n FROM product_recipes WHERE product_item_id=%s', (data['product'],))['n'] == 2
        page.goto('http://product-photo.test/portal/team-leader/inventory?modal=product-recipes')
        page.locator('.recipe-card-summary').first.click()
        form = page.locator('[data-recipe-editor]').first
        expect(form.locator('[name="pack_size"]')).to_have_value('500.0')
        form.locator('[data-edit-recipe-remove]').last.click()
        expect(form.locator('[data-edit-recipe-row]')).to_have_count(1)
        assert not errors, errors
        browser.close()
