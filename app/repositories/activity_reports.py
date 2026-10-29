"""Personally attributed, immutable Sales Activity Reports.

Dates select original orders. Return/refund work never claims the original sale.
Money is aggregated as Decimal before converting to the document's JSON boundary.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from psycopg2.errors import UniqueViolation
from psycopg2.extras import Json

from app.business_time import business_now, business_today
from app.database import fetch_one, get_transaction_cursor
from ._shared import _add_log_cursor
from .notifications import _create_notification_cursor

ORDER_ACTIONS = ("created_walk_in_sale", "approved_reseller_order", "rejected_reseller_order", "fulfilled_reseller_order")
RETURN_ACTIONS = ("sales_approved", "rejected", "settlement_authorized", "payment_verified")
REFUND_ACTIONS = ("sent_refund_conversation_message", "started_manual_refund", "failed_manual_refund", "completed_manual_refund", "took_over_manual_refund")
SECTIONS = ("summary", "sales", "orders", "returns", "refunds")


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def validate_period(start: date, end: date) -> None:
    if end < start:
        raise ValueError("End date must be on or after the start date.")
    if end > business_today():
        raise ValueError("Choose an end date on or before today.")


def _build(cur, actor_id: int, start: date, end: date) -> dict:
    validate_period(start, end)
    cur.execute("""SELECT account_id,name FROM accounts WHERE account_id=%s AND is_active
        AND account_type='team_leader' AND team_leader_role IN ('sales','sales-supervisor');""", (actor_id,))
    actor = cur.fetchone()
    if not actor:
        raise ValueError("A signed-in Sales account is required.")
    # Current access and explicit personal actions are both required. Assignment alone is insufficient.
    cur.execute("""SELECT o.order_id,o.order_type,o.status,o.order_date,
        COALESCE(r.business_name,'Retail counter') customer,o.total_amount,
        COALESCE(f.amount,0) completed_refunds,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('action',l.action,'at',l.created_at)
            ORDER BY l.created_at,l.activity_log_id) FROM activity_logs l
            WHERE l.account_id=%s AND l.entity_type='orders' AND l.entity_id=o.order_id
            AND l.action=ANY(%s) AND l.outcome='success'),'[]') actions,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('product',p.name,'quantity',i.quantity,
            'unit',i.unit,'unit_price',i.unit_price,'line_total',i.line_total) ORDER BY i.order_item_id)
            FROM order_items i JOIN inventory_items p ON p.item_id=i.product_id
            WHERE i.order_id=o.order_id),'[]') products
        FROM orders o LEFT JOIN resellers r USING(reseller_id)
        LEFT JOIN LATERAL (SELECT SUM(amount) amount FROM order_refunds
            WHERE order_id=o.order_id AND status='completed') f ON true
        WHERE (o.order_date AT TIME ZONE 'Asia/Manila')::date BETWEEN %s AND %s
        AND ((o.order_type='walk_in' AND o.created_by_account_id=%s) OR o.team_leader_account_id=%s)
        AND EXISTS(SELECT 1 FROM activity_logs l WHERE l.entity_type='orders' AND l.entity_id=o.order_id
            AND l.account_id=%s AND l.action=ANY(%s) AND l.outcome='success')
        ORDER BY o.order_date,o.order_id;""",
        (actor_id, list(ORDER_ACTIONS), start, end, actor_id, actor_id, actor_id, list(ORDER_ACTIONS)))
    orders = list(cur.fetchall())
    sales = []
    for order in orders:
        if order["status"] == "fulfilled":
            if order["completed_refunds"] > order["total_amount"]:
                raise ValueError(f"Order #{order['order_id']} requires financial reconciliation before reporting.")
            sales.append({**order, "gross_sales": order["total_amount"],
                          "retained_sales": order["total_amount"] - order["completed_refunds"]})
    cur.execute("""SELECT c.return_id,c.order_id,c.status,c.resolution,c.credit,c.created_at,
        COALESCE(r.business_name,'Retail counter') customer,
        (SELECT jsonb_agg(jsonb_build_object('action',e.action,'at',e.created_at,'remarks',e.remarks)
            ORDER BY e.created_at,e.event_id) FROM product_return_events e
            WHERE e.return_id=c.return_id AND e.actor_account_id=%s AND e.action=ANY(%s)) actions,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('product',p.name,'quantity',l.quantity,
            'received_quantity',l.received_quantity,'unit_price',l.unit_price) ORDER BY l.return_line_id)
            FROM product_return_lines l JOIN fulfillment_allocations a USING(allocation_id)
            JOIN order_items i USING(order_item_id) JOIN inventory_items p ON p.item_id=i.product_id
            WHERE l.return_id=c.return_id),'[]') products
        FROM product_returns c JOIN orders o USING(order_id) LEFT JOIN resellers r USING(reseller_id)
        WHERE (o.order_date AT TIME ZONE 'Asia/Manila')::date BETWEEN %s AND %s
        AND o.team_leader_account_id=%s
        AND EXISTS(SELECT 1 FROM product_return_events e WHERE e.return_id=c.return_id
            AND e.actor_account_id=%s AND e.action=ANY(%s)) ORDER BY c.return_id;""",
        (actor_id, list(RETURN_ACTIONS), start, end, actor_id, actor_id, list(RETURN_ACTIONS)))
    returns = list(cur.fetchall())
    return_ids = [row["return_id"] for row in returns]
    cur.execute("""SELECT f.order_refund_id,f.order_id,f.return_id,f.status,f.amount,f.requested_at,f.completed_at,
        COALESCE(r.business_name,'Retail counter') customer,
        CASE WHEN f.return_id IS NULL THEN 'Cancellation refund'
            WHEN c.resolution='replace' THEN 'Replacement difference' ELSE 'Product return refund' END refund_type,
        processor.name processor,requester.name requested_by,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('action',l.action,'at',l.created_at)
            ORDER BY l.created_at,l.activity_log_id) FROM activity_logs l
            WHERE l.entity_type='order_refunds' AND l.entity_id=f.order_refund_id
            AND l.account_id=%s AND l.action=ANY(%s) AND l.outcome='success'),'[]') actions
        FROM order_refunds f JOIN orders o USING(order_id)
        LEFT JOIN resellers r USING(reseller_id) LEFT JOIN product_returns c ON c.return_id=f.return_id
        LEFT JOIN accounts processor ON processor.account_id=f.processing_by_account_id
        LEFT JOIN accounts requester ON requester.account_id=f.requested_by_account_id
        WHERE (o.order_date AT TIME ZONE 'Asia/Manila')::date BETWEEN %s AND %s
        AND (o.team_leader_account_id=%s OR (o.order_type='walk_in' AND o.created_by_account_id=%s)
            OR f.assigned_supervisor_account_id=%s)
        AND (f.requested_by_account_id=%s OR f.return_id=ANY(%s)
            OR EXISTS(SELECT 1 FROM activity_logs l WHERE l.entity_type='order_refunds'
            AND l.entity_id=f.order_refund_id AND l.account_id=%s AND l.action=ANY(%s) AND l.outcome='success'))
        ORDER BY f.order_refund_id;""",
        (actor_id, list(REFUND_ACTIONS), start, end, actor_id, actor_id, actor_id, actor_id,
         return_ids, actor_id, list(REFUND_ACTIONS)))
    refunds = list(cur.fetchall())
    def total(rows, key):
        return sum((row[key] for row in rows), Decimal(0))
    summary = {
        "gross_sales": total(sales, "gross_sales"), "sales_refunds": total(sales, "completed_refunds"),
        "retained_sales": total(sales, "retained_sales"), "orders_handled": len(orders),
        "fulfilled_orders": len(sales), "retained_orders": sum(row["retained_sales"] > 0 for row in sales),
        "returns_handled": len(returns), "return_credit": total(returns, "credit"),
        "refunds_handled": len(refunds),
        "completed_refunds": total([r for r in refunds if r["status"] == "completed"], "amount"),
        "pending_refunds": total([r for r in refunds if r["status"] != "completed"], "amount"),
        "cancellation_refunds": total([r for r in refunds if r["status"] == "completed" and r["return_id"] is None], "amount"),
    }
    return _jsonable({"version": 1, "author": dict(actor), "period_start": start, "period_end": end,
                      "captured_at": business_now(), "summary": summary, "sales": sales,
                      "orders": orders, "returns": returns, "refunds": refunds})


def build_report(actor_id: int, start: date, end: date) -> dict:
    with get_transaction_cursor(repeatable_read=True) as cur:
        return _build(cur, actor_id, start, end)


def fingerprint(payload: dict) -> str:
    source = {k: v for k, v in payload.items() if k not in {"captured_at", "notes", "section_notes", "report_id", "submitted_at"}}
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def with_notes(payload: dict, notes: str = "", section_notes: dict | None = None) -> dict:
    notes = notes.strip()
    section_notes = {k: str((section_notes or {}).get(k, "")).strip() for k in SECTIONS[1:]}
    if len(notes) > 4000 or any(len(value) > 2000 for value in section_notes.values()):
        raise ValueError("Keep report notes within 4,000 characters and section notes within 2,000 characters.")
    return {**payload, "notes": notes, "section_notes": section_notes}


class StalePreview(ValueError):
    pass


def submit_report(actor_id: int, start: date, end: date, expected: str, submission_key: str,
                  notes: str = "", section_notes: dict | None = None) -> int:
    key = str(UUID(submission_key))
    existing = fetch_one("SELECT sales_report_id,author_account_id FROM sales_report_activity_snapshots WHERE submission_key=%s", (key,))
    if existing:
        if existing["author_account_id"] != actor_id:
            raise ValueError("Invalid submission key.")
        return int(existing["sales_report_id"])
    try:
        with get_transaction_cursor(repeatable_read=True) as cur:
            payload = _build(cur, actor_id, start, end)
            if fingerprint(payload) != expected:
                raise StalePreview("Records changed since the preview. Review the refreshed report before submitting.")
            payload = with_notes(payload, notes, section_notes)
            cur.execute("""INSERT INTO sales_reports(report_source,submitted_by_account_id,period_start,period_end,
                total_sales,total_orders,notes,refund_separation_version) VALUES('team_leader',%s,%s,%s,%s,%s,%s,1)
                RETURNING sales_report_id,submitted_at;""", (actor_id, start, end, payload["summary"]["retained_sales"],
                payload["summary"]["retained_orders"], notes.strip()))
            report = cur.fetchone()
            report_id = int(report["sales_report_id"])
            payload.update(report_id=report_id, submitted_at=report["submitted_at"].isoformat())
            cur.execute("""INSERT INTO sales_report_activity_snapshots(sales_report_id,author_account_id,
                submission_key,captured_at,payload) VALUES(%s,%s,%s,%s,%s)""",
                (report_id, actor_id, key, payload["captured_at"], Json(payload)))
            # Preserve original order/refund membership for existing traceability tools.
            for row in payload["sales"]:
                cur.execute("INSERT INTO sales_report_orders VALUES(%s,%s,%s,%s)",
                            (report_id, row["order_id"], row["gross_sales"], row["completed_refunds"]))
                cur.execute("""INSERT INTO sales_report_refunds SELECT %s,order_refund_id,amount FROM order_refunds
                    WHERE order_id=%s AND status='completed'""", (report_id, row["order_id"]))
            _add_log_cursor(cur, actor_account_id=actor_id, action="submitted_sales_activity_report",
                            entity_type="sales_reports", entity_id=report_id)
            _create_notification_cursor(cur, recipient_role="owner", category="report", title="Sales activity report submitted",
                message=f"{payload['author']['name']} submitted report #{report_id} for {start} to {end}.",
                target_url=f"/portal/owner/reports/{report_id}", source_type="sales_reports", source_id=report_id,
                dedupe_key=f"sales-activity-report:{report_id}")
            return report_id
    except UniqueViolation:
        existing = fetch_one("SELECT sales_report_id FROM sales_report_activity_snapshots WHERE submission_key=%s AND author_account_id=%s", (key, actor_id))
        if existing:
            return int(existing["sales_report_id"])
        raise


def saved_report(report_id: int, actor_id: int, *, owner: bool = False) -> dict | None:
    row = fetch_one("""SELECT payload FROM sales_report_activity_snapshots WHERE sales_report_id=%s
        AND (%s OR author_account_id=%s)""", (report_id, owner, actor_id))
    return row["payload"] if row else None
