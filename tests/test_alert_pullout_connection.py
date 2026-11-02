"""Batch-specific alert navigation and live pull-out progress."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name.
from pathlib import Path
import json
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, worker
from app.database import fetch_one
from app.repositories import product_returns, stock_alerts
from test_inventory_alert_workspace import sessions
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


def test_alert_link_is_actionable_before_scan_and_preserves_progress(lifecycle, monkeypatch):
    d = lifecycle
    session = sessions(monkeypatch, d)
    batch = d['batches'][0]
    target = f'/portal/team-leader/expired-stock?batch_id={batch}&from_alerts=1'
    assert fetch_one('SELECT count(*) n FROM expired_pullouts')['n'] == 0
    with TestClient(main.app) as client:
        alert_page = client.get('/portal/team-leader/alerts?category=expired')
        assert f'from_alerts=1#pullout-{batch}' in alert_page.text
        assert 'Pull out stock' in alert_page.text
        assert client.get(target).status_code == 200
        # Page reads must not manufacture worker queue/audit records.
        assert fetch_one('SELECT count(*) n FROM expired_pullouts')['n'] == 0
        assert product_returns.expired_stock_workspace()['pullouts'][0]['batch_id'] == batch
        for bad in ('-1', 'bad', str(d['batches'][1]), '9223372036854775808'):
            assert client.get(f'/portal/team-leader/expired-stock?batch_id={bad}').status_code == 404

        worker.scan_batch_expiry()
        episode = next(a for a in stock_alerts.stock_alert_workspace()['alerts'] if a['alert_type'] == 'expired_batch')
        payload = dict(batch_id=batch, from_alerts='1', quantity=4,
                       remarks='Expired packs physically moved', operation_key=str(uuid4()))
        for _ in range(2):
            response = client.post('/portal/team-leader/expired-stock/actions/pullout',
                                   data=payload, follow_redirects=False)
            params = parse_qs(urlsplit(response.headers['location']).query)
            assert params['batch_id'] == [str(batch)] and params['from_alerts'] == ['1']
        progress = product_returns.expired_pullout_progress(batch)
        assert progress['quantity_available'] == 6 and progress['holding_quantity'] == 4
        risk = stock_alerts.stock_alert_workspace()['stock_risk']['batches']
        assert next(b for b in risk if b['batch_id'] == batch)['quantity'] == 6

        # Invalid quantities keep the user in the same batch context.
        response = client.post('/portal/team-leader/expired-stock/actions/pullout',
                               data={**payload, 'quantity': 7, 'operation_key': str(uuid4())}, follow_redirects=False)
        params = parse_qs(urlsplit(response.headers['location']).query)
        assert 'error' in params and params['batch_id'] == [str(batch)]
        worker.scan_batch_expiry()
        assert next(a for a in stock_alerts.stock_alert_workspace()['alerts']
                    if a['alert_id'] == episode['alert_id'])['status'] != 'resolved'
        client.post('/portal/team-leader/expired-stock/actions/pullout',
                    data={**payload, 'quantity': 6, 'operation_key': str(uuid4())})
        progress = product_returns.expired_pullout_progress(batch)
        assert progress['quantity_available'] == 0 and progress['holding_quantity'] == 10
        assert 'Warehouse pull-out complete' in client.get(target).text
        assert not any(b['batch_id'] == batch for b in stock_alerts.stock_alert_workspace()['stock_risk']['batches'])
        worker.scan_batch_expiry()
        assert next(a for a in stock_alerts.stock_alert_workspace()['alerts']
                    if a['alert_id'] == episode['alert_id'])['status'] == 'resolved'
        # Disposal is still pending after warehouse risk clearance.
        assert product_returns.expired_pullout_progress(batch)['holding_quantity'] == 10
        session.update(account_id=d['sales'], team_leader_role='sales')
        assert client.get(target, follow_redirects=False).status_code == 404


@pytest.mark.parametrize('width', [1440, 390])
def test_browser_alert_to_batch_partial_full_and_back(lifecycle, monkeypatch, width):
    d = lifecycle
    sessions(monkeypatch, d)
    batch = d['batches'][0]
    output = Path('outputs/alert-pullout-connection')
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url='http://pullout.test') as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(req.method, url.path + ('?' + url.query if url.query else ''),
                                      content=req.post_data_buffer, follow_redirects=False,
                                      headers={k: v for k, v in req.headers.items()
                                               if k not in {'host', 'content-length', 'cookie'}})
            if response.status_code == 303:
                # Keep the redirect's GET inside this in-process browser bridge.
                route.fulfill(status=200, content_type='text/html', body=(
                    '<script>location.replace(' + json.dumps(response.headers['location']) + ')</script>'))
                return
            route.fulfill(status=response.status_code, body=response.content, headers={
                k: v for k, v in response.headers.items()
                if k not in {'content-length', 'content-encoding', 'transfer-encoding', 'set-cookie'}})

        page.route('http://pullout.test/**', bridge)
        page.goto('http://pullout.test/portal/team-leader/alerts?category=expired', wait_until='networkidle')
        page.evaluate("sessionStorage.setItem('expired-stock-tab:/portal/team-leader/expired-stock','disposed-stock')")
        page.locator('.oa-stock-row.is-urgent a').click()
        page.wait_for_load_state('networkidle')
        expect(page.locator(f'#pullout-{batch}')).to_be_visible()
        expect(page.locator('#tab-pending-pullout')).to_have_attribute('aria-selected', 'true')
        for quantity, remaining in ((4, 6), (6, 0)):
            card = page.locator(f'#pullout-{batch}')
            card.get_by_label('Quantity moved').fill(str(quantity))
            card.get_by_label('Pull-out remarks').fill('Expired packs physically moved to holding')
            card.get_by_role('button', name='Confirm physical pull-out').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('.custody-alert-focus')).to_contain_text(f'{remaining} packs awaiting pull-out')
            assert f'batch_id={batch}' in page.url
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / f'after-{quantity}-{width}.png'), full_page=True)
        expect(page.locator('.custody-alert-focus')).to_contain_text('Warehouse pull-out complete')
        page.get_by_role('link', name='View holding', exact=True).click()
        expect(page.locator('#expired-holding')).to_be_visible()
        page.get_by_role('link', name='Back to stock alerts').click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('heading', name='No expired stock')).to_be_visible()
        assert not errors
        browser.close()
