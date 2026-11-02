"""Inventory and Owner share stock truth without sharing Owner-only controls."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name.
from uuid import uuid4
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, web_sessions, worker, portal_config
from app.business_time import business_now, business_today
from app.database import fetch_one, get_transaction_cursor
from app.portals import shared
from app.repositories import stock_alerts, superuser, product_returns
from app.repositories.portal_dashboard import leader_work_summary
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


def sessions(monkeypatch, d):
    session = dict(account_id=d['inventory'], role_key='team-leader', team_leader_role='inventory',
                   portal_expires_at=int(business_now().timestamp())+600)
    monkeypatch.setattr(web_sessions, '_load_session', lambda token: dict(session))
    monkeypatch.setattr(web_sessions, '_persist_session', lambda *args: None)
    monkeypatch.setattr(web_sessions, '_delete_session', lambda *args: None)
    return session


def assert_shared(d):
    owner = stock_alerts.stock_alert_workspace(d['owner'])
    inventory = stock_alerts.stock_alert_workspace()
    for key in ('stock_risk', 'low_stock', 'stock_threshold', 'stock_alert_scan'):
        assert inventory[key] == owner[key], key
    stock_rows = [a for a in owner['alerts'] if a['alert_type'] in {'low_stock','near_expiry','expired_batch'}]
    assert [{k:v for k,v in a.items() if k != 'reviewed_at'} for a in stock_rows] == [
        {k:v for k,v in a.items() if k != 'reviewed_at'} for a in inventory['alerts']]
    assert all(a['reviewed_at'] is None for a in inventory['alerts'])
    return owner, inventory


def test_shared_records_and_live_values_without_owner_review_leakage(lifecycle):
    d = lifecycle
    worker.scan_batch_expiry()
    owner, inventory = assert_shared(d)
    alert_id = inventory['alerts'][0]['alert_id']
    stock_alerts.review_alert(alert_id, d['owner'])
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO alerts(alert_type,product_id,message) VALUES('forecast',%s,'Owner-only forecast alert')", (d['product'],))
    owner, inventory = assert_shared(d)
    assert next(a for a in owner['alerts'] if a['alert_id'] == alert_id)['reviewed_at']
    assert not any(a['alert_type'] == 'forecast' for a in inventory['alerts'])


def test_dashboard_uses_same_live_threshold_even_before_worker_scan(lifecycle):
    d = lifecycle
    superuser.update_business_settings({'inventory.low_stock_packs':20}, d['owner'], 'Shared warning policy test')
    assert not fetch_one("SELECT * FROM alerts WHERE alert_type='low_stock'")
    dashboard = leader_work_summary(d['inventory'], 'inventory')
    owner, inventory = assert_shared(d)
    assert dashboard['counts']['low_stock'] == len(inventory['low_stock']) == 1
    assert next(q for q in dashboard['queue'] if q['label'] == 'Low-stock products')['path'] == 'alerts#replenishment'
    superuser.update_business_settings({'inventory.low_stock_packs':19}, d['owner'], 'Lower shared warning policy')
    assert leader_work_summary(d['inventory'], 'inventory')['counts']['low_stock'] == 0
    assert stock_alerts.stock_alert_workspace()['low_stock'] == []


def test_pullout_updates_live_risks_and_recorded_clearance_in_both_portals(lifecycle):
    d = lifecycle
    worker.scan_batch_expiry()
    before, _ = assert_shared(d)
    expired = next(a for a in before['alerts'] if a['alert_type'] == 'expired_batch')
    product_returns.confirm_expired_pullout(d['batches'][0], d['inventory'], 10,
        'Expired holding', 'Expired packs physically removed', str(uuid4()))
    owner, _ = assert_shared(d)
    assert not any(b['expired'] for b in owner['stock_risk']['batches'])
    stock_alerts.reconcile_stock_alerts()
    owner, _ = assert_shared(d)
    assert next(a for a in owner['alerts'] if a['alert_id'] == expired['alert_id'])['status'] == 'resolved'


def test_inventory_page_links_are_scoped_and_get_is_read_only(lifecycle, monkeypatch):
    d = lifecycle
    worker.scan_batch_expiry()
    session = sessions(monkeypatch, d)
    before = fetch_one('SELECT count(*) n FROM activity_logs')['n']
    with TestClient(main.app) as client:
        response = client.get('/portal/team-leader/alerts')
        assert response.status_code == 200
        text = response.text
        assert 'Shared with Owner' in text and 'shared business policy' in text
        assert '/portal/team-leader/product-details?product_id=' in text
        assert '/portal/team-leader/expired-stock' in text
        assert '/portal/owner/' not in text
        assert 'Mark reviewed' not in text and 'Close recorded alert' not in text
        assert 'Change in settings' not in text
        assert fetch_one('SELECT count(*) n FROM activity_logs')['n'] == before
        session.update(account_id=d['sales'], team_leader_role='sales')
        assert client.get('/portal/team-leader/alerts', follow_redirects=False).status_code == 404
        session.update(account_id=d['supervisor'],role_key='sales-supervisor',team_leader_role='sales-supervisor')
        assert client.get('/portal/sales-supervisor/alerts', follow_redirects=False).status_code == 404
    assert 'alerts' in {a[0] for a in portal_config.team_leader_nav_by_role['inventory']}
    assert 'alerts' not in {a[0] for a in portal_config.team_leader_nav_by_role['sales']}


def test_stock_notifications_open_shared_inventory_alert_record(lifecycle, monkeypatch):
    d = lifecycle
    worker.scan_batch_expiry()
    sessions(monkeypatch, d)
    notification = fetch_one("""SELECT n.* FROM notifications n JOIN notification_recipients nr USING(notification_id)
        WHERE nr.account_id=%s AND n.source_type='alerts' ORDER BY n.notification_id LIMIT 1""", (d['inventory'],))
    assert notification and '/portal/team-leader/alerts#stock-alert-' in notification['target_url']
    # Older stock notices still reach the shared page without rewriting history.
    legacy = {**notification, 'target_url':'/portal/team-leader/batches'}
    assert shared.normalize_notification_targets([legacy], 'team-leader', 'inventory')[0]['target_url'] == (
        f"/portal/team-leader/alerts#stock-alert-{notification['source_id']}")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE notifications SET target_url='/portal/team-leader/batches' WHERE notification_id=%s", (notification['notification_id'],))
    with TestClient(main.app) as client:
        response = client.post(f"/portal/team-leader/notifications/{notification['notification_id']}/open", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == f"/portal/team-leader/alerts#stock-alert-{notification['source_id']}"
    assert fetch_one('SELECT read_at FROM notification_recipients WHERE notification_id=%s AND account_id=%s',
                     (notification['notification_id'], d['inventory']))['read_at']


@pytest.mark.parametrize('portal_role', ['team-leader', 'owner'])
def test_stock_alert_cards_open_filtered_detail_pages(lifecycle, monkeypatch, portal_role):
    from pathlib import Path
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright, expect

    d = lifecycle
    session = sessions(monkeypatch, d)
    if portal_role == 'owner':
        session.update(account_id=d['owner'], role_key='owner', team_leader_role=None)
    superuser.update_business_settings({'inventory.low_stock_packs': 20}, d['owner'], 'Detail page threshold')
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s',
                    (business_today() + timedelta(days=1), d['batches'][1]))
    workspace = stock_alerts.stock_alert_workspace()
    before = fetch_one('SELECT count(*) n FROM activity_logs')['n']
    output = Path(f'outputs/{portal_role}-alert-card-details')
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url='http://alerts.test') as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def bridge(route):
            url = urlsplit(route.request.url)
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            route.fulfill(status=response.status_code, body=response.content, headers={
                k: v for k, v in response.headers.items()
                if k not in {'content-length', 'content-encoding', 'transfer-encoding', 'set-cookie'}
            })

        page.route('http://alerts.test/**', bridge)
        for width in (1440, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.goto(f'http://alerts.test/portal/{portal_role}/alerts', wait_until='networkidle')
            expect(page.locator('.oa-summary a')).to_have_count(3)
            page.screenshot(path=str(output / f'summary-{width}.png'), full_page=True)
            for index, category in enumerate(('expired', 'expiring', 'low-stock')):
                card = page.locator('.oa-summary a').nth(index)
                href = f'/portal/{portal_role}/alerts?category={category}'
                expect(card).to_have_attribute('href', href)
                count = int(card.locator('strong').inner_text().split()[0])
                card.click()
                page.wait_for_url('http://alerts.test' + href)
                page.wait_for_load_state('networkidle')
                expect(page.locator('.oa-summary')).to_have_count(0)
                expect(page.locator('.oa-recorded')).to_have_count(0)
                expected = (workspace['low_stock'] if category == 'low-stock' else
                            [b for b in workspace['stock_risk']['batches'] if b['expired'] == (category == 'expired')])
                expect(page.locator('.oa-stock-row')).to_have_count(len(expected))
                assert count == len(expected)
                if category != 'low-stock':
                    assert page.locator('.oa-batch-code').all_text_contents() == [
                        f"Batch {b['batch_code']}" for b in expected]
                expect(page.locator('.ia-list-heading')).to_be_visible()
                expect(page.locator('.ia-categories [aria-current=page]')).to_have_attribute('href', href)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / f'{category}-{width}.png'), full_page=True)
                page.get_by_role('link', name='Back to stock alerts').click()
                page.wait_for_load_state('networkidle')
                expect(page.locator('.oa-summary a')).to_have_count(3)
        assert not errors
        assert client.get(f'/portal/{portal_role}/alerts?category=unknown').status_code == 404
        assert fetch_one('SELECT count(*) n FROM activity_logs')['n'] == before
        with get_transaction_cursor() as cur:
            cur.execute('UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s',
                        (business_today(), d['batches'][1]))
        page.goto(f'http://alerts.test/portal/{portal_role}/alerts?category=expiring', wait_until='networkidle')
        expect(page.locator('.oa-stock-row')).to_have_count(0)
        expect(page.get_by_role('heading', name='No batches expiring soon')).to_be_visible()
        session.update(account_id=d['sales'], role_key='team-leader', team_leader_role='sales')
        for category in ('expired', 'expiring', 'low-stock'):
            assert client.get(f'/portal/{portal_role}/alerts?category={category}', follow_redirects=False).status_code in {303, 404}
        browser.close()
