"""Render all five real role dashboards and follow their read-only destinations."""
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, repositories
from app.database import get_transaction_cursor
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, substitute  # noqa: F401
from test_replacement_workflow import request as replacement_request, approve_receive, pay
from test_role_dashboards import session_for

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_all_role_dashboards_reflow_navigation_and_keyboard(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    ready, _ = replacement_request(d, [(substitute(300), 1)])
    approve_receive(d, ready)
    pay(d, ready, '100')
    another, _ = replacement_request(d)
    from app.repositories import replacement_messages
    from uuid import uuid4
    replacement_messages.send(ready, d['reseller'], 'Payment is sent. Please confirm my replacement delivery.', str(uuid4()))
    replacement_messages.send(another, d['reseller'], 'Please review the products in this request.', str(uuid4()))
    with get_transaction_cursor() as cur:
        cur.execute('SELECT current_schema() name')
        assert cur.fetchone()['name'].startswith('test_inventory_')
        cur.execute('ANALYZE accounts, resellers, orders, order_items, inventory_items, inventory_batches, product_returns, product_return_lines, replacement_quote_lines, replacement_stock_reservations')
    output = Path('outputs/role-dashboards')
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    failures = []

    with TestClient(main.app, base_url='http://role-dashboards.test') as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.emulate_media(reduced_motion='reduce')
        page.on('pageerror', lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            parts = urlsplit(request.url)
            response = client.request(request.method, parts.path + ('?' + parts.query if parts.query else ''),
                content=request.post_data_buffer,
                headers={key: value for key, value in request.headers.items()
                         if key not in {'host', 'content-length', 'cookie'}}, follow_redirects=True)
            if response.status_code >= 400:
                failures.append((parts.path, response.status_code))
            route.fulfill(status=response.status_code, body=response.content,
                headers={key: value for key, value in response.headers.items()
                         if key not in {'content-length', 'content-encoding', 'transfer-encoding'}})

        page.route('http://role-dashboards.test/**', bridge)
        def fresh_page():
            fresh = browser.new_page(viewport={'width': 1440, 'height': 1000})
            fresh.emulate_media(reduced_motion='reduce')
            fresh.on('pageerror', lambda error: errors.append(str(error)))
            fresh.route('http://role-dashboards.test/**', bridge)
            return fresh
        for role in ('owner', 'supervisor', 'sales', 'inventory', 'reseller'):
            # Separate role sessions cannot leave an old role's badge timer polling the next session.
            page.close()
            _, portal = session_for(monkeypatch, d, role)
            page = fresh_page()
            for width in (320, 390, 768, 1024, 1440, 1920):
                page.set_viewport_size({'width': width, 'height': 1000 if width >= 1024 else 844})
                response = page.goto(f'http://role-dashboards.test/portal/{portal}/dashboard')
                page.wait_for_load_state('networkidle')
                assert response.status == 200
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (role, width)
                expect(page.locator('.role-dashboard')).to_be_visible()
                queues = page.locator('[data-dashboard-queue]')
                assert queues.count() >= 4
                assert queues.evaluate_all("rows => rows.some(row => row.dataset.count === '0')")
                assert queues.evaluate_all("rows => rows.every(row => row.getBoundingClientRect().height >= 44)")
                assert page.get_by_text('queued items', exact=True).count() == 0
                if width in (1440, 390):
                    page.screenshot(path=str(output / f'{role}-{width}.png'), full_page=True)
                    page.screenshot(path=str(output / f'{role}-{width}-viewport.png'))
                if role in ('owner','sales','supervisor'):
                    details = page.locator('.role-dashboard .dash-details').last
                    assert not details.evaluate('element => element.open')
                    details.locator('summary').first.focus()
                    page.keyboard.press('Enter')
                    assert details.evaluate('element => element.open')
                    page.keyboard.press('Enter')
                    assert not details.evaluate('element => element.open')
                if role == 'supervisor':
                    expect(page.get_by_role('heading', name='Operations Overview', exact=True)).to_be_visible()
                    expect(page.locator('.ops-message-list > a')).to_have_count(2)
                if role == 'inventory':
                    expect(page.get_by_role('heading', name='Warehouse Overview', exact=True)).to_be_visible()
                    expect(page.locator('[data-inventory-movement-chart]')).to_be_visible()
                    data = page.get_by_text('View movement data as a table', exact=True)
                    data.click()
                    expect(page.locator('.dash-chart-table table')).to_be_visible()
                if role == 'reseller':
                    for image in page.locator('.dash-product img').all():
                        image.scroll_into_view_if_needed()
                        expect(image).to_have_js_property('complete', True)
                        assert image.evaluate('image => image.naturalWidth > 0')

            # A 720 CSS-pixel viewport at DPR 2 exercises desktop-width 200% zoom reflow.
            zoom = browser.new_context(viewport={'width': 720, 'height': 500}, device_scale_factor=2)
            zoom_page = zoom.new_page()
            zoom_page.on('pageerror', lambda error: errors.append(str(error)))
            zoom_page.route('http://role-dashboards.test/**', bridge)
            zoom_page.goto(f'http://role-dashboards.test/portal/{portal}/dashboard')
            zoom_page.wait_for_load_state('networkidle')
            assert zoom_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            zoom.close()

            if role == 'inventory':
                page.goto(f'http://role-dashboards.test/portal/{portal}/dashboard')
                page.get_by_role('link').filter(has_text='Replacement preparation').click()
                expect(page.get_by_role('link', name='Clear dashboard filter')).to_be_visible()
                assert 'dashboard_queue=prepare' in page.url
                page.get_by_role('link', name='Clear dashboard filter').click()
                assert 'dashboard_queue=' not in page.url
            if role == 'supervisor':
                page.goto(f'http://role-dashboards.test/portal/{portal}/dashboard')
                page.locator('.ops-message-list > a').first.click()
                assert 'replacement=' in page.url
                expect(page.locator('[data-replacement-conversation]')).to_be_visible()
            if role == 'reseller':
                page.goto(f'http://role-dashboards.test/portal/{portal}/dashboard')
                page.locator('.dash-order').first.click()
                expect(page.locator(f'#order-{d["order"]}')).to_have_attribute('open', '')

        # Stress text/amount reflow separately; preserve representative screenshots above.
        original_money = repositories.settlement_summary
        monkeypatch.setattr(repositories, 'settlement_summary', lambda *args, **kwargs: {
            **original_money(*args, **kwargs), 'retained_sales': 9999999999.99,
        })
        with get_transaction_cursor() as cur:
            cur.execute('UPDATE accounts SET name=%s WHERE account_id=%s', ('Sales contact with an unusually long name ' * 3, d['sales']))
            cur.execute('UPDATE resellers SET business_name=%s WHERE reseller_id=(SELECT reseller_id FROM accounts WHERE account_id=%s)',
                        ('A very long reseller business name for responsive browsing ' * 2, d['reseller']))
        for role in ('owner', 'sales', 'reseller'):
            page.close()
            _, portal = session_for(monkeypatch, d, role)
            page = fresh_page()
            page.set_viewport_size({'width': 320, 'height': 844})
            page.goto(f'http://role-dashboards.test/portal/{portal}/dashboard')
            page.wait_for_load_state('networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), role
        with get_transaction_cursor() as cur:
            cur.execute('UPDATE resellers SET team_leader_account_id=NULL WHERE reseller_id=(SELECT reseller_id FROM accounts WHERE account_id=%s)', (d['reseller'],))
        page.reload()
        expect(page.get_by_text('No Sales Leader assigned', exact=True)).to_be_visible()
        browser.close()
    assert not errors
    assert not failures
