"""Sales reads follow current reseller assignments for both staff roles."""
from datetime import timedelta

import pytest

from app import repositories as data
from app.business_time import business_today
from app.database import get_transaction_cursor
from app.repositories import activity_reports
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


def test_sales_visibility_follows_assignment_and_owner_keeps_all(lifecycle):  # noqa: F811
    d = lifecycle
    start, end = business_today() - timedelta(days=2), business_today()
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO activity_logs(account_id,action,entity_type,entity_id) VALUES(%s,'fulfilled_reseller_order','orders',%s)", (d['sales'], d['order']))
    for assigned, other in [(d['sales'], d['supervisor']), (d['supervisor'], d['sales'])]:
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE resellers SET team_leader_account_id=%s WHERE reseller_id=(SELECT reseller_id FROM orders WHERE order_id=%s)", (assigned, d['order']))
        assert [o['order_id'] for o in data.list_orders(order_type='reseller', team_leader_account_id=assigned)] == [d['order']]
        assert data.count_orders(order_type='reseller', team_leader_account_id=assigned) == 1
        assert data.list_orders(order_type='reseller', team_leader_account_id=other) == []
        assert data.count_orders(order_type='reseller', team_leader_account_id=other) == 0
        assert data.current_metrics(team_leader_account_id=assigned)['fulfilled_sales'] == 1000
        assert data.current_metrics(team_leader_account_id=other)['fulfilled_sales'] == 0
        assert data.team_sales_report_entries(other) == []
        assert data.team_reseller_purchase_summary(other) == []
        assert data.team_sales_report_totals(start, end, other)['total_sales'] == 0
        assert data.team_sales_report_totals(start, end, assigned)['total_sales'] == 1000
        assert len(data.list_orders(order_type='reseller')) == 1
        assert data.current_metrics()['fulfilled_sales'] == 1000
    assert activity_reports.build_report(d['sales'], start, end)['sales'] == []
