"""Sales reports keep their original snapshot and expose refunds separately."""

from __future__ import annotations

from decimal import Decimal


def report_order_rows(cur, start, end, actor_id, *, lock=False):
    scope = """((o.order_type='walk_in' AND o.created_by_account_id=%s)
        OR (o.order_type='reseller' AND o.team_leader_account_id=%s))"""
    params = (start, end, actor_id, actor_id)
    if actor_id is None:
        scope = "true"
        params = (start, end)
    period = "(COALESCE(o.fulfilled_at,o.order_date) AT TIME ZONE 'Asia/Manila')::date BETWEEN %s AND %s"
    if lock:
        cur.execute(
            f"SELECT o.order_id FROM orders o WHERE o.status='fulfilled' AND {period} AND {scope} ORDER BY o.order_id FOR UPDATE;",
            params,
        )
        locked_ids = [row["order_id"] for row in cur.fetchall()]
        scope += " AND o.order_id=ANY(%s)"
        params = (*params, locked_ids)
    cur.execute(
        f"""SELECT o.order_id,o.total_amount gross_sales,
        COALESCE(f.refunded,0) refunded_sales,GREATEST(0,o.total_amount-COALESCE(f.refunded,0)) retained_sales
        FROM orders o LEFT JOIN LATERAL (SELECT SUM(amount) refunded FROM order_refunds f
          WHERE f.order_id=o.order_id AND f.status='completed') f ON true
        WHERE o.status='fulfilled' AND {period} AND {scope} ORDER BY o.order_id;""",
        params,
    )
    return list(cur.fetchall())


def report_totals(rows):
    return {
        "gross_sales": sum((r["gross_sales"] for r in rows), Decimal(0)),
        "refunded_sales": sum((r["refunded_sales"] for r in rows), Decimal(0)),
        "total_sales": sum((r["retained_sales"] for r in rows), Decimal(0)),
        "total_orders": sum(r["retained_sales"] > 0 for r in rows),
    }


def snapshot_report_orders(cur, report_id, rows):
    for row in rows:
        if row["refunded_sales"] > row["gross_sales"]:
            raise ValueError(
                "Completed refunds exceed the original sale. Reconcile the order before submitting a report."
            )
        cur.execute(
            "INSERT INTO sales_report_orders(sales_report_id,order_id,gross_sales,refunded_at_submission) VALUES(%s,%s,%s,%s);",
            (report_id, row["order_id"], row["gross_sales"], row["refunded_sales"]),
        )
        cur.execute(
            "INSERT INTO sales_report_refunds(sales_report_id,order_refund_id,amount) SELECT %s,order_refund_id,amount FROM order_refunds WHERE order_id=%s AND status='completed';",
            (report_id, row["order_id"]),
        )
