"""Stock alert episodes and per-Owner reviews, separate from notification reads."""
from __future__ import annotations

from app.business_time import BUSINESS_ZONE, business_today
from ._shared import clean_row, fetch_all, fetch_one, get_transaction_cursor
from .notifications import _create_notification_cursor
from .superuser import _audit_cursor, business_setting


STATE_SQL = """
SELECT a.*, COALESCE(p.name, material.name, 'System alert') AS subject,
       b.batch_code, r.reviewed_at,
       CASE WHEN a.raw_material_id IS NOT NULL THEN false
            WHEN a.alert_type='low_stock' THEN s.item_id IS NOT NULL
            WHEN a.alert_type IN ('near_expiry','expired_batch') THEN b.batch_id IS NOT NULL
            ELSE false END AS stock_managed,
       CASE WHEN a.raw_material_id IS NOT NULL THEN false
            WHEN a.alert_type='low_stock' THEN COALESCE(s.is_active AND s.available<=%s,false)
            WHEN a.alert_type='expired_batch' THEN COALESCE(b.quantity_available>0
                 AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date,false)
            WHEN a.alert_type='near_expiry' THEN COALESCE(p.is_active AND b.quantity_available>0
                 AND b.quality_status='approved'
                 AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date
                 AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+%s,false)
            ELSE false END AS risk_active
FROM alerts a LEFT JOIN inventory_items p ON p.item_id=a.product_id
LEFT JOIN inventory_items material ON material.item_id=a.raw_material_id
LEFT JOIN inventory_batches b ON b.batch_id=a.product_batch_id
LEFT JOIN finished_stock_availability s ON s.item_id=a.product_id
LEFT JOIN owner_alert_reviews r ON r.alert_id=a.alert_id AND r.account_id=%s
"""


def _state_params(account_id):
    return (int(business_setting('inventory.low_stock_packs')),
            int(business_setting('inventory.near_expiry_days')), account_id)


def _alert_records(account_id: int | None, *, inventory_only: bool = False) -> list[dict]:
    scope = " WHERE a.alert_type IN ('low_stock','near_expiry','expired_batch')" if inventory_only else ""
    rows = clean_row(fetch_all(STATE_SQL + scope + " ORDER BY (a.status='resolved'), (a.severity<>'critical'), a.alert_id DESC",
                               _state_params(account_id)))
    for alert in rows:
        raised_date = alert['triggered_at'].astimezone(BUSINESS_ZONE).date()
        alert['age_days'] = max(0, (business_today() - raised_date).days)
    return rows


def owner_alerts(account_id: int) -> list[dict]:
    return _alert_records(account_id)


def inventory_alerts() -> list[dict]:
    # Inventory shares stock episodes, never another Owner's personal reviews.
    return _alert_records(None, inventory_only=True)


def stock_alert_workspace(owner_account_id: int | None = None) -> dict:
    from .owner_insights import owner_low_stock, owner_stock_snapshot
    threshold = int(business_setting('inventory.low_stock_packs'))
    return {'alerts': owner_alerts(owner_account_id) if owner_account_id is not None else inventory_alerts(),
            'stock_risk': owner_stock_snapshot(batch_limit=None),
            'low_stock': owner_low_stock(threshold), 'stock_threshold': threshold,
            'stock_alert_scan': reconciliation_status()}


def reconciliation_status() -> dict | None:
    return fetch_one("SELECT status,checked_at FROM system_status_reports WHERE component='stock_alerts'")


def _require_owner(cur, account_id):
    cur.execute("SELECT 1 FROM accounts WHERE account_id=%s AND account_type='owner' AND is_active", (account_id,))
    if not cur.fetchone():
        raise ValueError('An active Owner account is required.')


def review_alert(alert_id: int, account_id: int) -> dict:
    with get_transaction_cursor() as cur:
        _require_owner(cur, account_id)
        cur.execute('SELECT * FROM alerts WHERE alert_id=%s FOR UPDATE', (alert_id,))
        alert = cur.fetchone()
        if not alert:
            raise ValueError('Alert not found.')
        cur.execute("""INSERT INTO owner_alert_reviews(alert_id,account_id) VALUES(%s,%s)
            ON CONFLICT DO NOTHING RETURNING reviewed_at""", (alert_id, account_id))
        if cur.fetchone():
            _audit_cursor(cur, actor_account_id=account_id, actor_role='owner', action='reviewed_alert',
                          entity_type='alerts', entity_id=alert_id, reason='Owner reviewed this alert.')
    return clean_row(alert)


def _resolve(cur, alert, reason, *, account_id=None):
    if alert['status'] == 'resolved':
        return
    source = 'owner' if account_id is not None else 'automatic'
    cur.execute("""UPDATE alerts SET status='resolved',resolved_at=now(),
        resolution_source=%s,resolution_reason=%s WHERE alert_id=%s""", (source, reason, alert['alert_id']))
    _audit_cursor(cur, actor_account_id=account_id, actor_role='owner' if account_id is not None else 'system',
                  action='resolved_alert' if account_id is not None else 'automatically_resolved_alert',
                  entity_type='alerts', entity_id=alert['alert_id'], reason=reason,
                  before_state={'status': alert['status']}, after_state={'status': 'resolved', 'source': source})


def close_alert(alert_id: int, account_id: int, reason: str) -> dict:
    reason = ' '.join(reason.strip().split())
    if not 5 <= len(reason) <= 500:
        raise ValueError('Enter a reason using 5 to 500 characters.')
    params = _state_params(account_id)
    with get_transaction_cursor() as cur:
        _require_owner(cur, account_id)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('stock-alert-reconcile'))")
        cur.execute(STATE_SQL + ' WHERE a.alert_id=%s FOR UPDATE OF a', (*params, alert_id))
        alert = cur.fetchone()
        if not alert:
            raise ValueError('Alert not found.')
        if alert['risk_active']:
            raise ValueError('Stock risk is still active. Inventory must clear the stock condition first.')
        _resolve(cur, alert, reason, account_id=account_id)
        cur.execute("SELECT * FROM alerts WHERE alert_id=%s", (alert_id,))
        alert = cur.fetchone()
    return clean_row(alert)


def upsert_stock_alert(cur, *, kind, severity, product_id, batch_id, message, notify_owners):
    if kind == 'low_stock':
        conflict = """(product_id) WHERE alert_type='low_stock' AND product_id IS NOT NULL
            AND raw_material_id IS NULL AND status IN ('open','acknowledged')"""
    else:
        conflict = """(product_batch_id,alert_type) WHERE product_batch_id IS NOT NULL
            AND status IN ('open','acknowledged')"""
    cur.execute(f"""INSERT INTO alerts(alert_type,severity,product_id,product_batch_id,message)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT {conflict}
        DO UPDATE SET severity=EXCLUDED.severity,message=EXCLUDED.message RETURNING alert_id""",
        (kind, severity, product_id, batch_id, message))
    alert_id = int(cur.fetchone()['alert_id'])
    title = {'low_stock': 'Low stock requires attention', 'near_expiry': 'Batch expiry warning',
             'expired_batch': 'Expired batch requires action'}[kind]
    for role in ('team-leader', 'owner'):
        if role == 'owner' and (not notify_owners or severity != 'critical'):
            continue
        target = 'alerts'
        _create_notification_cursor(cur, recipient_role=role, category='inventory', severity=severity,
            title=title, message=message, target_url=f'/portal/{role}/{target}#stock-alert-{alert_id}',
            source_type='alerts', source_id=alert_id,
            dedupe_key=f'stock-alert-{alert_id}-{role}-{severity}')
    return alert_id


def batch_alert(cur, batch, near_days, critical_days, notify_owners):
    days = (batch['expiry_date'] - business_today()).days
    expired = days <= 0
    if batch['quantity_available'] <= 0 or (not expired and
            (not batch['is_active'] or batch['quality_status'] != 'approved' or days > near_days)):
        return
    kind = 'expired_batch' if expired else 'near_expiry'
    severity = 'critical' if days <= critical_days else 'warning'
    message = (f"{batch['name']} batch {batch['batch_code']}: {batch['quantity_available']:g} packs "
               f"{'expired' if expired else 'expire'} on {batch['expiry_date']}. "
               + ('Physical pull-out required.' if expired else f'{days} day(s) remaining.'))
    upsert_stock_alert(cur, kind=kind, severity=severity, product_id=batch['item_id'],
                       batch_id=batch['batch_id'], message=message, notify_owners=notify_owners)


def receipt_batch_alert(cur, batch_id, near_days, critical_days):
    cur.execute("""SELECT b.*,p.name,p.is_active FROM inventory_batches b
        JOIN inventory_items p USING(item_id) WHERE b.batch_id=%s""", (batch_id,))
    batch = cur.fetchone()
    batch_alert(cur, batch, near_days, critical_days, bool(business_setting('notifications.owner_critical_alerts')))


def reconcile_stock_alerts():
    """Run after expiry custody maintenance; only alert state is changed here."""
    threshold, near_days, _ = _state_params(None)
    critical_days = int(business_setting('inventory.critical_expiry_days'))
    notify_owners = bool(business_setting('notifications.owner_critical_alerts'))
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('stock-alert-reconcile')) AS locked")
        if not cur.fetchone()['locked']:
            return
        cur.execute(STATE_SQL + " WHERE a.status IN ('open','acknowledged') ORDER BY a.alert_id FOR UPDATE OF a",
                    (threshold, near_days, None))
        for alert in cur.fetchall():
            if alert['stock_managed'] and not alert['risk_active']:
                _resolve(cur, alert, 'Current stock conditions no longer trigger this alert.')
        cur.execute('SELECT * FROM finished_stock_availability WHERE is_active AND available<=%s ORDER BY item_id', (threshold,))
        for product in cur.fetchall():
            upsert_stock_alert(cur, kind='low_stock', severity='critical' if product['available'] == 0 else 'warning',
                product_id=product['item_id'], batch_id=None,
                message=f"{product['name']}: {product['available']:g} available packs; warning level {threshold} packs.",
                notify_owners=notify_owners)
        cur.execute("""SELECT b.*,p.name,p.is_active FROM inventory_batches b
            JOIN inventory_items p USING(item_id) WHERE p.item_type='finished_product'
            AND b.quantity_available>0 AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+%s
            ORDER BY b.expiry_date,b.batch_id""", (near_days,))
        for batch in cur.fetchall():
            batch_alert(cur, batch, near_days, critical_days, notify_owners)
        cur.execute("""INSERT INTO system_status_reports(component,status,checked_at)
            VALUES('stock_alerts','ok',now()) ON CONFLICT(component)
            DO UPDATE SET status='ok',checked_at=now()""")
