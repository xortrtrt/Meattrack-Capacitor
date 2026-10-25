"""Shared invariants for returned goods and financial settlement (cursor only)."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID


def fingerprint(*values) -> str:
    return hashlib.sha256(json.dumps(values, default=str, sort_keys=True).encode()).hexdigest()


def receipt_problems(cur, return_id: int) -> list[str]:
    cur.execute(
        """SELECT l.return_line_id,l.quantity,l.received_quantity,a.quantity fulfilled,
        oi.order_id,oi.unit_price original_price,l.unit_price,r.order_id case_order,
        a.verification_note,a.verified_by_account_id,
        COALESCE((SELECT SUM(x.quantity) FROM product_return_receipt_lines x
          JOIN product_return_receipts h USING(receipt_id)
          JOIN expired_holdings s USING(holding_id)
          JOIN expired_stock_movements m ON m.movement_id=x.movement_id
          WHERE x.return_line_id=l.return_line_id AND h.return_id=l.return_id
           AND s.return_line_id=l.return_line_id AND s.batch_id=a.batch_id
           AND s.product_id=oi.product_id AND s.source='return'
           AND s.expiry_date=b.expiry_date AND s.source_batch_code=b.batch_code
           AND h.received_by_account_id=m.actor_account_id AND h.location=s.location
           AND m.holding_id=s.holding_id AND m.action='return_receipt'
           AND x.quantity=m.quantity AND x.quantity=s.quantity_received
           AND s.quantity_remaining=s.quantity_received+COALESCE((SELECT SUM(d.quantity)
             FROM expired_stock_movements d WHERE d.holding_id=s.holding_id AND d.action='disposal'),0)),0) traced,
        COALESCE((SELECT SUM(x.quantity) FROM product_return_lines x JOIN product_returns c USING(return_id)
          WHERE x.allocation_id=l.allocation_id AND c.status<>'rejected'),0) claimed
        FROM product_return_lines l JOIN product_returns r USING(return_id)
        JOIN fulfillment_allocations a USING(allocation_id)
        JOIN inventory_batches b USING(batch_id) JOIN order_items oi USING(order_item_id)
        WHERE l.return_id=%s;""",
        (return_id,),
    )
    lines = cur.fetchall()
    problems = []
    if not lines:
        return ["No traceable approved return lines."]
    for line in lines:
        if line["order_id"] != line["case_order"] or line["unit_price"] != line["original_price"]:
            problems.append("Return attribution or original purchase price does not match the sale.")
        if line["quantity"] != line["received_quantity"] or line["received_quantity"] != line["traced"]:
            problems.append("Returned quantities and receipt/holding records do not reconcile.")
        if line["claimed"] > line["fulfilled"]:
            problems.append("Claims exceed the fulfilled batch allocation.")
    cur.execute("SELECT flags FROM product_return_reconciliation WHERE return_id=%s;", (return_id,))
    check = cur.fetchone()
    if check:
        problems.extend(check["flags"])
    return list(dict.fromkeys(problems))


def check_refund_eligibility(cur, refund: dict) -> None:
    cur.execute("SELECT * FROM orders WHERE order_id=%s;", (refund["order_id"],))
    order = cur.fetchone()
    collected = Decimal(str(order["payment_amount"] or 0))
    cur.execute("SELECT COALESCE(SUM(amount),0) total FROM order_refunds WHERE order_id=%s;", (refund["order_id"],))
    if collected <= 0 or cur.fetchone()["total"] > collected:
        raise ValueError("Refund obligations exceed the confirmed original payment. Review the sales record.")
    if not refund.get("return_id"):
        if order["status"] != "rejected" or not order["rejection_payment_received"] or refund["amount"] != collected:
            raise ValueError("A rejected-order refund requires the full confirmed original payment.")
        return
    cur.execute("SELECT * FROM product_returns WHERE return_id=%s;", (refund["return_id"],))
    case = cur.fetchone()
    if (
        not case
        or case["order_id"] != refund["order_id"]
        or not case["verified_at"]
        or not case["approved_by_account_id"]
    ):
        raise ValueError("Final Inventory verification and Sales approval are required before settlement.")
    if case["status"] not in {"settlement_pending", "completed"} or order["status"] != "fulfilled":
        raise ValueError("Resolve the return discrepancy and verify returned items before settlement.")
    problems = receipt_problems(cur, case["return_id"])
    if problems:
        raise ValueError(problems[0])
    cur.execute(
        "SELECT SUM(quantity*unit_price) credit FROM product_return_lines WHERE return_id=%s;", (case["return_id"],)
    )
    if cur.fetchone()["credit"] != case["credit"]:
        raise ValueError("Return credit does not match the original sale lines.")
    expected = case["credit"]
    if case["resolution"] == "replace":
        if not case["replacement_released_at"] or case["accepted_revision"] != case["quote_revision"]:
            raise ValueError("The accepted replacement must be released before its refund is settled.")
        expected = max(Decimal(0), case["credit"] - case["replacement_total"])
    if refund["amount"] != expected or expected <= 0:
        raise ValueError("Refund amount does not match the verified return credit or replacement difference.")


def replay_refund_operation(
    cur, key: str | None, refund_id: int, actor_id: int, action: str, request_hash: str
) -> int | None:
    if not key:
        return None
    key = str(UUID(key))
    cur.execute("SELECT * FROM refund_operations WHERE operation_key=%s;", (key,))
    row = cur.fetchone()
    if not row:
        return None
    if (row["refund_id"], row["actor_account_id"], row["action"], row["request_hash"]) != (
        refund_id,
        actor_id,
        action,
        request_hash,
    ):
        raise ValueError("This operation reference was used with different details.")
    return int(row["attempt_id"])


def record_refund_operation(cur, key, refund_id, actor_id, action, request_hash, attempt_id) -> None:
    if key:
        cur.execute(
            "INSERT INTO refund_operations(operation_key,refund_id,actor_account_id,action,request_hash,attempt_id) VALUES(%s,%s,%s,%s,%s,%s);",
            (str(UUID(key)), refund_id, actor_id, action, request_hash, attempt_id),
        )
