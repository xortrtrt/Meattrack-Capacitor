"""Stock episodes, Owner review isolation, and guarded manual closure."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name.
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, worker
from app.business_time import business_today
from app.database import fetch_one, fetch_all, get_transaction_cursor
from app.portals import shared
from app.repositories import stock_alerts as alerts
from app.repositories import superuser, product_returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_inventory_postgres import _payment_submission
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


def setting(d, value):
    superuser.update_business_settings({'inventory.low_stock_packs': value}, d['owner'], 'Adjust warning policy for test')


def current(d):
    return fetch_one("SELECT * FROM alerts WHERE alert_type='low_stock' AND product_id=%s AND status<>'resolved'", (d['product'],))


def test_low_stock_episodes_threshold_and_notifications(lifecycle):
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    first = current(d)
    assert first and first['severity'] == 'warning'
    assert fetch_one('SELECT available FROM finished_stock_availability WHERE item_id=%s', (d['product'],))['available'] == 20
    alerts.reconcile_stock_alerts()
    assert current(d)['alert_id'] == first['alert_id']
    assert fetch_one("SELECT count(*) n FROM notifications WHERE source_type='alerts' AND source_id=%s", (first['alert_id'],))['n'] == 1
    setting(d, 19)
    alerts.reconcile_stock_alerts()
    assert not current(d)
    closed = fetch_one('SELECT * FROM alerts WHERE alert_id=%s', (first['alert_id'],))
    assert closed['resolved_at'] and closed['resolution_source'] == 'automatic'
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    assert current(d)['alert_id'] != first['alert_id']
    assert alerts.reconciliation_status()['status'] == 'ok'


def test_zero_stock_escalation_and_inactive_clearance(lifecycle):
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    first = current(d)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quality_status='rejected' WHERE item_id=%s", (d['product'],))
    setting(d, 0)
    alerts.reconcile_stock_alerts()
    assert current(d)['alert_id'] == first['alert_id']
    assert current(d)['severity'] == 'critical'
    assert fetch_one("SELECT count(*) n FROM notifications WHERE source_type='alerts' AND source_id=%s", (first['alert_id'],))['n'] == 3
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_items SET is_active=false WHERE item_id=%s', (d['product'],))
    alerts.reconcile_stock_alerts()
    assert not current(d)


def test_reviews_are_private_idempotent_and_do_not_read_notifications(lifecycle):
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    alert_id = current(d)['alert_id']
    before = fetch_all('SELECT * FROM notification_recipients ORDER BY notification_id,account_id')
    alerts.review_alert(alert_id, d['owner'])
    alerts.review_alert(alert_id, d['owner'])
    assert current(d)['status'] == 'open'
    assert next(a for a in alerts.owner_alerts(d['owner']) if a['alert_id'] == alert_id)['reviewed_at']
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash) VALUES('owner','Second Owner',%s,'hash') RETURNING account_id", (f'{uuid4()}@test.example',))
        second = cur.fetchone()['account_id']
    assert not next(a for a in alerts.owner_alerts(second) if a['alert_id'] == alert_id)['reviewed_at']
    assert fetch_one("SELECT count(*) n FROM activity_logs WHERE action='reviewed_alert' AND entity_id=%s", (alert_id,))['n'] == 1
    assert fetch_all('SELECT * FROM notification_recipients ORDER BY notification_id,account_id') == before
    with pytest.raises(ValueError, match='Owner'):
        alerts.review_alert(alert_id, d['inventory'])


def test_review_and_close_roll_back_when_audit_fails(lifecycle, monkeypatch):
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    alert_id = current(d)['alert_id']
    def fail(*args, **kwargs):
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr(alerts, '_audit_cursor', fail)
    with pytest.raises(RuntimeError, match='audit unavailable'):
        alerts.review_alert(alert_id, d['owner'])
    assert not fetch_one('SELECT * FROM owner_alert_reviews WHERE alert_id=%s', (alert_id,))
    setting(d, 19)
    with pytest.raises(RuntimeError, match='audit unavailable'):
        alerts.close_alert(alert_id, d['owner'], 'Stock verified above threshold')
    assert current(d)['status'] == 'open'


def test_closure_rechecks_risk_and_requires_reason(lifecycle):
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    alert_id = current(d)['alert_id']
    with pytest.raises(ValueError, match='5 to 500'):
        alerts.close_alert(alert_id, d['owner'], '')
    with pytest.raises(ValueError, match='still active'):
        alerts.close_alert(alert_id, d['owner'], 'Ignore current stock shortage')
    setting(d, 19)
    result = alerts.close_alert(alert_id, d['owner'], 'Verified enough available stock')
    assert result['status'] == 'resolved' and result['resolution_source'] == 'owner'
    alerts.close_alert(alert_id, d['owner'], 'Verified enough available stock')
    assert fetch_one("SELECT count(*) n FROM activity_logs WHERE action='resolved_alert' AND entity_id=%s", (alert_id,))['n'] == 1


def test_expiry_transition_and_quality_reclassification_do_not_fake_pullout(lifecycle):
    d = lifecycle
    batch_id = d['batches'][1]
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s', (business_today()+timedelta(days=1), batch_id))
    worker.scan_batch_expiry()
    assert fetch_one("SELECT * FROM alerts WHERE product_batch_id=%s AND alert_type='near_expiry' AND status='open'", (batch_id,))
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s', (business_today(), batch_id))
    worker.scan_batch_expiry()
    assert fetch_one("SELECT resolution_source FROM alerts WHERE product_batch_id=%s AND alert_type='near_expiry'", (batch_id,))['resolution_source'] == 'automatic'
    expired = fetch_one("SELECT * FROM alerts WHERE product_batch_id=%s AND alert_type='expired_batch' AND status='open'", (batch_id,))
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quality_status='spoiled' WHERE batch_id=%s", (batch_id,))
    alerts.reconcile_stock_alerts()
    with pytest.raises(ValueError, match='still active'):
        alerts.close_alert(expired['alert_id'], d['owner'], 'Quality changed without pull-out')
    product_returns.confirm_expired_pullout(batch_id, d['inventory'], 4, 'Expired holding', 'Partial physical pull-out', str(uuid4()))
    alerts.reconcile_stock_alerts()
    assert fetch_one('SELECT status FROM alerts WHERE alert_id=%s', (expired['alert_id'],))['status'] == 'open'
    product_returns.confirm_expired_pullout(batch_id, d['inventory'], 16, 'Expired holding', 'Remaining physical pull-out', str(uuid4()))
    alerts.reconcile_stock_alerts()
    assert fetch_one('SELECT status FROM alerts WHERE alert_id=%s', (expired['alert_id'],))['status'] == 'resolved'


def test_concurrent_reconciliation_and_disabled_owner_notifications(lifecycle):
    d = lifecycle
    setting(d, 20)
    superuser.update_business_settings({'notifications.owner_critical_alerts': False}, d['owner'], 'Disable Owner critical alerts')
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: alerts.reconcile_stock_alerts(), range(2)))
    assert len(fetch_all("SELECT * FROM alerts WHERE product_id=%s AND alert_type='low_stock' AND status<>'resolved'", (d['product'],))) == 1
    assert not fetch_one("SELECT * FROM notifications WHERE recipient_role='owner' AND source_type='alerts'")


def test_owner_page_and_routes(lifecycle, monkeypatch):
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    alert_id = current(d)['alert_id']
    def session(request, role):
        request.session.update(account_id=d['owner'], role_key='owner')
    monkeypatch.setattr(shared, 'require_portal_session', session)
    with TestClient(main.app) as client:
        response = client.get('/portal/owner/alerts?threshold=0')
        assert response.status_code == 200
        assert 'Mark reviewed' in response.text and 'Business warning level' in response.text
        assert 'name="threshold"' not in response.text
        response = client.post(f'/portal/owner/alerts/{alert_id}/review', follow_redirects=False)
        assert response.status_code == 303
        assert fetch_one('SELECT * FROM owner_alert_reviews WHERE alert_id=%s', (alert_id,))
        response = client.post(f'/portal/owner/alerts/{alert_id}/status', data={'status':'bogus'}, follow_redirects=False)
        assert response.status_code == 303 and 'Unknown' in response.headers['location']
        response = client.post(f'/portal/owner/alerts/{alert_id}/status', data={'status':'resolved'}, follow_redirects=False)
        assert response.status_code == 303 and 'reason' in response.headers['location']
        response = client.post('/portal/owner/settings', data={
            'order_payment_window_minutes':15, 'near_expiry_days':14, 'critical_expiry_days':2,
            'low_stock_packs':0, 'forecast_default_horizon_days':7,
            'reason':'Use out-of-stock warnings only'}, follow_redirects=False)
        assert response.status_code == 303 and 'error=' not in response.headers['location']
        assert superuser.business_setting('inventory.low_stock_packs') == 0
        assert '0 available packs or fewer' in client.get('/portal/owner/alerts?threshold=100').text


@pytest.mark.parametrize('value', [-1, 100001, 1.5, True, 'ten'])
def test_invalid_threshold_rejected_before_db(value):
    with pytest.raises(ValueError, match='[Ll]ow-stock'):
        superuser.update_business_settings({'inventory.low_stock_packs':value}, 1, 'Update warning policy')


def test_paid_reservations_reduce_availability_and_cancel_releases_risk(lifecycle):
    d = lifecycle
    setting(d, 15)
    order = repositories.create_order_from_items('reseller', [(d['product'], 5)], account_id=d['reseller'])
    alerts.reconcile_stock_alerts()
    assert not current(d)  # An unpaid order does not reserve stock.
    proofs, payment = _payment_submission('stock-alert-reservation')
    repositories.submit_order_payment(d['reseller'], order['order_id'], proofs, payment)
    alerts.reconcile_stock_alerts()
    assert current(d)
    assert fetch_one('SELECT available FROM finished_stock_availability WHERE item_id=%s', (d['product'],))['available'] == 15
    repositories.decide_order(order['order_id'], 'reject', team_leader_account_id=d['sales'],
                              rejection_reason='Payment could not be confirmed', payment_received=False)
    alerts.reconcile_stock_alerts()
    assert not current(d)


def test_migration_consolidates_duplicates_and_retains_raw_material_history(lifecycle):
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute('DROP INDEX ux_active_product_low_stock_alert')
        for batch in d['batches']:
            cur.execute("INSERT INTO alerts(alert_type,product_id,product_batch_id,message) VALUES('low_stock',%s,%s,'Legacy shortage')", (d['product'], batch))
        cur.execute("INSERT INTO inventory_items(item_type,name,unit) VALUES('raw_material','Legacy meat','kg') RETURNING item_id")
        raw = cur.fetchone()['item_id']
        cur.execute("INSERT INTO alerts(alert_type,raw_material_id,message,status) VALUES('low_stock',%s,'Manual material policy','acknowledged') RETURNING alert_id", (raw,))
        raw_alert = cur.fetchone()['alert_id']
        cur.execute(Path('database/migrations/045_owner_alert_workflow.sql').read_text(encoding='utf-8'))
    history = fetch_all("SELECT * FROM alerts WHERE product_id=%s AND alert_type='low_stock' ORDER BY alert_id", (d['product'],))
    assert len(history) == 2 and history[0]['status'] == 'open' and history[1]['status'] == 'resolved'
    assert all(a['product_batch_id'] for a in history)
    assert history[1]['resolution_source'] == 'automatic' and 'duplicate' in history[1]['resolution_reason']
    alerts.reconcile_stock_alerts()
    raw_row = next(a for a in alerts.owner_alerts(d['owner']) if a['alert_id'] == raw_alert)
    assert not raw_row['stock_managed'] and raw_row['status'] == 'acknowledged'
    assert not raw_row['reviewed_at']
    alerts.close_alert(raw_alert, d['owner'], 'Manually verified material supply')


def test_receipt_and_worker_share_notification_episode(lifecycle):
    d = lifecycle
    batch = repositories.add_product_batch(d['product'], 'NEAR-RECEIPT', 2,
        business_today()+timedelta(days=1), actor_account_id=d['inventory'])
    alert = fetch_one("SELECT * FROM alerts WHERE product_batch_id=%s AND status='open'", (batch['product_batch_id'],))
    count = fetch_one("SELECT count(*) n FROM notifications WHERE source_type='alerts' AND source_id=%s", (alert['alert_id'],))['n']
    worker.scan_batch_expiry()
    assert fetch_one("SELECT count(*) n FROM notifications WHERE source_type='alerts' AND source_id=%s", (alert['alert_id'],))['n'] == count == 2


def test_review_and_closure_require_owner_session_and_csrf(lifecycle, monkeypatch):
    from app import web_sessions, security_controls
    from app.business_time import business_now
    d = lifecycle
    setting(d, 20)
    alerts.reconcile_stock_alerts()
    alert_id = current(d)['alert_id']
    session = dict(account_id=d['owner'], role_key='owner', csrf_token='stock-alert-token',
                   portal_expires_at=int(business_now().timestamp())+600)
    monkeypatch.setattr(web_sessions, '_load_session', lambda token: dict(session))
    monkeypatch.setattr(web_sessions, '_persist_session', lambda *args: None)
    monkeypatch.setattr(web_sessions, '_delete_session', lambda *args: None)
    monkeypatch.setattr(security_controls, 'CSRF_PROTECTION_ENABLED', True)
    review = f'/portal/owner/alerts/{alert_id}/review'
    close = f'/portal/owner/alerts/{alert_id}/status'
    with TestClient(main.app, headers={'Origin':'http://testserver'}) as client:
        assert client.post(review).status_code == 403
        assert client.post(close, data={'status':'resolved','reason':'A reason for closure'}).status_code == 403
        response = client.post(review, data={'csrf_token':session['csrf_token']}, follow_redirects=False)
        assert response.status_code == 303 and 'error=' not in response.headers['location']
        response = client.post(close, data={'csrf_token':session['csrf_token'],'status':'resolved','reason':'Override active risk'}, follow_redirects=False)
        assert 'error=' in response.headers['location'] and current(d)
        session.update(account_id=d['inventory'],role_key='team-leader',team_leader_role='inventory')
        assert client.post(review, data={'csrf_token':session['csrf_token']}, follow_redirects=False).status_code in {303,403}
        assert not fetch_one('SELECT * FROM owner_alert_reviews WHERE alert_id=%s AND account_id=%s', (alert_id,d['inventory']))


def test_replacement_reservations_are_part_of_low_stock_policy(lifecycle):
    from test_product_lifecycle import create_case, receive, substitute
    d = lifecycle
    product = substitute(200)
    case = create_case(d, 'replace')
    product_returns.review_product_return(case, d['sales'], True, 'Approved replacement quote', [(product, 1)])
    receive(d, case)
    product_returns.accept_replacement_quote(case, d['reseller'], 1)
    setting(d, 19)
    alerts.reconcile_stock_alerts()
    assert not fetch_one("SELECT * FROM alerts WHERE product_id=%s AND alert_type='low_stock' AND status<>'resolved'", (product,))
    product_returns.reserve_replacement(case, d['inventory'], 'Reserve physical replacement pack')
    alerts.reconcile_stock_alerts()
    assert fetch_one('SELECT available FROM finished_stock_availability WHERE item_id=%s', (product,))['available'] == 19
    assert fetch_one("SELECT * FROM alerts WHERE product_id=%s AND alert_type='low_stock' AND status<>'resolved'", (product,))
