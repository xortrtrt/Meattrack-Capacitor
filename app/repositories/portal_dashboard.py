"""Bounded, read-only dashboard summaries using the destination queue predicates."""
from datetime import datetime
from app.database import clean_row, fetch_all, fetch_one
from app.conversation_notifications import unread_summary
from .dashboard_queues import (
    ALLOWED, RETURN_FROM, WAITING_STOCK, OPEN_RETURNS,
    dashboard_predicate, return_scope, review_predicate,
)


def return_dashboard_counts(account_id: int, role: str, keys: tuple[str, ...]) -> dict:
    fields, params = [], []
    for key in keys:
        predicate, values = (dashboard_predicate(role, key, account_id)
                             if key in ALLOWED.get(role, set()) else review_predicate(role, key, account_id))
        fields.append(f"COUNT(*) FILTER (WHERE {predicate}) AS {key}")
        params.extend(values)
    scope, scoped_params = return_scope(role, account_id)
    return {key: int(value or 0) for key, value in fetch_one(
        "SELECT " + ", ".join(fields) + RETURN_FROM + " WHERE true" + scope,
        tuple(params + scoped_params),
    ).items()}


def _queue(label, count, path, description, icon, *, urgent=False, waiting=False):
    return dict(label=label, count=int(count or 0), path=path, description=description,
                icon=icon, urgent=urgent, waiting=waiting)


def reseller_dashboard_summary(account_id: int | None) -> dict:
    if not account_id:
        return {"queue": [], "recent_orders": [], "actions": []}
    counts = fetch_one("""SELECT COUNT(*) FILTER(WHERE status='to_pay') to_pay,
        COUNT(*) FILTER(WHERE status='pending') pending, COUNT(*) FILTER(WHERE status='approved') approved
        FROM orders WHERE order_type='reseller' AND reseller_id=(SELECT reseller_id FROM accounts
        WHERE account_id=%s AND account_type='reseller')""", (account_id,))
    returns = return_dashboard_counts(account_id, 'reseller', ('customer_action',))
    queue = [
        _queue('Awaiting payment', counts['to_pay'], 'history?view=to_pay', 'Complete payment in your existing order', 'wallet-cards'),
        _queue('Under review', counts['pending'], 'history?view=pending', 'Your Sales Leader is checking the order', 'clipboard-check', waiting=True),
        _queue('Approved for fulfillment', counts['approved'], 'history?view=approved', 'Follow delivery in your order history', 'truck', waiting=True),
        _queue('Returns needing your action', returns['customer_action'], 'history?view=returns&dashboard_queue=customer_action',
               'Review an updated offer or complete receipt details', 'undo-2'),
    ]
    recent = clean_row(fetch_all("""SELECT o.order_id,o.order_date,o.total_amount,
        CASE WHEN COALESCE((SELECT SUM(f.original_credit_amount) FROM order_refunds f
             WHERE f.order_id=o.order_id AND f.status='completed'),0)>=o.total_amount
             AND o.total_amount>0 THEN 'refunded' ELSE o.status END status
        FROM orders o WHERE o.order_type='reseller' AND o.reseller_id=(SELECT reseller_id FROM accounts
        WHERE account_id=%s AND account_type='reseller') ORDER BY o.order_date DESC,o.order_id DESC LIMIT 5""", (account_id,)))
    predicate, values = dashboard_predicate('reseller', 'customer_action', account_id)
    scope, params = return_scope('reseller', account_id)
    actions = clean_row(fetch_all("SELECT r.return_id,r.order_id,r.updated_at," +
        "CASE WHEN r.accepted_revision IS DISTINCT FROM r.quote_revision THEN 'Review replacement offer' " +
        "ELSE 'Complete payment details' END next_step" + RETURN_FROM +
        " WHERE " + predicate + scope + " ORDER BY r.updated_at,r.return_id LIMIT 5", tuple(values + params)))
    return {"queue": queue, "recent_orders": recent, "actions": actions}


def leader_work_summary(account_id: int, leader_role: str) -> dict:
    if leader_role == 'inventory':
        return inventory_dashboard_summary()
    role = 'sales-supervisor' if leader_role == 'sales-supervisor' else 'sales'
    counts = fetch_one("""SELECT COUNT(*) FILTER(WHERE status='pending') paid_review,
        COUNT(*) FILTER(WHERE status='approved') ready_fulfillment,
        COUNT(*) FILTER(WHERE status='to_pay') to_pay,
        COUNT(*) FILTER(WHERE status='fulfilled') fulfilled,
        COUNT(*) FILTER(WHERE status='rejected') rejected,
        COUNT(*) FILTER(WHERE status='cancelled') cancelled,
        COUNT(*) FILTER(WHERE status='expired') expired,
        COUNT(*) total_orders
        FROM orders WHERE order_type='reseller' AND team_leader_account_id=%s""", (account_id,))
    keys = ('needs_review', 'mismatch', 'payment', 'waiting')
    if role == 'sales-supervisor':
        keys += ('delivery', 'followups')
    counts.update(return_dashboard_counts(account_id, role, keys))
    counts['unread'] = unread_summary(account_id, 'sales-supervisor' if role == 'sales-supervisor' else 'team-leader')['unread_conversations']
    rows = [
        _queue('Paid orders to review', counts['paid_review'], 'orders?status=pending', 'Check payment evidence and record your decision', 'clipboard-check'),
        _queue('Ready for fulfillment', counts['ready_fulfillment'], 'orders?status=approved', 'Complete delivery from reserved stock', 'truck'),
        _queue('Initial return review', counts['needs_review'] + counts['mismatch'], 'returns?queue=needs_review',
               'Review assigned requests before processor handoff', 'undo-2'),
        _queue('Unread conversations', counts['unread'], 'messages', 'Reply to your assigned resellers', 'message-circle'),
        _queue('Waiting on Supervisor/Owner', counts['waiting'], 'returns?queue=waiting', 'Track progress; processing belongs to Supervisor/Owner', 'clock-3', waiting=True),
    ]
    # Sales mismatch is a separate destination, never hidden in an unrelated count.
    rows[2]['count'] = counts['needs_review']
    rows.insert(3, _queue('Return mismatches to review', counts['mismatch'], 'returns?queue=mismatch',
                          'Review assigned requests that have not been transferred', 'package-x', urgent=True))
    cases, messages = [], []
    if role == 'sales-supervisor':
        from .refunds import refund_status_counts
        refunds = refund_status_counts('sales-supervisor', account_id)
        counts.update(fetch_one("""SELECT COUNT(*) inquiries FROM inquiries WHERE status IN ('pending','assigned','contacted')
            AND (assigned_team_leader_account_id=%s OR assigned_team_leader_account_id IS NULL)""", (account_id,)))
        rows = [
            _queue('Failed refunds', refunds.get('failed', 0), 'refunds?status=failed', 'Review the recorded failure before retrying', 'triangle-alert', urgent=True),
            _queue('Payment verification', counts['payment'], 'returns?queue=payment', 'Verify current receipt details and actual funds', 'receipt-text'),
            _queue('Receipt mismatches', counts['mismatch'], 'returns?queue=mismatch', 'Resolve the recorded return discrepancy', 'package-x', urgent=True),
            _queue('Assigned return reviews', counts['needs_review'], 'returns?queue=needs_review', 'Review accepted requests and settlement clearance', 'clipboard-check'),
            _queue('Open reseller follow-ups', counts['followups'], 'returns?dashboard_queue=followups', 'Resolve open issues, including completed cases', 'message-square'),
            _queue('Confirm replacement delivery', counts['delivery'], 'returns?dashboard_queue=delivery', 'Record delivery after Inventory warehouse release', 'truck'),
            _queue('Pending refunds', refunds.get('pending', 0), 'refunds?status=pending', 'Check eligibility in the existing refund workspace', 'wallet-cards'),
            _queue('Reseller inquiries', counts['inquiries'], 'inquiries?status=open', 'Review unresolved assigned and unassigned prospects', 'user-check'),
            *rows[:2], rows[4],
            _queue('Waiting on other roles', counts['waiting'], 'returns?queue=waiting',
                   'Track cases awaiting goods, stock, or customer input', 'clock-3', waiting=True),
        ]
        scope, params = return_scope(role, account_id)
        counts['open_cases'] = int(fetch_one('SELECT COUNT(*) total' + RETURN_FROM +
            ' WHERE ' + OPEN_RETURNS + scope, tuple(params))['total'])
        counts['pending_refunds'] = refunds.get('pending', 0)
        counts['failed_refunds'] = refunds.get('failed', 0)
        messages = supervisor_recent_messages(account_id)
        predicates, values, classifications = [], [], []
        labels = {'needs_review': 'Review request or clearance', 'mismatch': 'Resolve receipt mismatch',
                  'payment': 'Verify payment receipt', 'delivery': 'Confirm replacement delivery',
                  'followups': 'Resolve reseller follow-up'}
        for key in ('needs_review', 'mismatch', 'payment', 'delivery', 'followups'):
            predicate, bound = (dashboard_predicate(role, key, account_id) if key in ALLOWED[role]
                                else review_predicate(role, key, account_id))
            predicates.append(predicate)
            classifications.append(f"WHEN {predicate} THEN '{labels[key]}'")
            values += bound
        cases = clean_row(fetch_all("SELECT r.return_id,r.order_id,r.updated_at,r.status,a.name reseller_name, CASE " +
            " ".join(classifications) + " END next_step" +
            RETURN_FROM + " JOIN accounts a ON a.account_id=o.created_by_account_id WHERE (" +
            " OR ".join(predicates) + ")" + scope + " ORDER BY r.updated_at,r.return_id LIMIT 5", tuple(values + values + params)))
    active = sum(bool(row['count']) and not row['waiting'] for row in rows)
    return {"counts": clean_row(counts), "queue": rows, "active_queues": active, "messages": messages,
            "attention_total": active, "recent": [], "cases": cases}


def inventory_dashboard_summary() -> dict:
    from .superuser import business_setting
    from .owner_insights import owner_stock_snapshot
    threshold = int(business_setting('inventory.low_stock_packs'))
    counts = fetch_one("""SELECT
        (SELECT COUNT(*) FROM finished_stock_availability WHERE is_active AND available<=%s) low_stock,
        (SELECT COUNT(*) FROM finished_stock_availability WHERE is_active AND available>%s) healthy_products,
        (SELECT COUNT(*) FROM finished_stock_availability WHERE is_active AND available>0 AND available<=%s) low_products,
        (SELECT COUNT(*) FROM finished_stock_availability WHERE is_active AND available<=0) out_products,
        (SELECT COUNT(*) FROM finished_stock_availability WHERE is_active) products,
        COALESCE((SELECT SUM(available) FROM finished_stock_availability WHERE is_active),0) available,
        (SELECT COUNT(*) FROM inventory_batches WHERE quantity_available>0
          AND expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date) pending_pullouts,
        COALESCE((SELECT SUM(quantity) FROM all_stock_reservations),0) reserved,
        COALESCE((SELECT SUM(b.quantity_available) FROM inventory_batches b JOIN inventory_items i USING(item_id)
          WHERE i.item_type='finished_product'),0) physical_stock""", (threshold, threshold, threshold))
    counts.update(return_dashboard_counts(0, 'inventory', ('receipts', 'prepare', 'release')))
    counts['waiting'] = int(fetch_one("SELECT COUNT(*) total" + RETURN_FROM +
        " WHERE " + WAITING_STOCK)['total'])
    rows = [
        _queue('Expired warehouse batches', counts['pending_pullouts'], 'expired-stock', 'Expired goods still in the warehouse need physical pull-out', 'clock-alert', urgent=True),
        _queue('Return receipt checks', counts['receipts'], 'returns?dashboard_queue=receipts', 'Check complete returned goods and evidence', 'package-check'),
        _queue('Replacement preparation', counts['prepare'], 'returns?dashboard_queue=prepare', 'Check valid stock and prepare eligible FEFO packs', 'refresh-cw'),
        _queue('Replacement releases', counts['release'], 'returns?dashboard_queue=release', 'Payment and preparation checks cleared for warehouse release', 'truck'),
        _queue('Low-stock products', counts['low_stock'], 'alerts?category=low-stock', 'Available finished packs at the saved warning level', 'triangle-alert'),
    ]
    recent = clean_row(fetch_all("""SELECT m.movement_type action,i.name entity,i.unit,m.quantity_delta,m.created_at
        FROM inventory_movements m JOIN inventory_items i ON i.item_id=m.item_id
        WHERE m.movement_type<>'opening_balance' ORDER BY m.created_at DESC,m.movement_id DESC LIMIT 5"""))
    risk = owner_stock_snapshot(batch_limit=6)
    risk['critical_days'] = int(business_setting('inventory.critical_expiry_days'))
    counts['expiring_batches'] = int(fetch_one("""SELECT COUNT(*) total FROM inventory_batches b
        JOIN inventory_items i USING(item_id) WHERE i.item_type='finished_product' AND i.is_active
        AND b.quantity_available>0 AND b.quality_status='approved'
        AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date
        AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+%s""", (risk['near_days'],))['total'])
    risk['batches'] = clean_row(fetch_all("""SELECT b.batch_id,b.item_id product_id,i.name,b.batch_code,
        b.expiry_date,b.quantity_available quantity,
        b.expiry_date-(now() AT TIME ZONE 'Asia/Manila')::date days_left,
        b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date expired
        FROM inventory_batches b JOIN inventory_items i USING(item_id)
        WHERE b.quantity_available>0 AND (b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date
        OR (i.item_type='finished_product' AND i.is_active AND b.quality_status='approved'
          AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+%s))
        ORDER BY b.expiry_date,b.batch_id LIMIT 6""", (risk['near_days'],)))
    active = sum(bool(row['count']) for row in rows)
    return {"counts": clean_row(counts), "queue": rows, "active_queues": active,
            "attention_total": active, "recent": recent, "risk": risk, "cases": []}


def supervisor_recent_messages(account_id: int) -> list[dict]:
    """Bounded previews from both existing inboxes; never advances read cursors."""
    from app.reseller_messaging import leader_conversations
    from .replacement_messages import threads
    rows = []
    for row in threads(account_id, limit=3, messages_only=True):
        rows.append(dict(name=row['reseller_name'], content=row['last_message'],
            created_at=datetime.fromisoformat(row['last_message_at']), unread=row['unread_count'],
            context=f"Replacement #{row['return_id']}", path=f"messages?replacement={row['return_id']}"))
    for row in leader_conversations(account_id, limit=3, messages_only=True):
        rows.append(dict(name=row.get('business_name') or row['reseller_name'], content=row['last_message'],
            created_at=datetime.fromisoformat(row['last_message_at']), unread=row['unread_count'],
            context='Direct conversation', path=f"messages?reseller={row['reseller_account_id']}"))
    return sorted(rows, key=lambda row: row['created_at'].timestamp(), reverse=True)[:3]
