"""Role dashboard counts, linked filters, and payment/stock boundaries in isolated schemas."""
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import repositories, main, web_sessions
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns, replacement_workflow, return_customer
from app.repositories.portal_dashboard import leader_work_summary, reseller_dashboard_summary, return_dashboard_counts
from test_inventory_postgres import strict_inventory_database, _seed_fulfillment  # noqa: F401
from test_product_lifecycle import lifecycle, substitute, refund_proof, create_case, receive, supporting_photo  # noqa: F401
from test_replacement_workflow import request as replacement_request, approve_receive, pay

pytestmark = pytest.mark.postgres


def session_for(monkeypatch, d, role):
    portal = 'sales-supervisor' if role == 'supervisor' else 'team-leader' if role in {'sales', 'inventory'} else role
    session = dict(account_id=d[role], role_key=portal,
                   team_leader_role='sales-supervisor' if role == 'supervisor' else role,
                   portal_expires_at=int(business_now().timestamp()) + 600)
    monkeypatch.setattr(web_sessions, '_load_session', lambda token: dict(session))
    monkeypatch.setattr(web_sessions, '_persist_session', lambda *args: None)
    monkeypatch.setattr(web_sessions, '_delete_session', lambda *args: None)
    return session, portal


def list_total(actor, key):
    rows = returns.list_product_returns(actor, dashboard_queue=key)
    return rows[0]['list_total'] if rows else 0


def test_expired_counter_matches_live_pullouts_before_worker_scan(lifecycle):  # noqa: F811
    d = lifecycle
    before = fetch_one('SELECT COUNT(*) n FROM expired_pullouts')['n']
    summary = leader_work_summary(d['inventory'], 'inventory')
    assert before == 0
    assert summary['counts']['pending_pullouts'] == len(returns.expired_stock_workspace()['pullouts']) == 1
    assert fetch_one('SELECT COUNT(*) n FROM expired_pullouts')['n'] == before
    assert summary['counts']['physical_stock'] == 30
    assert summary['risk']['counts']['available'] == 20
    assert any(row['count'] == 0 for row in summary['queue'])
    assert summary['attention_total'] == sum(bool(row['count']) for row in summary['queue'])


def test_v2_dashboard_queues_follow_payment_preparation_release_and_delivery(lifecycle):  # noqa: F811
    d = lifecycle
    case, _ = replacement_request(d, [(substitute(300), 1)])
    assert return_dashboard_counts(d['supervisor'], 'sales-supervisor', ('needs_review',))['needs_review'] == 1
    assert return_dashboard_counts(d['sales'], 'sales', ('needs_review', 'waiting')) == {'needs_review': 0, 'waiting': 1}
    approve_receive(d, case)
    assert list_total(d['reseller'], 'customer_action') == 1
    assert return_dashboard_counts(d['inventory'], 'inventory', ('prepare', 'release')) == {'prepare': 0, 'release': 0}
    assert leader_work_summary(d['inventory'], 'inventory')['counts']['waiting'] == 1
    details = dict(transaction_number='DASH' + uuid4().hex[:14], payment_amount='100',
                   payment_date=business_now().date().isoformat(), payment_time='10:00')
    returns.record_return_payment(case, d['reseller'], [refund_proof()], details)
    assert list_total(d['reseller'], 'customer_action') == 0
    counts = return_dashboard_counts(d['supervisor'], 'sales-supervisor', ('payment',))
    assert counts['payment'] == returns.return_queue_counts(d['supervisor'])['payment'] == 1
    assert returns.list_product_returns(d['supervisor'], queue='payment')[0]['return_id'] == case
    returns.verify_return_payment(case, d['supervisor'], details['transaction_number'], '100',
                                  'Verified funds in payment provider', funds_received=True)
    assert list_total(d['inventory'], 'prepare') == 1
    returns.reserve_replacement(case, d['inventory'], 'Prepare paid replacement packs')
    assert list_total(d['inventory'], 'prepare') == 0 and list_total(d['inventory'], 'release') == 1
    returns.release_replacement(case, d['inventory'], 'Release verified replacement packs')
    assert list_total(d['inventory'], 'release') == 0 and list_total(d['supervisor'], 'delivery') == 1
    replacement_workflow.deliver(case, d['supervisor'], 'Delivered to reseller contact')
    assert list_total(d['supervisor'], 'delivery') == 0
    with get_transaction_cursor() as cur:
        row = returns._case(cur, case, d['reseller'], {'reseller'}, read_only=True)
        return_customer.report_followup(cur, row, d['reseller'], 'Please check the replacement delivery', str(uuid4()))
    assert list_total(d['supervisor'], 'followups') == 1
    assert return_dashboard_counts(d['sales'], 'sales', ('needs_review', 'waiting')) == {'needs_review': 0, 'waiting': 1}


def test_legacy_reservation_before_payment_is_preserved(lifecycle):  # noqa: F811
    d = lifecycle
    case = create_case(d, 'replace')
    replacement = substitute(300)
    returns.review_product_return(case, d['sales'], True, 'Approved initial return request', [(replacement, 1)])
    returns.accept_replacement_quote(case, d['reseller'], 1)
    receive(d, case, authorize=False)
    assert leader_work_summary(d['inventory'], 'inventory')['counts']['waiting'] == 1
    assert list_total(d['inventory'], 'prepare') == 0
    returns.authorize_return_settlement(case, d['supervisor'], 'Confirm verified replacement resolution', str(uuid4()))
    assert list_total(d['inventory'], 'prepare') == 1
    assert list_total(d['inventory'], 'release') == 0
    returns.reserve_replacement(case, d['inventory'], 'Prepare stock before legacy payment')
    assert list_total(d['reseller'], 'customer_action') == 1
    pay(d, case, '100')
    assert list_total(d['inventory'], 'release') == 1


def test_most_ordered_uses_fulfilled_line_prices_and_not_unpaid_orders(lifecycle):  # noqa: F811
    d = lifecycle
    repositories.update_product_price(d['product'], '999.50')
    repositories.create_order_from_items('reseller', [(d['product'], 2)], account_id=d['reseller'])
    product = repositories.reseller_most_bought_products(account_id=d['reseller'])[0]
    assert product['total_quantity'] == 10 and product['order_count'] == 1
    assert product['total_amount'] == 1000 and Decimal(str(product['base_price'])) == Decimal('999.50')
    summary = reseller_dashboard_summary(d['reseller'])
    assert next(row for row in summary['queue'] if row['label'] == 'Awaiting payment')['count'] == 1
    assert summary['recent_orders'][0]['status'] == 'to_pay'


def test_derived_filters_authentication_roles_and_visibility(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case, _ = replacement_request(d)
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,team_leader_role,name,email,password_hash) VALUES('team_leader','sales-supervisor','Other supervisor','other@dashboard.test','hash') RETURNING account_id")
        other = cur.fetchone()['account_id']
    assert leader_work_summary(other, 'sales-supervisor')['cases'] == []
    session, _ = session_for(monkeypatch, d, 'supervisor')
    with TestClient(main.app) as client:
        assert client.get('/portal/sales-supervisor/returns?dashboard_queue=prepare').status_code == 400
        session_for(monkeypatch, d, 'inventory')
        response = client.get('/portal/team-leader/returns?dashboard_queue=receipts')
        assert response.status_code == 200 and 'Clear dashboard filter' in response.text
        assert 'Current stock (not reserved)' not in response.text
        session_for(monkeypatch, d, 'reseller')
        assert client.get('/portal/reseller/history?view=returns&dashboard_queue=release').status_code == 400
        assert client.get('/portal/reseller/history?view=returns&dashboard_queue=customer_action').status_code == 200
        session.clear()
        monkeypatch.setattr(web_sessions, '_load_session', lambda token: {})
        assert client.get('/portal/team-leader/returns?dashboard_queue=receipts', follow_redirects=False).status_code == 303
    assert returns.get_product_return(case, d['supervisor'])['status'] == 'submitted'


def test_counts_are_not_limited_to_preview_pages_and_gets_do_not_write(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    # Legacy incomplete requests still need review. Seed only the disposable schema.
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO product_returns(order_id,requested_by_account_id,resolution,remarks)
            SELECT %s,%s,'refund','Legacy request requiring review' FROM generate_series(1,15)""",
            (d['order'], d['reseller']))
    counts = returns.return_queue_counts(d['sales'])
    rows = returns.list_product_returns(d['sales'], queue='needs_review')
    assert counts['needs_review'] == 15 and len(rows) == 12 and rows[0]['list_total'] == 15
    assert leader_work_summary(d['sales'], 'sales')['counts']['needs_review'] == 15
    def snapshot():
        return fetch_one("""SELECT
            (SELECT COUNT(*) FROM inventory_movements) movements,
            (SELECT COUNT(*) FROM expired_pullouts) pullouts,
            (SELECT COUNT(*) FROM product_return_events) events,
            (SELECT COUNT(*) FROM notifications WHERE read_at IS NOT NULL) read_notifications,
            (SELECT COUNT(*) FROM all_stock_reservations) reservations""")
    before = snapshot()
    with TestClient(main.app) as client:
        for role in ('owner','supervisor','sales','inventory','reseller'):
            _, portal = session_for(monkeypatch, d, role)
            response = client.get(f'/portal/{portal}/dashboard')
            assert response.status_code == 200
            assert 'queued items' not in response.text
    assert snapshot() == before


def test_owner_exception_counts_match_missing_active_coverage_filters(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = create_case(d)
    returns.review_product_return(case, d['sales'], True, 'Approve verified return refund')
    receive(d, case)
    with get_transaction_cursor() as cur:
        for index, status in enumerate(('pending', 'contacted', 'approved')):
            cur.execute("""INSERT INTO inquiries(name,business_name,email,contact_number,message,status,assigned_team_leader_account_id)
                VALUES('Test applicant',%s,%s,'09170000000','Request Sales coverage',%s,%s)""",
                (f'Coverage shop {index}', f'coverage-{index}@dashboard.test', status, d['sales']))
        cur.execute('UPDATE accounts SET is_active=false WHERE account_id IN (%s,%s)', (d['sales'], d['supervisor']))
    snapshot = repositories.owner_business_snapshot()
    assert snapshot['unassigned_resellers'] == repositories.count_owner_resellers(status='active', leader='unassigned') == 1
    assert snapshot['unassigned_inquiries'] == repositories.count_inquiries(missing_coverage=True) == 2
    assert snapshot['owner_refunds'] == repositories.count_order_refunds('owner', d['owner'], owner_only=True) == 1
    assert len(repositories.list_order_refunds('owner', d['owner'], owner_only=True)) == 1
    session_for(monkeypatch, d, 'owner')
    with TestClient(main.app) as client:
        response = client.get('/portal/owner/inquiries?coverage=missing')
        assert response.status_code == 200 and 'Clear coverage filter' in response.text
        response = client.get('/portal/owner/refunds?owner_only=1')
        assert response.status_code == 200 and 'name="owner_only" value="1"' in response.text
    with pytest.raises(ValueError, match='Only an Owner'):
        repositories.count_order_refunds('sales-supervisor', d['supervisor'], owner_only=True)


def test_derived_receipt_queue_pagination_retains_filter(strict_inventory_database, monkeypatch):  # noqa: F811
    import html
    import re
    sales, product, batches, orders = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[40], order_quantities=[20])
    repositories.decide_order(orders[0], 'fulfill', team_leader_account_id=sales)
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Inventory','pagination-inventory@dashboard.test','hash','inventory') RETURNING account_id")
        inventory = cur.fetchone()['account_id']
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Supervisor','pagination-supervisor@dashboard.test','hash','sales-supervisor')")
        cur.execute('SELECT created_by_account_id FROM orders WHERE order_id=%s', (orders[0],))
        reseller = cur.fetchone()['created_by_account_id']
        cur.execute('SELECT allocation_id FROM fulfillment_allocations a JOIN order_items oi USING(order_item_id) WHERE oi.order_id=%s', (orders[0],))
        allocation = cur.fetchone()['allocation_id']
    for _ in range(15):
        case = returns.create_product_return(orders[0], reseller, 'refund', [(allocation, 1)],
                                              'A traceable pack requires receipt checking', [supporting_photo()])
        returns.review_product_return(case, sales, True, 'Approved for complete receipt checking')
    assert return_dashboard_counts(inventory, 'inventory', ('receipts',))['receipts'] == 15
    rows = returns.list_product_returns(inventory, dashboard_queue='receipts')
    assert len(rows) == 12 and rows[0]['list_total'] == 15
    assert len(returns.list_product_returns(inventory, dashboard_queue='receipts', page=2)) == 3
    session_for(monkeypatch, {'inventory': inventory}, 'inventory')
    with TestClient(main.app) as client:
        response = client.get('/portal/team-leader/returns?dashboard_queue=receipts')
        assert response.status_code == 200
        links = [html.unescape(href) for href in re.findall(r'href="([^"]+)"', response.text)]
        next_page = next(href for href in links if 'dashboard_queue=receipts' in href and 'page=2' in href)
        assert client.get(next_page).status_code == 200


def test_reference_summary_partitions_and_message_previews_are_scoped_read_only(lifecycle, monkeypatch):  # noqa: F811
    from app.repositories import replacement_messages
    from app import reseller_messaging
    from app.repositories.portal_dashboard import supervisor_recent_messages
    d = lifecycle
    cases = [replacement_request(d)[0] for _ in range(4)]
    for index, case in enumerate(cases):
        replacement_messages.send(case, d['reseller'], f'Please review replacement request {index}', str(uuid4()))
    with get_transaction_cursor() as cur:
        cur.execute('SELECT COUNT(*) n FROM return_conversation_reads')
        reads_before = cur.fetchone()['n']
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Other supervisor','other-preview@dashboard.test','hash','sales-supervisor') RETURNING account_id")
        other_supervisor = cur.fetchone()['account_id']
    messages = supervisor_recent_messages(d['supervisor'])
    assert len(messages) == 3 and all(message['unread'] > 0 for message in messages)
    unread_before = [(row['return_id'], row['unread_count']) for row in replacement_messages.threads(d['supervisor'])]
    assert [message['path'] for message in messages] == [f'messages?replacement={case}' for case in reversed(cases[1:])]
    assert supervisor_recent_messages(other_supervisor) == []
    summary = leader_work_summary(d['supervisor'], 'sales-supervisor')
    open_cases = returns.list_product_returns(d['supervisor'], status='open')
    assert summary['counts']['open_cases'] == open_cases[0]['list_total'] == 4
    assert sum(row['count'] for row in summary['queue']) >= summary['counts']['open_cases']
    inventory = leader_work_summary(d['inventory'], 'inventory')['counts']
    assert inventory['products'] == sum(inventory[key] for key in ('healthy_products','low_products','out_products'))
    assert inventory['low_stock'] == inventory['low_products'] + inventory['out_products']
    expected = fetch_one("""SELECT COUNT(*) n FROM inventory_batches b JOIN inventory_items i USING(item_id)
        WHERE i.item_type='finished_product' AND i.is_active AND b.quantity_available>0 AND b.quality_status='approved'
        AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date
        AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+
          (SELECT setting_value::text::int FROM business_settings WHERE setting_key='inventory.near_expiry_days')""")
    assert inventory['expiring_batches'] == expected['n']
    with get_transaction_cursor() as cur:
        cur.execute('SELECT COUNT(*) n FROM return_conversation_reads')
        assert cur.fetchone()['n'] == reads_before
    assert [(row['return_id'], row['unread_count']) for row in replacement_messages.threads(d['supervisor'])] == unread_before
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE resellers SET team_leader_account_id=%s WHERE reseller_id=(SELECT reseller_id FROM accounts WHERE account_id=%s)", (d['supervisor'],d['reseller']))
    reseller_messaging.send_from_reseller(d['reseller'], str(uuid4()), 'A current assigned reseller message')
    messages = supervisor_recent_messages(d['supervisor'])
    assert messages[0]['path'] == f"messages?reseller={d['reseller']}"
    assert len(messages) == 3
    assert len(reseller_messaging.leader_conversations(d['supervisor'], limit=1, messages_only=True)) == 1
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE resellers SET team_leader_account_id=%s WHERE reseller_id=(SELECT reseller_id FROM accounts WHERE account_id=%s)", (d['sales'],d['reseller']))
    # Historic assigned conversations stay visible in the existing read-only inbox.
    assert any(message['path'].startswith('messages?reseller=') for message in supervisor_recent_messages(d['supervisor']))
    assert not any(message['path'].startswith('messages?reseller=') for message in supervisor_recent_messages(other_supervisor))
    session_for(monkeypatch, d, 'supervisor')
    with TestClient(main.app) as client:
        response = client.get('/portal/sales-supervisor/returns?status=open')
        assert response.status_code == 200 and 'Clear open-case filter' in response.text
        response = client.get('/portal/sales-supervisor/dashboard')
        assert response.status_code == 200 and 'Recent Reseller Messages' in response.text


def test_supervisor_inquiry_summary_includes_all_unresolved_statuses(lifecycle):  # noqa: F811
    d = lifecycle
    with get_transaction_cursor() as cur:
        for index, (status, assigned) in enumerate([('pending',None),('assigned',d['supervisor']),('contacted',d['supervisor']),('approved',d['supervisor']),('assigned',d['sales'])]):
            cur.execute("""INSERT INTO inquiries(name,business_name,email,contact_number,message,status,assigned_team_leader_account_id)
                VALUES('Preview prospect','Preview store',%s,'09123456789','Dashboard inquiry',%s,%s)""",
                (f'preview-{index}@dashboard.test',status,assigned))
    counts = leader_work_summary(d['supervisor'], 'sales-supervisor')['counts']
    assert counts['inquiries'] == repositories.count_inquiries(status='open',assigned_team_leader_account_id=d['supervisor'],include_unassigned=True) == 3
    assert len(repositories.list_inquiries(status='open',assigned_team_leader_account_id=d['supervisor'],include_unassigned=True)) == 3
