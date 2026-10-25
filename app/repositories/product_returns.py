"""Transactional product returns and non-sellable stock custody."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from app.business_time import business_today
from app.database import clean_row, fetch_all, fetch_one, get_transaction_cursor
from app.inventory_measurements import parse_whole_packs
from ._shared import pagination_meta
from .inventory.allocation import _plan_fefo_allocations
from .inventory.movements import _insert_inventory_movement, _inventory_actor_name
from .notifications import _create_notification_cursor
from .refund_integrity import fingerprint, receipt_problems


def _text(value: str, label: str = "Remarks", minimum: int = 5, maximum: int = 1000) -> str:
    value = " ".join(str(value or "").split())
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must contain {minimum} to {maximum} characters.")
    return value


def _actor(cur, actor_id: int, allowed: set[str]) -> str:
    cur.execute(
        "SELECT account_type,team_leader_role FROM accounts WHERE account_id=%s AND is_active=true;", (actor_id,)
    )
    row = cur.fetchone()
    role = (
        row["team_leader_role"]
        if row and row["account_type"] == "team_leader"
        else (row["account_type"] if row else "")
    )
    if role not in allowed and role != "owner":
        raise ValueError("Your account cannot perform this action.")
    return role


def _case(cur, return_id: int, actor_id: int, allowed: set[str], *, read_only: bool = False) -> dict:
    role = _actor(cur, actor_id, allowed)
    if not read_only:
        cur.execute("SELECT order_id FROM product_returns WHERE return_id=%s;", (return_id,))
        parent = cur.fetchone()
        if parent:
            cur.execute("SELECT order_id FROM orders WHERE order_id=%s FOR UPDATE;", (parent["order_id"],))
    cur.execute(
        """SELECT r.*,o.created_by_account_id AS reseller_account_id,o.team_leader_account_id
        FROM product_returns r JOIN orders o USING(order_id) WHERE return_id=%s FOR UPDATE OF r;""",
        (return_id,),
    )
    case = cur.fetchone()
    supervisor_read = False
    if case and read_only and role == "sales-supervisor":
        cur.execute(
            "SELECT 1 FROM order_refunds WHERE return_id=%s AND (assigned_supervisor_account_id=%s OR (assigned_supervisor_account_id IS NULL AND status IN ('pending','failed')));",
            (return_id, actor_id),
        )
        supervisor_read = bool(cur.fetchone())
    if (
        not case
        or (role == "reseller" and case["reseller_account_id"] != actor_id)
        or (
            role in {"sales", "sales-supervisor"} and case["team_leader_account_id"] != actor_id and not supervisor_read
        )
    ):
        raise ValueError("Return case was not found or is not assigned to you.")
    return dict(case)


def _event(cur, case: dict, actor_id: int | None, action: str, remarks: str, operation_key: str | None = None,
           *, refund_id: int | None = None, attempt_id: int | None = None, amount=None, request_hash: str | None = None) -> None:
    cur.execute(
        "INSERT INTO product_return_events(return_id,actor_account_id,action,remarks,operation_key,refund_id,refund_attempt_id,amount,request_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING event_id;",
        (case["return_id"], actor_id, action, remarks, operation_key, refund_id, attempt_id, amount, request_hash),
    )
    event_id = cur.fetchone()["event_id"]
    cur.execute("UPDATE product_returns SET updated_at=now() WHERE return_id=%s;", (case["return_id"],))
    for recipient, role in (
        (case.get("reseller_account_id"), "reseller"),
        (case.get("team_leader_account_id"), "team-leader"),
    ):
        if recipient:
            if role == "team-leader":
                cur.execute("SELECT team_leader_role FROM accounts WHERE account_id=%s;", (recipient,))
                account = cur.fetchone()
                if account and account["team_leader_role"] == "sales-supervisor":
                    role = "sales-supervisor"
            _create_notification_cursor(
                cur,
                recipient_account_id=recipient,
                category="order",
                title=f"Return #{case['return_id']}: {action.replace('_', ' ')}",
                message=remarks,
                target_url=f"/portal/{role}/returns?case={case['return_id']}",
                source_type="product_returns",
                source_id=case["return_id"],
                dedupe_key=f"return-event-{event_id}-{recipient}",
            )
    _create_notification_cursor(
        cur,
        recipient_role="team-leader",
        category="inventory",
        title=f"Return #{case['return_id']}: {action.replace('_', ' ')}",
        message=remarks,
        target_url=f"/portal/team-leader/returns?case={case['return_id']}",
        source_type="product_returns",
        source_id=case["return_id"],
        dedupe_key=f"return-event-{event_id}-inventory",
    )


def _attachments(cur, return_id: int, actor_id: int, attachments: list[dict], purpose: str, revision: int = 0) -> None:
    for a in attachments:
        if purpose == "payment":
            cur.execute(
                """SELECT 1 FROM return_attachments WHERE purpose='payment' AND checksum_sha256=%s AND return_id<>%s
                UNION ALL SELECT 1 FROM order_payment_proofs WHERE checksum_sha256=%s LIMIT 1;""",
                (a["checksum_sha256"], return_id, a["checksum_sha256"]),
            )
            if cur.fetchone():
                raise ValueError("This payment screenshot has already been used.")
        cur.execute(
            """INSERT INTO return_attachments(return_id,purpose,revision,filename,content_type,content,size_bytes,checksum_sha256,uploaded_by_account_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING;""",
            (
                return_id,
                purpose,
                revision,
                a["filename"],
                a["content_type"],
                a["content"],
                a["size_bytes"],
                a["checksum_sha256"],
                actor_id,
            ),
        )


def create_product_return(
    order_id: int,
    actor_id: int,
    resolution: str,
    lines: list[tuple[int, object]],
    remarks: str,
    attachments: list[dict] | None = None,
    operation_key: str | None = None,
) -> int:
    remarks = _text(remarks)
    if resolution not in {"refund", "replace"} or not lines:
        raise ValueError("Select items and choose refund or replacement.")
    if len({int(a) for a, _ in lines}) != len(lines):
        raise ValueError("Select each batch allocation only once.")
    request_hash = fingerprint(order_id,actor_id,resolution,sorted((int(i),str(parse_whole_packs(q))) for i,q in lines),remarks,sorted(a["checksum_sha256"] for a in attachments or []))
    with get_transaction_cursor() as cur:
        _actor(cur, actor_id, {"reseller"})
        cur.execute(
            "SELECT * FROM orders WHERE order_id=%s AND created_by_account_id=%s AND order_type='reseller' AND status='fulfilled' FOR UPDATE;",
            (order_id, actor_id),
        )
        order = cur.fetchone()
        if not order:
            raise ValueError("Only your fulfilled reseller orders can be returned.")
        if operation_key:
            operation_key = str(UUID(operation_key))
            cur.execute(
                "SELECT return_id,order_id,requested_by_account_id,request_hash FROM product_returns WHERE operation_key=%s;",
                (operation_key,),
            )
            existing = cur.fetchone()
            if existing:
                if (existing["order_id"],existing["requested_by_account_id"],existing["request_hash"]) != (order_id,actor_id,request_hash):
                    raise ValueError("This return reference was used with different details. Refresh the request.")
                return int(existing["return_id"])
        selected = []
        for allocation_id, value in sorted(lines):
            quantity = parse_whole_packs(value)
            cur.execute(
                """SELECT a.*,oi.unit_price,oi.order_id,b.expiry_date FROM fulfillment_allocations a
                JOIN order_items oi USING(order_item_id) JOIN inventory_batches b USING(batch_id)
                WHERE allocation_id=%s AND oi.order_id=%s FOR UPDATE OF a;""",
                (allocation_id, order_id),
            )
            allocation = cur.fetchone()
            if not allocation or allocation["expiry_date"] > business_today():
                raise ValueError("Select a fulfilled batch that has reached its expiration date.")
            cur.execute(
                """SELECT COALESCE(SUM(l.quantity),0) AS claimed FROM product_return_lines l
                JOIN product_returns r USING(return_id) WHERE l.allocation_id=%s AND r.status<>'rejected';""",
                (allocation_id,),
            )
            if quantity + cur.fetchone()["claimed"] > allocation["quantity"]:
                raise ValueError("Return quantity exceeds the remaining eligible quantity.")
            selected.append((allocation_id, quantity, allocation["unit_price"]))
        cur.execute(
            """INSERT INTO product_returns(order_id,resolution,remarks,requested_by_account_id,credit,operation_key,request_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *;""",
            (order_id, resolution, remarks, actor_id, sum(q * p for _, q, p in selected), operation_key, request_hash),
        )
        case = dict(cur.fetchone())
        case.update(reseller_account_id=actor_id, team_leader_account_id=order["team_leader_account_id"])
        for allocation_id, quantity, price in selected:
            cur.execute(
                "INSERT INTO product_return_lines(return_id,allocation_id,quantity,unit_price) VALUES(%s,%s,%s,%s);",
                (case["return_id"], allocation_id, quantity, price),
            )
        _attachments(cur, case["return_id"], actor_id, attachments or [], "evidence")
        _event(cur, case, actor_id, "submitted", remarks)
        return int(case["return_id"])


def review_product_return(
    return_id: int,
    actor_id: int,
    approve: bool,
    remarks: str,
    replacements: list[tuple[int, object]] | None = None,
    revised_quantities: list[tuple[int, object]] | None = None,
    operation_key: str | None = None,
) -> None:
    remarks = _text(remarks)
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"sales", "sales-supervisor"})
        request_hash = fingerprint(return_id,actor_id,approve,remarks,replacements,revised_quantities)
        if operation_key:
            operation_key = str(UUID(operation_key))
            cur.execute(
                "SELECT * FROM product_return_events WHERE operation_key=%s;",
                (operation_key,),
            )
            previous = cur.fetchone()
            if previous:
                if previous["return_id"] != return_id or previous["request_hash"] != request_hash:
                    raise ValueError("This review reference was used with different details.")
                return
        if (
            case["status"] not in {"submitted", "discrepancy", "awaiting_receipt", "awaiting_inventory_verification", "settlement_pending"}
            or case["replacement_released_at"]
            or case["payment_verified_at"]
        ):
            raise ValueError("This return can no longer be changed.")
        if not approve:
            if case["status"] != "submitted":
                raise ValueError("Only a submitted request can be rejected.")
            cur.execute("UPDATE product_returns SET status='rejected' WHERE return_id=%s;", (return_id,))
            _event(cur, case, actor_id, "rejected", remarks, operation_key,request_hash=request_hash)
            return
        if revised_quantities:
            if case["status"] != "discrepancy" or case["verified_at"]:
                raise ValueError("Quantity revisions require a documented receipt discrepancy.")
            if len({int(i) for i, _ in revised_quantities}) != len(revised_quantities):
                raise ValueError("Revise each return line only once.")
            for line_id, value in sorted(revised_quantities):
                quantity = parse_whole_packs(value)
                cur.execute(
                    """SELECT l.*,a.quantity fulfilled_quantity FROM product_return_lines l JOIN fulfillment_allocations a USING(allocation_id)
                    WHERE return_id=%s AND return_line_id=%s FOR UPDATE OF l,a;""",
                    (return_id, line_id),
                )
                line = cur.fetchone()
                if not line or quantity < line["received_quantity"]:
                    raise ValueError("Revised quantities cannot be below goods already received.")
                cur.execute(
                    """SELECT COALESCE(SUM(l.quantity),0) total FROM product_return_lines l JOIN product_returns r USING(return_id)
                    WHERE l.allocation_id=%s AND r.return_id<>%s AND r.status<>'rejected';""",
                    (line["allocation_id"], return_id),
                )
                if quantity + cur.fetchone()["total"] > line["fulfilled_quantity"]:
                    raise ValueError("Revised quantity exceeds the remaining fulfilled quantity.")
                cur.execute("UPDATE product_return_lines SET quantity=%s WHERE return_line_id=%s;", (quantity, line_id))
            cur.execute(
                "SELECT SUM(quantity*unit_price) credit,bool_and(quantity=received_quantity) received FROM product_return_lines WHERE return_id=%s;",
                (return_id,),
            )
            revised = cur.fetchone()
            cur.execute("UPDATE product_returns SET credit=%s WHERE return_id=%s;", (revised["credit"], return_id))
            case["credit"] = revised["credit"]
        cur.execute("DELETE FROM replacement_stock_reservations WHERE return_id=%s;", (return_id,))
        if case["resolution"] == "replace":
            cur.execute(
                """SELECT oi.product_id,SUM(l.quantity) quantity,MIN(l.unit_price) price,MAX(l.unit_price) max_price
                FROM product_return_lines l JOIN fulfillment_allocations a USING(allocation_id)
                JOIN order_items oi USING(order_item_id) WHERE return_id=%s GROUP BY oi.product_id;""",
                (return_id,),
            )
            originals = {row["product_id"]: row for row in cur.fetchall()}
            chosen = replacements or [(p, row["quantity"]) for p, row in originals.items()]
            if len({int(p) for p, _ in chosen}) != len(chosen):
                raise ValueError("Select each replacement product only once.")
            revision = case["quote_revision"] + 1
            total = Decimal(0)
            for product_id, value in sorted(chosen):
                quantity = parse_whole_packs(value)
                cur.execute(
                    "SELECT base_price FROM inventory_items WHERE item_id=%s AND item_type='finished_product' AND is_active=true;",
                    (product_id,),
                )
                product = cur.fetchone()
                if not product:
                    raise ValueError("Select an active replacement product.")
                original = originals.get(int(product_id))
                # Preserve the full original credit for an identical replacement.
                if original and quantity == original["quantity"]:
                    cur.execute(
                        """SELECT SUM(l.quantity*l.unit_price) value FROM product_return_lines l
                        JOIN fulfillment_allocations a USING(allocation_id) JOIN order_items oi USING(order_item_id)
                        WHERE l.return_id=%s AND oi.product_id=%s;""",
                        (return_id, product_id),
                    )
                    line_total = cur.fetchone()["value"]
                    price = line_total / quantity
                else:
                    price = product["base_price"]
                    line_total = quantity * price
                price = price.quantize(Decimal("0.01"))
                total += line_total
                cur.execute(
                    "INSERT INTO replacement_quote_lines(return_id,revision,product_id,quantity,unit_price,line_total) VALUES(%s,%s,%s,%s,%s,%s);",
                    (return_id, revision, product_id, quantity, price, line_total),
                )
            cur.execute(
                "UPDATE product_returns SET quote_revision=%s,accepted_revision=NULL,replacement_total=%s WHERE return_id=%s;",
                (revision, total, return_id),
            )
        elif case["status"] == "settlement_pending":
            raise ValueError("The refund is already awaiting settlement.")
        cur.execute(
            """UPDATE product_returns SET approved_by_account_id=%s,status=CASE WHEN verified_at IS NOT NULL THEN 'settlement_pending'
            WHEN NOT EXISTS(SELECT 1 FROM product_return_lines l WHERE l.return_id=product_returns.return_id AND l.received_quantity<l.quantity)
            THEN 'awaiting_inventory_verification' ELSE 'awaiting_receipt' END WHERE return_id=%s;""",
            (actor_id, return_id),
        )
        cur.execute("SELECT * FROM product_returns WHERE return_id=%s;", (return_id,))
        updated = dict(cur.fetchone())
        if updated["verified_at"] and updated["resolution"] == "refund":
            _refund(cur, updated)
            _complete_if_ready(cur, return_id)
        _event(cur, case, actor_id, "sales_approved", remarks, operation_key,request_hash=request_hash)


def _holding(
    cur,
    *,
    product_id: int,
    batch_code: str,
    expiry_date: date,
    quantity: Decimal,
    location: str,
    actor_id: int,
    source: str,
    batch_id: int | None = None,
    return_line_id: int | None = None,
) -> int:
    cur.execute(
        """INSERT INTO expired_holdings(product_id,batch_id,return_line_id,source,source_batch_code,expiry_date,
        quantity_received,quantity_remaining,location,created_by_account_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING holding_id;""",
        (product_id, batch_id, return_line_id, source, batch_code, expiry_date, quantity, quantity, location, actor_id),
    )
    return int(cur.fetchone()["holding_id"])


def _custody(
    cur,
    holding_id: int,
    action: str,
    quantity: Decimal,
    actor_id: int,
    remarks: str,
    operation_key: str,
    method: str | None = None,
) -> None:
    cur.execute(
        """INSERT INTO expired_stock_movements(holding_id,action,quantity,actor_account_id,remarks,operation_key,disposal_method)
        VALUES(%s,%s,%s,%s,%s,%s,%s);""",
        (holding_id, action, quantity, actor_id, remarks, str(UUID(operation_key)), method),
    )


def _refund(cur, case: dict) -> None:
    amount = (
        case["credit"]
        if case["resolution"] == "refund"
        else max(Decimal(0), case["credit"] - case["replacement_total"])
    )
    if amount <= 0:
        return
    cur.execute("SELECT payment_amount FROM orders WHERE order_id=%s FOR UPDATE;", (case["order_id"],))
    collected = cur.fetchone()["payment_amount"] or Decimal(0)
    cur.execute("SELECT COALESCE(SUM(amount),0) total FROM order_refunds WHERE order_id=%s AND return_id IS DISTINCT FROM %s;", (case["order_id"],case["return_id"]))
    if cur.fetchone()["total"] + amount > collected:
        raise ValueError("Refund obligations exceed the confirmed original payment. Review the sales record.")
    cur.execute(
        "SELECT account_id FROM accounts WHERE account_type='team_leader' AND team_leader_role='sales-supervisor' AND is_active=true ORDER BY account_id LIMIT 1;"
    )
    supervisor = cur.fetchone()
    supervisor_id = supervisor["account_id"] if supervisor else None
    cur.execute(
        """INSERT INTO order_refunds(order_id,return_id,amount,requested_by_account_id,assigned_supervisor_account_id)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(return_id) WHERE return_id IS NOT NULL DO NOTHING RETURNING order_refund_id;""",
        (case["order_id"], case["return_id"], amount, case["requested_by_account_id"], supervisor_id),
    )
    refund = cur.fetchone()
    if refund:
        _create_notification_cursor(
            cur,
            recipient_account_id=supervisor_id,
            recipient_role=None if supervisor_id else "owner",
            category="order",
            title="Return refund pending",
            message=f"Return #{case['return_id']} requires a PHP {amount:,.2f} refund.",
            target_url=f"/portal/{'sales-supervisor' if supervisor_id else 'owner'}/refunds?refund={refund['order_refund_id']}",
            dedupe_key=f"return-{case['return_id']}-refund",
        )


def _complete_if_ready(cur, return_id: int) -> None:
    cur.execute("SELECT * FROM product_returns WHERE return_id=%s FOR UPDATE;", (return_id,))
    case = cur.fetchone()
    if not case or not case["verified_at"] or case["status"] != "settlement_pending":
        return
    if case["resolution"] == "replace" and not case["replacement_released_at"]:
        return
    cur.execute("SELECT 1 FROM order_refunds WHERE return_id=%s AND status<>'completed';", (return_id,))
    if not cur.fetchone():
        cur.execute(
            "UPDATE product_returns SET status='completed',completed_at=now(),updated_at=now() WHERE return_id=%s;",
            (return_id,),
        )
        cur.execute(
            "SELECT r.*,o.created_by_account_id reseller_account_id,o.team_leader_account_id FROM product_returns r JOIN orders o USING(order_id) WHERE return_id=%s;",
            (return_id,),
        )
        _event(cur, dict(cur.fetchone()), None, "completed", "All returned goods and settlement actions are complete.")


def receive_product_return(
    return_id: int, actor_id: int, receipts: list[tuple[int, object]], location: str, remarks: str, operation_key: str
) -> None:
    remarks, location = _text(remarks), _text(location, "Holding location", 2, 200)
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"inventory"})
        operation_key = str(UUID(operation_key))
        request_hash = fingerprint(return_id, actor_id, sorted((int(i), str(parse_whole_packs(q))) for i,q in receipts), location, remarks)
        cur.execute("SELECT * FROM product_return_receipts WHERE operation_key=%s;", (operation_key,))
        recorded = cur.fetchone()
        if recorded:
            if recorded["return_id"] != return_id or recorded["request_hash"] != request_hash:
                raise ValueError("This receipt reference was used with different details.")
            return
        if case["status"] != "awaiting_receipt":
            raise ValueError("Sales approval is required before receiving these items.")
        if not receipts or len({int(i) for i, _ in receipts}) != len(receipts):
            raise ValueError("Select distinct approved return lines and quantities.")
        cur.execute("INSERT INTO product_return_receipts(return_id,operation_key,request_hash,received_by_account_id,location,remarks) VALUES(%s,%s,%s,%s,%s,%s) RETURNING receipt_id;", (return_id,operation_key,request_hash,actor_id,location,remarks))
        receipt_id = cur.fetchone()["receipt_id"]
        from uuid import uuid5, NAMESPACE_URL

        for index, (line_id, value) in enumerate(receipts):
            quantity = parse_whole_packs(value)
            cur.execute(
                """SELECT l.*,a.batch_id,b.item_id,b.batch_code,b.expiry_date FROM product_return_lines l
                JOIN fulfillment_allocations a USING(allocation_id) JOIN inventory_batches b USING(batch_id)
                WHERE l.return_id=%s AND l.return_line_id=%s FOR UPDATE OF l;""",
                (return_id, line_id),
            )
            line = cur.fetchone()
            if (
                not line
                or quantity + line["received_quantity"] > line["quantity"]
                or line["expiry_date"] > business_today()
            ):
                raise ValueError(
                    "Receipt must match an approved expired batch and outstanding quantity. Flag discrepancies separately."
                )
            holding_id = _holding(
                cur,
                product_id=line["item_id"],
                batch_code=line["batch_code"],
                expiry_date=line["expiry_date"],
                quantity=quantity,
                location=location,
                actor_id=actor_id,
                source="return",
                batch_id=line["batch_id"],
                return_line_id=line_id,
            )
            key = operation_key if index == 0 else str(uuid5(NAMESPACE_URL, f"{operation_key}-{line_id}"))
            _custody(cur, holding_id, "return_receipt", quantity, actor_id, remarks, key)
            cur.execute("INSERT INTO product_return_receipt_lines(receipt_id,return_line_id,holding_id,movement_id,quantity) SELECT %s,%s,%s,movement_id,%s FROM expired_stock_movements WHERE operation_key=%s;", (receipt_id,line_id,holding_id,quantity,key))
            cur.execute(
                "UPDATE product_return_lines SET received_quantity=received_quantity+%s WHERE return_line_id=%s;",
                (quantity, line_id),
            )
        cur.execute(
            "SELECT 1 FROM product_return_lines WHERE return_id=%s AND received_quantity<quantity;", (return_id,)
        )
        if not cur.fetchone():
            cur.execute("UPDATE product_returns SET status='awaiting_inventory_verification' WHERE return_id=%s;", (return_id,))
        _event(cur, case, actor_id, "inventory_received", f"Receipt #{receipt_id}: {remarks}")


def verify_product_return(return_id: int, actor_id: int, remarks: str, operation_key: str) -> None:
    remarks = _text(remarks)
    operation_key = str(UUID(operation_key))
    request_hash = fingerprint(return_id, actor_id, remarks)
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"inventory"})
        cur.execute("SELECT * FROM product_return_events WHERE operation_key=%s;", (operation_key,))
        event = cur.fetchone()
        if event:
            if event["return_id"] != return_id or event["action"] != "inventory_verified" or event["request_hash"] != request_hash:
                raise ValueError("This verification reference was used with different details.")
            return
        if case["verified_at"] or case["status"] != "awaiting_inventory_verification" or not case["approved_by_account_id"]:
            raise ValueError("Receive all Sales-approved goods and resolve discrepancies before final Inventory verification.")
        problems = receipt_problems(cur, return_id)
        if problems:
            raise ValueError(problems[0])
        cur.execute("SELECT SUM(quantity*unit_price) credit FROM product_return_lines WHERE return_id=%s;", (return_id,))
        if cur.fetchone()["credit"] != case["credit"]:
            raise ValueError("Return credit does not match the original purchase prices.")
        cur.execute("UPDATE product_returns SET verified_at=now(),verified_by_account_id=%s,verification_remarks=%s,status='settlement_pending' WHERE return_id=%s RETURNING *;", (actor_id,remarks,return_id))
        verified = dict(cur.fetchone())
        _event(cur, case, actor_id, "inventory_verified", remarks, operation_key, request_hash=request_hash)
        if case["resolution"] == "refund":
            _refund(cur, verified)
        _complete_if_ready(cur, return_id)


def flag_return_discrepancy(return_id: int, actor_id: int, remarks: str) -> None:
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"inventory"})
        if case["status"] not in {"awaiting_receipt", "awaiting_inventory_verification"} or case["verified_at"]:
            raise ValueError("Only a return awaiting receipt can have a receipt discrepancy.")
        cur.execute("UPDATE product_returns SET status='discrepancy' WHERE return_id=%s;", (return_id,))
        _event(cur, case, actor_id, "receipt_discrepancy", _text(remarks))


def accept_replacement_quote(return_id: int, actor_id: int, revision: int) -> None:
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"reseller"})
        if (
            case["resolution"] != "replace"
            or case["status"] not in {"awaiting_receipt", "awaiting_inventory_verification", "settlement_pending"}
            or revision != case["quote_revision"]
        ):
            raise ValueError("This quote has changed or is unavailable. Review the current quote.")
        if case["accepted_revision"] == revision:
            return
        cur.execute("UPDATE product_returns SET accepted_revision=%s WHERE return_id=%s;", (revision, return_id))
        _event(cur, case, actor_id, "quote_accepted", f"Reseller accepted replacement quote revision {revision}.")


def reserve_replacement(return_id: int, actor_id: int, remarks: str) -> None:
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"inventory"})
        if (
            case["status"] != "settlement_pending"
            or not case["verified_at"]
            or case["resolution"] != "replace"
            or case["accepted_revision"] != case["quote_revision"]
            or case["replacement_released_at"]
        ):
            raise ValueError("Verify the returned goods and obtain quote acceptance first.")
        problems = receipt_problems(cur, return_id)
        if problems:
            raise ValueError(problems[0])
        cur.execute(
            "SELECT product_id,quantity FROM replacement_quote_lines WHERE return_id=%s AND revision=%s;",
            (return_id, case["quote_revision"]),
        )
        quantities = {r["product_id"]: r["quantity"] for r in cur.fetchall()}
        plans = _plan_fefo_allocations(cur, quantities, reservation_return_id=return_id)
        cur.execute("DELETE FROM replacement_stock_reservations WHERE return_id=%s;", (return_id,))
        for plan in plans:
            cur.execute(
                "INSERT INTO replacement_stock_reservations(return_id,batch_id,quantity) VALUES(%s,%s,%s);",
                (return_id, plan["batch_id"], plan["take"]),
            )
        _event(cur, case, actor_id, "replacement_reserved", _text(remarks))


def record_return_payment(return_id: int, actor_id: int, attachments: list[dict]) -> None:
    if not attachments:
        raise ValueError("Attach a payment screenshot.")
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"reseller"})
        if (
            case["resolution"] != "replace"
            or case["status"] != "settlement_pending"
            or case["accepted_revision"] != case["quote_revision"]
            or case["replacement_total"] <= case["credit"]
            or case["payment_verified_at"]
        ):
            raise ValueError("This return is not awaiting an additional payment.")
        if not _valid_reservation(cur, case):
            raise ValueError("Inventory must reserve valid replacement stock before payment.")
        _attachments(cur, return_id, actor_id, attachments, "payment", case["quote_revision"])
        _event(cur, case, actor_id, "payment_uploaded", "Additional-payment evidence uploaded for sales verification.")


def _valid_reservation(cur, case: dict) -> bool:
    cur.execute(
        """SELECT q.product_id,q.quantity,COALESCE(SUM(r.quantity) FILTER(WHERE b.quality_status='approved'
        AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date AND b.quantity_available>=r.quantity),0) reserved
        FROM replacement_quote_lines q LEFT JOIN inventory_batches b ON b.item_id=q.product_id
        LEFT JOIN replacement_stock_reservations r ON r.batch_id=b.batch_id AND r.return_id=q.return_id
        WHERE q.return_id=%s AND q.revision=%s GROUP BY q.product_id,q.quantity;""",
        (case["return_id"], case["quote_revision"]),
    )
    rows = cur.fetchall()
    return bool(rows) and all(r["quantity"] == r["reserved"] for r in rows)


def verify_return_payment(return_id: int, actor_id: int, reference: str, amount: object, remarks: str) -> None:
    from .refunds import _normalize_reference

    _, reference_key = _normalize_reference(reference)
    try:
        amount = Decimal(str(amount))
    except Exception as exc:
        raise ValueError("Enter the exact additional payment amount.") from exc
    if not amount.is_finite():
        raise ValueError("Enter a valid payment amount.")
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"sales", "sales-supervisor"})
        if case["payment_verified_at"]:
            raise ValueError("Additional payment is already verified.")
        if (
            case["resolution"] != "replace"
            or case["status"] != "settlement_pending"
            or case["accepted_revision"] != case["quote_revision"]
            or amount <= 0
            or amount != case["replacement_total"] - case["credit"]
        ):
            raise ValueError("Payment must equal the accepted quote's additional amount.")
        cur.execute(
            "SELECT 1 FROM return_attachments WHERE return_id=%s AND purpose='payment' AND revision=%s;",
            (return_id, case["quote_revision"]),
        )
        if not cur.fetchone() or not _valid_reservation(cur, case):
            raise ValueError("Current payment proof and valid reserved stock are required.")
        cur.execute(
            "SELECT 1 FROM orders WHERE payment_transaction_key=%s UNION ALL SELECT 1 FROM product_returns WHERE payment_reference=%s LIMIT 1;",
            (reference_key, reference_key),
        )
        if cur.fetchone():
            raise ValueError("This payment reference has already been used.")
        cur.execute(
            "UPDATE product_returns SET payment_verified_by_account_id=%s,payment_verified_at=now(),payment_reference=%s,payment_amount=%s WHERE return_id=%s;",
            (actor_id, reference_key, amount, return_id),
        )
        _event(cur, case, actor_id, "payment_verified", _text(remarks))


def release_replacement(return_id: int, actor_id: int, remarks: str) -> None:
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"inventory"})
        if case["replacement_released_at"]:
            return
        if (
            case["status"] != "settlement_pending"
            or not case["verified_at"]
            or case["resolution"] != "replace"
            or case["accepted_revision"] != case["quote_revision"]
        ):
            raise ValueError("A verified return and accepted quote are required.")
        problems = receipt_problems(cur, return_id)
        if problems:
            raise ValueError(problems[0])
        if case["replacement_total"] > case["credit"] and not case["payment_verified_at"]:
            raise ValueError("Sales must verify the additional payment before release.")
        cur.execute(
            "SELECT DISTINCT b.item_id FROM replacement_stock_reservations r JOIN inventory_batches b USING(batch_id) WHERE return_id=%s ORDER BY b.item_id;",
            (return_id,),
        )
        for row in cur.fetchall():
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0));", (f"inventory-product:{row['item_id']}",)
            )
        cur.execute(
            "SELECT b.* FROM replacement_stock_reservations r JOIN inventory_batches b USING(batch_id) WHERE return_id=%s ORDER BY b.item_id,b.expiry_date,b.batch_id FOR UPDATE OF b;",
            (return_id,),
        )
        cur.fetchall()
        if not _valid_reservation(cur, case):
            raise ValueError("Replacement reservation is missing or expired. Reserve valid stock again.")
        cur.execute(
            """SELECT r.quantity,b.*,i.unit FROM replacement_stock_reservations r JOIN inventory_batches b USING(batch_id)
            JOIN inventory_items i ON i.item_id=b.item_id WHERE return_id=%s ORDER BY b.item_id,b.expiry_date,b.batch_id;""",
            (return_id,),
        )
        for row in cur.fetchall():
            before, quantity = row["quantity_available"], row["quantity"]
            cur.execute(
                "UPDATE inventory_batches SET quantity_available=quantity_available-%s WHERE batch_id=%s;",
                (quantity, row["batch_id"]),
            )
            cur.execute(
                "INSERT INTO replacement_releases(return_id,batch_id,quantity,released_by_account_id) VALUES(%s,%s,%s,%s);",
                (return_id, row["batch_id"], quantity, actor_id),
            )
            _insert_inventory_movement(
                cur,
                item_id=row["item_id"],
                affected_batch_id=row["batch_id"],
                order_id=case["order_id"],
                movement_type="replacement_release",
                quantity_delta=-quantity,
                unit=row["unit"],
                balance_before=before,
                balance_after=before - quantity,
                actor_account_id=actor_id,
                actor_name=_inventory_actor_name(cur, actor_id),
                note=f"Replacement for return #{return_id}: {_text(remarks)}",
            )
        cur.execute("DELETE FROM replacement_stock_reservations WHERE return_id=%s;", (return_id,))
        cur.execute("UPDATE product_returns SET replacement_released_at=now() WHERE return_id=%s;", (return_id,))
        _refund(cur, case)
        _complete_if_ready(cur, return_id)
        _event(cur, case, actor_id, "replacement_released", _text(remarks))


def confirm_expired_pullout(
    batch_id: int, actor_id: int, quantity: object, location: str, remarks: str, operation_key: str
) -> None:
    quantity = parse_whole_packs(quantity)
    with get_transaction_cursor() as cur:
        _actor(cur, actor_id, {"inventory"})
        cur.execute("SELECT 1 FROM expired_stock_movements WHERE operation_key=%s;", (str(UUID(operation_key)),))
        if cur.fetchone():
            return
        cur.execute("SELECT item_id FROM inventory_batches WHERE batch_id=%s;", (batch_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Batch was not found.")
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0));", (f"inventory-product:{row['item_id']}",))
        cur.execute("SELECT * FROM inventory_batches WHERE batch_id=%s FOR UPDATE;", (batch_id,))
        batch = cur.fetchone()
        # A competing replay can commit while this request waits for the stock
        # lock. Recheck before validating the now-reduced warehouse balance.
        cur.execute("SELECT 1 FROM expired_stock_movements WHERE operation_key=%s;", (str(UUID(operation_key)),))
        if cur.fetchone():
            return
        if batch["expiry_date"] > business_today() or quantity > batch["quantity_available"]:
            raise ValueError("Pull-out must reference expired stock and cannot exceed the warehouse balance.")
        cur.execute(
            "UPDATE inventory_batches SET quality_status='expired',quantity_available=quantity_available-%s WHERE batch_id=%s;",
            (quantity, batch_id),
        )
        holding_id = _holding(
            cur,
            product_id=batch["item_id"],
            batch_id=batch_id,
            batch_code=batch["batch_code"],
            expiry_date=batch["expiry_date"],
            quantity=quantity,
            location=_text(location, "Holding location", 2, 200),
            actor_id=actor_id,
            source="pullout",
        )
        _custody(cur, holding_id, "pullout", quantity, actor_id, _text(remarks), operation_key)
        _insert_inventory_movement(
            cur,
            item_id=batch["item_id"],
            affected_batch_id=batch_id,
            movement_type="expired_pullout",
            quantity_delta=-quantity,
            unit=batch["unit"],
            balance_before=batch["quantity_available"],
            balance_after=batch["quantity_available"] - quantity,
            actor_account_id=actor_id,
            actor_name=_inventory_actor_name(cur, actor_id),
            note=_text(remarks),
        )


def record_expired_arrival(
    product_id: int,
    actor_id: int,
    batch_code: str,
    expiry_date: date,
    quantity: object,
    location: str,
    remarks: str,
    operation_key: str,
) -> None:
    quantity = parse_whole_packs(quantity)
    if expiry_date > business_today():
        raise ValueError("Use normal batch receipt for goods with a future expiration date.")
    with get_transaction_cursor() as cur:
        _actor(cur, actor_id, {"inventory"})
        cur.execute("SELECT 1 FROM expired_stock_movements WHERE operation_key=%s;", (str(UUID(operation_key)),))
        if cur.fetchone():
            return
        cur.execute("SELECT 1 FROM inventory_items WHERE item_id=%s AND item_type='finished_product';", (product_id,))
        if not cur.fetchone():
            raise ValueError("Select a finished product.")
        holding_id = _holding(
            cur,
            product_id=product_id,
            batch_code=_text(batch_code, "Batch code", 4, 100).upper(),
            expiry_date=expiry_date,
            quantity=quantity,
            location=_text(location, "Holding location", 2, 200),
            actor_id=actor_id,
            source="expired_receipt",
        )
        _custody(cur, holding_id, "expired_receipt", quantity, actor_id, _text(remarks), operation_key)


def dispose_expired_stock(
    holding_id: int, actor_id: int, quantity: object, method: str, remarks: str, operation_key: str
) -> None:
    quantity = parse_whole_packs(quantity)
    with get_transaction_cursor() as cur:
        _actor(cur, actor_id, {"inventory"})
        cur.execute("SELECT * FROM expired_holdings WHERE holding_id=%s FOR UPDATE;", (holding_id,))
        holding = cur.fetchone()
        cur.execute("SELECT 1 FROM expired_stock_movements WHERE operation_key=%s;", (str(UUID(operation_key)),))
        if cur.fetchone():
            return
        if not holding or quantity > holding["quantity_remaining"]:
            raise ValueError("Disposal quantity exceeds the holding balance.")
        cur.execute(
            "UPDATE expired_holdings SET quantity_remaining=quantity_remaining-%s WHERE holding_id=%s;",
            (quantity, holding_id),
        )
        _custody(
            cur,
            holding_id,
            "disposal",
            -quantity,
            actor_id,
            _text(remarks),
            operation_key,
            _text(method, "Disposal method", 3, 200),
        )


def sync_expired_custody(cur) -> None:
    cur.execute("""INSERT INTO expired_pullouts(batch_id) SELECT batch_id FROM inventory_batches
        WHERE expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date AND quantity_available>0 ON CONFLICT DO NOTHING;""")
    # Release invalid replacement reservations; the case stays pending for fresh stock.
    cur.execute("""DELETE FROM replacement_stock_reservations r USING inventory_batches b
        WHERE r.batch_id=b.batch_id AND (b.quality_status<>'approved' OR b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date)
        RETURNING r.return_id;""")
    for return_id in {r["return_id"] for r in cur.fetchall()}:
        cur.execute(
            "SELECT r.*,o.created_by_account_id reseller_account_id,o.team_leader_account_id FROM product_returns r JOIN orders o USING(order_id) WHERE return_id=%s;",
            (return_id,),
        )
        case = dict(cur.fetchone())
        _event(
            cur,
            case,
            None,
            "awaiting_valid_stock",
            "Reserved replacement stock expired. Inventory must reserve valid stock before release.",
        )
    cur.execute("""SELECT o.order_id,o.team_leader_account_id FROM orders o WHERE o.status IN ('to_pay','pending','approved')
        AND EXISTS(SELECT 1 FROM order_items oi WHERE oi.order_id=o.order_id AND oi.quantity>(
            SELECT COALESCE(SUM(b.quantity_available),0) FROM inventory_batches b WHERE b.item_id=oi.product_id
            AND b.quality_status='approved' AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date
        )-COALESCE((SELECT SUM(s.quantity) FROM all_stock_reservations s
            WHERE s.product_id=oi.product_id AND s.order_id IS DISTINCT FROM o.order_id),0));""")
    for row in cur.fetchall():
        if row["team_leader_account_id"]:
            _create_notification_cursor(
                cur,
                recipient_account_id=row["team_leader_account_id"],
                category="order",
                severity="critical",
                title="Order needs stock review",
                message=f"Order #{row['order_id']} has insufficient valid stock. Review before fulfillment.",
                target_url="/portal/team-leader/orders",
                dedupe_key=f"order-stock-review-{row['order_id']}-{business_today()}",
            )


def list_product_returns(actor_id: int, status: str = "", *, page: int = 1, reconciliation_only: bool = False) -> list[dict]:
    with get_transaction_cursor() as cur:
        role = _actor(cur, actor_id, {"reseller", "sales", "sales-supervisor", "inventory"})
        scope, params = "", []
        if role == "reseller":
            scope, params = " AND o.created_by_account_id=%s", [actor_id]
        elif role == "sales-supervisor":
            scope, params = (
                " AND (o.team_leader_account_id=%s OR EXISTS(SELECT 1 FROM order_refunds f WHERE f.return_id=r.return_id AND f.assigned_supervisor_account_id=%s))",
                [actor_id, actor_id],
            )
        elif role == "sales":
            scope, params = " AND o.team_leader_account_id=%s", [actor_id]
        if status:
            scope += " AND r.status=%s"
            params.append(status)
        if reconciliation_only:
            scope += " AND cardinality(checks.flags)>0"
        cur.execute("SELECT COUNT(*) total FROM product_returns r JOIN orders o USING(order_id) JOIN product_return_reconciliation checks USING(return_id) WHERE true" + scope,tuple(params))
        total = cur.fetchone()["total"]
        pagination = pagination_meta(total,page,12)
        cur.execute(
            """SELECT r.*,a.name reseller_name,o.team_leader_account_id,o.total_amount order_total,
            GREATEST(r.replacement_total-r.credit,0) additional_due,GREATEST(r.credit-r.replacement_total,0) unused_credit,
            EXISTS(SELECT 1 FROM replacement_stock_reservations s WHERE s.return_id=r.return_id) has_reservations,
            (SELECT status FROM order_refunds f WHERE f.return_id=r.return_id) refund_status,
            (SELECT order_refund_id FROM order_refunds f WHERE f.return_id=r.return_id) order_refund_id,checks.flags reconciliation_flags
            FROM product_returns r JOIN orders o USING(order_id) JOIN accounts a ON a.account_id=o.created_by_account_id
            JOIN product_return_reconciliation checks USING(return_id)
            WHERE true"""
            + scope
            + " ORDER BY r.updated_at DESC,r.return_id DESC LIMIT 12 OFFSET %s;",
            tuple([*params,(pagination["page"]-1)*12]),
        )
        rows = clean_row(cur.fetchall())
        for row in rows:
            row["list_total"] = total
            row["list_page"] = pagination["page"]
        return rows


def get_product_return(return_id: int, actor_id: int) -> dict:
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"reseller", "sales", "sales-supervisor", "inventory"}, read_only=True)
        cur.execute(
            """SELECT l.*,a.batch_id,oi.product_id,i.name,b.batch_code,b.expiry_date FROM product_return_lines l
            JOIN fulfillment_allocations a USING(allocation_id) JOIN order_items oi USING(order_item_id)
            JOIN inventory_items i ON i.item_id=oi.product_id JOIN inventory_batches b USING(batch_id) WHERE return_id=%s ORDER BY l.return_line_id;""",
            (return_id,),
        )
        case["lines"] = clean_row(cur.fetchall())
        cur.execute(
            "SELECT e.*,a.name actor_name FROM product_return_events e LEFT JOIN accounts a ON a.account_id=e.actor_account_id WHERE return_id=%s ORDER BY event_id;",
            (return_id,),
        )
        case["events"] = clean_row(cur.fetchall())
        cur.execute(
            "SELECT q.*,i.name FROM replacement_quote_lines q JOIN inventory_items i ON i.item_id=q.product_id WHERE return_id=%s AND revision=%s ORDER BY q.product_id;",
            (return_id, case["quote_revision"]),
        )
        case["quote_lines"] = clean_row(cur.fetchall())
        cur.execute(
            "SELECT attachment_id,purpose,revision,filename FROM return_attachments WHERE return_id=%s ORDER BY attachment_id;",
            (return_id,),
        )
        case["attachments"] = clean_row(cur.fetchall())
        case["has_reservations"] = _valid_reservation(cur, case)
        cur.execute("SELECT order_refund_id,status,amount FROM order_refunds WHERE return_id=%s;", (return_id,))
        case["refund"] = clean_row(cur.fetchone())
        cur.execute("SELECT h.*,a.name received_by_name FROM product_return_receipts h LEFT JOIN accounts a ON a.account_id=h.received_by_account_id WHERE return_id=%s ORDER BY receipt_id;", (return_id,))
        case["receipts"] = clean_row(cur.fetchall())
        cur.execute("""SELECT x.*,h.location,h.quantity_remaining,h.source_batch_code,
            i.name,h.expiry_date,m.created_at,
            -COALESCE((SELECT SUM(d.quantity) FROM expired_stock_movements d WHERE d.holding_id=h.holding_id AND d.action='disposal'),0) disposed_quantity
            FROM product_return_receipt_lines x JOIN product_return_receipts r USING(receipt_id)
            JOIN expired_holdings h USING(holding_id) JOIN inventory_items i ON i.item_id=h.product_id
            JOIN expired_stock_movements m ON m.movement_id=x.movement_id
            WHERE r.return_id=%s ORDER BY x.receipt_line_id;""", (return_id,))
        case["receipt_lines"] = clean_row(cur.fetchall())
        cur.execute("SELECT m.*,a.name actor_name FROM expired_stock_movements m JOIN expired_holdings h USING(holding_id) JOIN product_return_lines l ON l.return_line_id=h.return_line_id LEFT JOIN accounts a ON a.account_id=m.actor_account_id WHERE l.return_id=%s ORDER BY m.movement_id;", (return_id,))
        case["custody_movements"] = clean_row(cur.fetchall())
        cur.execute("SELECT name FROM accounts WHERE account_id=%s;", (case["verified_by_account_id"],))
        verifier = cur.fetchone()
        case["verified_by_name"] = verifier["name"] if verifier else None
        case["reconciliation_flags"] = receipt_problems(cur, return_id) if case["verified_at"] or case["status"] == "awaiting_inventory_verification" else []
        if case["status"] == "completed" and (not case["verified_at"] or (case["resolution"] == "replace" and not case["replacement_released_at"]) or (case["refund"] and case["refund"]["status"] != "completed")):
            case["reconciliation_flags"].append("Case is marked complete before its required steps are complete.")
        expected_refund = case["credit"] if case["resolution"] == "refund" else max(0,case["credit"]-case["replacement_total"])
        if case["refund"] and case["refund"]["amount"] != expected_refund:
            case["reconciliation_flags"].append("Refund amount does not match the verified credit.")
        if case["verified_at"] and expected_refund > 0 and not case["refund"] and (case["resolution"] == "refund" or case["replacement_released_at"]):
            case["reconciliation_flags"].append("The verified settlement is missing its refund record.")
        cur.execute("SELECT flags FROM product_return_reconciliation WHERE return_id=%s;",(return_id,))
        case["reconciliation_flags"] = list(dict.fromkeys(case["reconciliation_flags"] + cur.fetchone()["flags"]))
        return clean_row(case)


def returnable_allocations(order_id: int, actor_id: int) -> list[dict]:
    return clean_row(
        fetch_all(
            """SELECT a.allocation_id,a.quantity,b.batch_code,b.expiry_date,i.name,oi.unit_price,
        a.quantity-COALESCE((SELECT SUM(l.quantity) FROM product_return_lines l JOIN product_returns r USING(return_id)
            WHERE l.allocation_id=a.allocation_id AND r.status<>'rejected'),0) returnable_quantity
        FROM fulfillment_allocations a JOIN order_items oi USING(order_item_id) JOIN orders o USING(order_id)
        JOIN inventory_batches b USING(batch_id) JOIN inventory_items i ON i.item_id=oi.product_id
        WHERE o.order_id=%s AND o.created_by_account_id=%s AND o.status='fulfilled'
        AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date ORDER BY i.name,b.expiry_date;""",
            (order_id, actor_id),
        )
    )


def expired_stock_workspace() -> dict:
    return {
        "pullouts": clean_row(
            fetch_all("""SELECT p.*,b.item_id,b.batch_code,b.quantity_available,b.expiry_date,i.name
            FROM expired_pullouts p JOIN inventory_batches b USING(batch_id) JOIN inventory_items i ON i.item_id=b.item_id
            WHERE b.quantity_available>0 AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date ORDER BY b.expiry_date,b.batch_id;""")
        ),
        "holdings": clean_row(
            fetch_all(
                "SELECT h.*,i.name FROM expired_holdings h JOIN inventory_items i ON i.item_id=h.product_id WHERE quantity_remaining>0 ORDER BY h.created_at;"
            )
        ),
        "disposals": clean_row(
            fetch_all("""SELECT m.*,h.source_batch_code,h.location,h.source,h.return_line_id,i.name,a.name actor_name
            FROM expired_stock_movements m JOIN expired_holdings h USING(holding_id) JOIN inventory_items i ON i.item_id=h.product_id
            JOIN accounts a ON a.account_id=m.actor_account_id WHERE action='disposal' ORDER BY m.created_at DESC LIMIT 100;""")
        ),
        "custody_history": clean_row(
            fetch_all("""SELECT m.*,h.source_batch_code,h.location,i.name,a.name actor_name
            FROM expired_stock_movements m JOIN expired_holdings h USING(holding_id) JOIN inventory_items i ON i.item_id=h.product_id
            JOIN accounts a ON a.account_id=m.actor_account_id ORDER BY m.created_at DESC LIMIT 100;""")
        ),
    }


def product_lifecycle_details(product_id: int, *, batch_page: int = 1, movement_page: int = 1) -> dict | None:
    product = clean_row(
        fetch_one("SELECT * FROM inventory_items WHERE item_id=%s AND item_type='finished_product';", (product_id,))
    )
    if not product:
        return None
    from .superuser import business_setting

    today = business_today()
    product["near_expiry_days"] = int(business_setting("inventory.near_expiry_days"))
    totals = clean_row(
        fetch_one(
            """SELECT COUNT(*) batch_count,
        COALESCE(SUM(quantity_available),0) warehouse,
        COALESCE(SUM(quantity_available) FILTER(WHERE quality_status='approved' AND expiry_date>%s),0) eligible,
        COALESCE(SUM(quantity_available) FILTER(WHERE quality_status='approved' AND expiry_date-%s BETWEEN 1 AND %s),0) near_expiry,
        COALESCE(SUM(quantity_available) FILTER(WHERE expiry_date<=%s),0) expired
        FROM inventory_batches WHERE item_id=%s;""",
            (today, today, product["near_expiry_days"], today, product_id),
        )
    )
    product.update(totals)
    batch_pagination = pagination_meta(totals["batch_count"], batch_page, 3)
    product["batch_pagination"] = batch_pagination
    product["batches"] = clean_row(
        fetch_all(
            """SELECT b.*,b.expiry_date-%s days_left,
        COALESCE((SELECT SUM(r.quantity) FROM replacement_stock_reservations r WHERE r.batch_id=b.batch_id),0) replacement_reserved
        FROM inventory_batches b WHERE item_id=%s ORDER BY expiry_date,batch_id LIMIT %s OFFSET %s;""",
            (
                today,
                product_id,
                batch_pagination["page_size"],
                (batch_pagination["page"] - 1) * batch_pagination["page_size"],
            ),
        )
    )
    product["reserved"] = float(
        fetch_one(
            "SELECT COALESCE(SUM(quantity),0) total FROM all_stock_reservations WHERE product_id=%s;", (product_id,)
        )["total"]
    )
    product["available"] = max(0, product["eligible"] - product["reserved"])
    product["holding"] = float(
        fetch_one(
            "SELECT COALESCE(SUM(quantity_remaining),0) total FROM expired_holdings WHERE product_id=%s;", (product_id,)
        )["total"]
    )
    movement_count = fetch_one("SELECT COUNT(*) total FROM inventory_movements WHERE item_id=%s;", (product_id,))[
        "total"
    ]
    movement_pagination = pagination_meta(movement_count, movement_page, 5)
    product["movement_pagination"] = movement_pagination
    product["movements"] = clean_row(
        fetch_all(
            "SELECT * FROM inventory_movements WHERE item_id=%s ORDER BY movement_id DESC LIMIT %s OFFSET %s;",
            (
                product_id,
                movement_pagination["page_size"],
                (movement_pagination["page"] - 1) * movement_pagination["page_size"],
            ),
        )
    )
    return product


def return_settlement_totals(actor_id: int) -> dict:
    with get_transaction_cursor() as cur:
        role = _actor(cur, actor_id, {"sales", "sales-supervisor", "inventory", "reseller"})
        scope, params = "", ()
        if role in {"sales", "sales-supervisor"}:
            scope, params = " WHERE o.team_leader_account_id=%s", (actor_id,)
        elif role == "reseller":
            scope, params = " WHERE o.created_by_account_id=%s", (actor_id,)
        cur.execute(
            """SELECT COALESCE(SUM(r.credit) FILTER(WHERE r.verified_at IS NOT NULL),0) return_credits,
            COALESCE(SUM(r.replacement_total) FILTER(WHERE r.replacement_released_at IS NOT NULL),0) replacement_value,
            COALESCE(SUM(r.payment_amount) FILTER(WHERE r.payment_verified_at IS NOT NULL),0) additional_collections,
            COALESCE(SUM((SELECT COALESCE(SUM(f.amount),0) FROM order_refunds f WHERE f.return_id=r.return_id AND f.status='completed')),0) completed_refunds
            FROM product_returns r JOIN orders o USING(order_id)"""
            + scope,
            params,
        )
        totals = dict(cur.fetchone())
        cur.execute(
            "SELECT COALESCE(SUM(o.total_amount) FILTER(WHERE o.status='fulfilled'),0) gross FROM orders o" + scope,
            params,
        )
        totals["original_sales"] = cur.fetchone()["gross"]
        cur.execute(
            "SELECT COALESCE(SUM(CASE WHEN o.order_type='walk_in' AND o.status='fulfilled' THEN o.total_amount WHEN o.status IN ('approved','fulfilled') OR (o.status='rejected' AND o.rejection_payment_received=true) THEN o.payment_amount ELSE 0 END),0) collections FROM orders o"
            + scope,
            params,
        )
        totals["original_collections"] = cur.fetchone()["collections"]
        cur.execute(
            "SELECT COALESCE(SUM(f.amount),0) total FROM order_refunds f JOIN orders o USING(order_id)"
            + scope
            + (" AND " if scope else " WHERE ")
            + "f.status='completed';",
            params,
        )
        totals["net_collections"] = (
            totals["original_collections"] + totals["additional_collections"] - cur.fetchone()["total"]
        )
        cur.execute("SELECT COALESCE(SUM(f.amount),0) total FROM order_refunds f JOIN orders o USING(order_id)" + scope + (" AND " if scope else " WHERE ") + "f.status<>'completed';",params)
        totals["pending_refunds"] = cur.fetchone()["total"]
        return clean_row(totals)


def verify_legacy_allocation(
    order_id: int, order_item_id: int, batch_id: int, quantity: object, actor_id: int, remarks: str
) -> None:
    quantity = parse_whole_packs(quantity)
    with get_transaction_cursor() as cur:
        _actor(cur, actor_id, {"inventory"})
        cur.execute(
            "SELECT oi.*,o.status FROM order_items oi JOIN orders o USING(order_id) WHERE oi.order_id=%s AND order_item_id=%s FOR UPDATE OF oi;",
            (order_id, order_item_id),
        )
        line = cur.fetchone()
        cur.execute("SELECT item_id FROM inventory_batches WHERE batch_id=%s;", (batch_id,))
        batch = cur.fetchone()
        if not line or line["status"] != "fulfilled" or not batch or batch["item_id"] != line["product_id"]:
            raise ValueError("Verify the fulfilled order line and matching product batch.")
        cur.execute(
            "SELECT COALESCE(SUM(quantity),0) total FROM fulfillment_allocations WHERE order_item_id=%s;",
            (order_item_id,),
        )
        if quantity + cur.fetchone()["total"] > line["quantity"]:
            raise ValueError("Verified batch quantities exceed the fulfilled order line.")
        cur.execute(
            "INSERT INTO fulfillment_allocations(order_item_id,batch_id,quantity,verified_by_account_id,verification_note) VALUES(%s,%s,%s,%s,%s);",
            (order_item_id, batch_id, quantity, actor_id, _text(remarks)),
        )
