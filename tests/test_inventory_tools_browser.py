"""Real templates and JavaScript, bridged to an isolated database."""
# ruff: noqa: F811
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from app.database import fetch_one
from test_inventory_tools import material, package, photo
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_inventory_tools_mobile_and_desktop(lifecycle, monkeypatch):
    d = lifecycle
    item = material(d)
    package_id = package(d)
    session = dict(account_id=d['inventory'],role_key='team-leader',team_leader_role='inventory',
                   portal_expires_at=int(business_now().timestamp())+600)
    monkeypatch.setattr(web_sessions,'_load_session',lambda token:dict(session))
    monkeypatch.setattr(web_sessions,'_persist_session',lambda token,data,*args:session.update(data))
    output = Path('outputs/inventory-tools')
    output.mkdir(parents=True,exist_ok=True)
    with TestClient(main.app,base_url='http://inventory-tools.test') as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []
        page.on('pageerror',lambda error:errors.append(str(error)))
        def bridge(route):
            req = route.request
            parts = urlsplit(req.url)
            path = parts.path+('?' + parts.query if parts.query else '')
            headers = {k:v for k,v in req.headers.items() if k not in {'host','content-length','cookie'}}
            response = client.request(req.method,path,content=req.post_data_buffer,headers=headers,follow_redirects=True)
            response_headers = {k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','transfer-encoding','set-cookie'}}
            route.fulfill(status=response.status_code,headers=response_headers,body=response.content)
        page.route('http://inventory-tools.test/**',bridge)
        page.goto('http://inventory-tools.test/portal/team-leader/inventory-tools')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('heading',name='Deduct raw materials',exact=True)).to_be_visible()
        page.screenshot(path=str(output/'materials-desktop.png'),full_page=True)
        form = page.locator('form[action$="/deduct"]')
        form.locator('select').select_option(str(item))
        form.locator('[name=quantity]').fill('1.125')
        form.locator('[name=reason]').fill('Browser spoilage check')
        form.get_by_role('button',name='Record deduction').click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('Inventory change recorded.',exact=True)).to_be_visible()
        assert float(fetch_one('SELECT quantity_available FROM inventory_items WHERE item_id=%s',(item,))['quantity_available'])==8.875
        page.set_viewport_size({'width':390,'height':844})
        page.goto('http://inventory-tools.test/portal/team-leader/inventory-tools?view=packages')
        page.wait_for_load_state('networkidle')
        panel = page.locator(f'#package-{package_id}')
        panel.get_by_text('Configure and lock standard contents',exact=True).click()
        form = panel.locator('form[action$="/package-save"]')
        form.locator('input[name="packs[]"]').fill('2')
        form.locator('[name=confirmed]').check()
        form.get_by_role('button',name='Save and lock standard').click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('Standard locked',exact=True)).to_be_visible()
        panel.get_by_text('Dispatch Trial Package',exact=True).click()
        form = panel.locator('form[action$="/package-dispatch"]')
        form.locator('[name=reason]').fill('Browser package dispatch')
        form.get_by_role('button',name='Dispatch package stock').click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('Browser package dispatch',exact=True)).to_be_visible()
        assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s',(d['batches'][1],))['quantity_available']==18
        page.get_by_role('button',name='Close notification').click()
        expect(page.locator('[data-toast]')).to_have_count(0)
        page.screenshot(path=str(output/'packages-mobile.png'),full_page=True)
        page.goto(f"http://inventory-tools.test/portal/team-leader/product-details?product_id={d['product']}")
        page.wait_for_load_state('networkidle')
        form = page.locator('form[action$="/photo"]')
        form.locator('[name=photo]').set_input_files({'name':'product.png','mimeType':'image/png','buffer':photo()['content']})
        expect(page.get_by_alt_text('Selected product photo')).to_be_visible()
        form.get_by_role('button',name='Save photo').click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('Inventory change recorded.',exact=True)).to_be_visible()
        page.get_by_role('button',name='Close notification').click()
        expect(page.locator('[data-toast]')).to_have_count(0)
        form = page.locator('form[action$="/remove-batch"]')
        page.get_by_text('Remove batch',exact=True).first.click()
        form.locator('[name=reason]').fill('Browser batch damage')
        form.locator('[name=confirmed]').check()
        form.get_by_role('button',name='Remove batch',exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('Removed',exact=True)).to_be_visible()
        assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s',(d['batches'][1],))['quantity_available']==0
        page.get_by_role('button',name='Close notification').click()
        expect(page.locator('[data-toast]')).to_have_count(0)
        page.screenshot(path=str(output/'product-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
