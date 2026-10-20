from __future__ import annotations

from datetime import datetime, timedelta
from typing import TypedDict

import psycopg2

from ._shared import *
from .accounts import reseller_account_profile
from .inventory import _apply_fefo_allocations, _inventory_actor_name, _plan_fefo_allocations
from .notifications import _create_notification_cursor, _create_order_system_message_cursor
from .superuser import business_setting


class ConfirmedPayment(TypedDict):
    transaction_number: str
    transaction_key: str
    paid_at: datetime
    payment_amount: Decimal
    ocr_confidence: float
    extraction_method: str
    confirmed_at: datetime
    ocr_engine: str


RESELLER_ORDER_CANCELLATION_REASONS = (
    ("ordered_by_mistake", "Ordered by mistake"),
    ("change_items", "Need to change items or quantities"),
    ("cannot_pay", "Unable to complete payment"),
    ("no_longer_needed", "Order is no longer needed"),
    ("other", "Other reason"),
)
_CANCELLATION_REASON_LABELS = dict(RESELLER_ORDER_CANCELLATION_REASONS)


def list_orders(
    order_type: str | None = None,
    q: str = "",
    status: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
    limit: int | None = None,
    page: int | None = None,
    page_size: int = 10,
    sort: str = "",
) -> list[dict]:
    if order_type not in {None, "reseller", "walk_in"}:
        raise ValueError("order_type must be reseller, walk_in, or None")
    where = []
    params: list[object] = []
    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)
    q = q.strip()
    status = status.strip()
    if status == "refunded":
        where.append("refund.status = 'completed'")
    elif status:
        where.append("o.status = %s")
        params.append(status)
    if team_leader_account_id is not None:
        if order_type == "walk_in":
            where.append("o.created_by_account_id = %s")
            params.append(team_leader_account_id)
        elif order_type == "reseller":
            where.append("o.team_leader_account_id = %s")
            params.append(team_leader_account_id)
        else:
            where.append("(o.created_by_account_id = %s OR o.team_leader_account_id = %s)")
            params.extend([team_leader_account_id, team_leader_account_id])
    if reseller_account_id is not None:
        where.append("""o.reseller_id = (
            SELECT a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
        )""")
        params.append(reseller_account_id)
    if q:
        where.append("""(
            CAST(o.order_id AS text) ILIKE %s
            OR COALESCE(r.business_name, 'Retail counter') ILIKE %s
            OR EXISTS (
                SELECT 1
                FROM order_items oi_search
                JOIN inventory_items p_search ON p_search.item_id = oi_search.product_id
                WHERE oi_search.order_id = o.order_id
                  AND p_search.name ILIKE %s
            )
        )""")
        search = f"%{q}%"
        params.extend([search, search, search])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    order_sql = safe_sort_sql(sort, ORDER_SORTS, ORDER_SORTS["newest"])
    orders = clean_row(fetch_all(f"""
        SELECT o.order_id, o.order_type, o.reseller_id,
               COALESCE(r.business_name, 'Retail counter') AS reseller,
               o.team_leader_account_id,
               tl.name AS team_leader_name,
               o.status, o.order_date, o.total_amount, o.notes,
               o.cancellation_reason, o.cancelled_at,
               o.payment_transaction_number, o.payment_paid_at, o.payment_amount,
               o.payment_ocr_confidence, o.payment_extraction_method,
               o.payment_confirmed_at, o.payment_ocr_engine,
               o.payment_due_at, o.expired_at,
               refund.status AS refund_status
        FROM orders o
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = o.team_leader_account_id
        LEFT JOIN order_refunds refund ON refund.order_id = o.order_id
        {where_sql}
        ORDER BY {order_sql}
        {"LIMIT %s" if limit is not None else ""}
        {"LIMIT %s OFFSET %s" if limit is None and page is not None else ""};
    """, tuple(params + ([limit] if limit is not None else ([page_size, (page - 1) * page_size] if page is not None else []))) or None))
    if not orders:
        return orders

    order_ids = [order["order_id"] for order in orders]
    items = clean_row(fetch_all("""
        SELECT oi.order_id, oi.product_id, p.name, oi.quantity, oi.unit_price, oi.line_total
        FROM order_items oi
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE p.item_type = 'finished_product'
          AND oi.order_id = ANY(%s);
    """, (order_ids,)))
    items_by_order: dict[int, list[dict]] = {}
    for item in items:
        order_id = item.pop("order_id")
        items_by_order.setdefault(order_id, []).append(item)
    for order in orders:
        order["items"] = items_by_order.get(order["order_id"], [])

    proofs = clean_row(fetch_all("""
        SELECT order_payment_proof_id,
               order_id,
               filename,
               content_type,
               size_bytes,
               checksum_sha256,
               uploaded_at
        FROM order_payment_proofs
        WHERE order_id = ANY(%s)
        ORDER BY uploaded_at DESC, order_payment_proof_id DESC;
    """, (order_ids,)))
    proofs_by_order: dict[int, list[dict]] = {}
    for proof in proofs:
        order_id = proof.pop("order_id")
        proofs_by_order.setdefault(order_id, []).append(proof)
    for order in orders:
        order["payment_proofs"] = proofs_by_order.get(order["order_id"], [])
        order["payment_proof_count"] = len(order["payment_proofs"])
    return orders


def _insert_order_payment_proofs_cursor(cur, account_id: int, order_id: int, attachments: list[dict]) -> int:
    inserted = 0
    for attachment in attachments:
        cur.execute(
            """
            SELECT 1
            FROM order_payment_proofs existing_proof
            JOIN orders existing_order ON existing_order.order_id = existing_proof.order_id
            JOIN orders target_order ON target_order.order_id = %s
            WHERE existing_order.reseller_id = target_order.reseller_id
              AND existing_proof.checksum_sha256 = %s
            LIMIT 1;
            """,
            (order_id, attachment["checksum_sha256"]),
        )
        if cur.fetchone():
            raise ValueError("This payment screenshot has already been used for another order.")
        cur.execute(
            """
            INSERT INTO order_payment_proofs (
                order_id, uploaded_by_account_id, filename, content_type,
                content, size_bytes, checksum_sha256
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                order_id,
                account_id,
                attachment["filename"],
                attachment["content_type"],
                attachment["content"],
                attachment["size_bytes"],
                attachment["checksum_sha256"],
            ),
        )
        inserted += 1
    return inserted


def add_order_payment_proofs(account_id: int, order_id: int, attachments: list[dict]) -> int:
    if not attachments:
        raise ValueError("Attach at least one proof of payment screenshot.")
    try:
        with get_transaction_cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, o.status, o.team_leader_account_id, r.business_name
                FROM orders o
                JOIN resellers r ON r.reseller_id = o.reseller_id
                JOIN accounts a ON a.reseller_id = r.reseller_id
                WHERE o.order_id = %s AND o.order_type = 'reseller' AND a.account_id = %s
                FOR UPDATE OF o;
                """,
                (order_id, account_id),
            )
            order = cur.fetchone()
            if not order:
                raise ValueError("Order was not found.")
            if order["status"] != "pending":
                raise ValueError("Proof of payment can only be uploaded while the order is pending.")
            cur.execute("SELECT count(*) AS total FROM order_payment_proofs WHERE order_id = %s;", (order_id,))
            existing = int(cur.fetchone()["total"])
            if existing + len(attachments) > 3:
                raise ValueError("An order can have at most three payment proof screenshots.")
            inserted = _insert_order_payment_proofs_cursor(cur, account_id, order_id, attachments)
            _add_log_cursor(cur, actor_account_id=account_id, action="uploaded_payment_proof", entity_type="orders", entity_id=order_id)
            _create_notification_cursor(
                cur, recipient_account_id=order["team_leader_account_id"], category="order", severity="info",
                title="Payment proof uploaded",
                message=f"{order['business_name']} uploaded payment proof for order #{order_id}.",
                target_url="/portal/team-leader/orders", source_type="orders", source_id=order_id,
                dedupe_key=f"order-proof-{order_id}-{existing + inserted}",
            )
    except psycopg2.Error as exc:
        if "already been used" in str(exc):
            raise ValueError("This payment screenshot has already been used for another order.")
        raise
    return inserted


def _expire_unpaid_orders_cursor(
    cur,
    *,
    account_id: int | None = None,
    reseller_id: int | None = None,
    limit: int = 100,
) -> int:
    where = ["o.status = 'to_pay'", "o.payment_due_at <= now()"]
    params: list[object] = []
    if account_id is not None:
        where.append("o.created_by_account_id = %s")
        params.append(account_id)
    if reseller_id is not None:
        where.append("o.reseller_id = %s")
        params.append(reseller_id)
    params.append(limit)
    cur.execute(
        f"""
        SELECT o.order_id, o.created_by_account_id
        FROM orders o
        WHERE {' AND '.join(where)}
        ORDER BY o.payment_due_at, o.order_id
        FOR UPDATE OF o SKIP LOCKED
        LIMIT %s;
        """,
        tuple(params),
    )
    due_orders = cur.fetchall()
    for order in due_orders:
        order_id = int(order["order_id"])
        cur.execute("DELETE FROM order_stock_reservations WHERE order_id = %s;", (order_id,))
        cur.execute(
            """
            UPDATE orders
            SET status = 'expired', expired_at = now()
            WHERE order_id = %s AND status = 'to_pay';
            """,
            (order_id,),
        )
        if cur.rowcount != 1:
            continue
        _add_log_cursor(
            cur, actor_account_id=None, action="expired_unpaid_order",
            entity_type="orders", entity_id=order_id,
        )
        _create_notification_cursor(
            cur, recipient_account_id=order["created_by_account_id"], category="order", severity="warning",
            title="Payment window expired",
            message=f"Order #{order_id} expired because payment was not submitted within 15 minutes.",
            target_url="/portal/reseller/history", source_type="orders", source_id=order_id,
            dedupe_key=f"order-expired-{order_id}",
        )
    return len(due_orders)


def expire_unpaid_orders(account_id: int | None = None, limit: int = 100) -> int:
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('meattrack-unpaid-order-expiry')) AS locked;")
        if not cur.fetchone()["locked"]:
            return 0
        return _expire_unpaid_orders_cursor(cur, account_id=account_id, limit=limit)


def _normalize_order_cancellation_reason(reason: str, details: str = "") -> str:
    reason_key = str(reason).strip().lower()
    label = _CANCELLATION_REASON_LABELS.get(reason_key)
    if label is None:
        raise ValueError("Select a cancellation reason.")
    clean_details = " ".join(str(details).strip().split())
    if len(clean_details) > 300:
        raise ValueError("Cancellation details must be 300 characters or fewer.")
    if reason_key == "other" and len(clean_details) < 5:
        raise ValueError("Add a short explanation for the cancellation.")
    return f"{label}: {clean_details}" if clean_details else label


def cancel_reseller_order(
    account_id: int,
    order_id: int,
    cancellation_reason: str,
    cancellation_details: str = "",
) -> dict:
    clean_reason = _normalize_order_cancellation_reason(cancellation_reason, cancellation_details)
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT o.order_id, o.status, o.created_by_account_id,
                   o.team_leader_account_id, o.payment_transaction_number,
                   o.payment_paid_at, o.payment_amount, o.payment_confirmed_at,
                   r.business_name,
                   EXISTS (
                       SELECT 1 FROM order_payment_proofs opp
                       WHERE opp.order_id = o.order_id
                   ) AS has_payment_proof
            FROM orders o
            JOIN resellers r ON r.reseller_id = o.reseller_id
            WHERE o.order_id = %s
              AND o.order_type = 'reseller'
              AND o.created_by_account_id = %s
            FOR UPDATE OF o;
            """,
            (order_id, account_id),
        )
        order = cur.fetchone()
        if not order:
            raise ValueError("Order was not found.")
        if order["status"] not in {"to_pay", "pending"}:
            raise ValueError("This order can no longer be cancelled.")
        payment_amount = Decimal(str(order.get("payment_amount") or 0))
        payment_exists = any((
            order.get("has_payment_proof"),
            order.get("payment_transaction_number"),
            order.get("payment_paid_at"),
            order.get("payment_confirmed_at"),
            payment_amount > 0,
        ))
        if payment_exists:
            raise ValueError(
                "Orders with a submitted payment cannot be cancelled. Contact your sales leader."
            )

        cancelled_at = business_now()
        cur.execute("DELETE FROM order_stock_reservations WHERE order_id = %s;", (order_id,))
        cur.execute(
            """
            UPDATE orders
            SET status = 'cancelled', cancellation_reason = %s, cancelled_at = %s
            WHERE order_id = %s
            RETURNING order_id, status, cancellation_reason, cancelled_at;
            """,
            (clean_reason, cancelled_at, order_id),
        )
        result = dict(cur.fetchone())
        _add_log_cursor(
            cur,
            actor_account_id=account_id,
            action="cancelled_reseller_order",
            entity_type="orders",
            entity_id=order_id,
        )
        _create_order_system_message_cursor(
            cur,
            reseller_account_id=account_id,
            order_id=order_id,
            event_type="cancelled",
            title="Order cancelled",
            message=f"You cancelled order #{order_id}. Reason: {clean_reason}",
            dedupe_key=f"order-cancelled-{order_id}",
        )
        _create_notification_cursor(
            cur,
            recipient_account_id=order.get("team_leader_account_id"),
            category="order",
            severity="info",
            title="Reseller order cancelled",
            message=(
                f"{order['business_name']} cancelled order #{order_id}. "
                f"Reason: {clean_reason}"
            ),
            target_url="/portal/team-leader/orders?status=cancelled",
            source_type="orders",
            source_id=order_id,
            dedupe_key=f"order-cancelled-{order_id}-leader",
        )
    return clean_row(result)


def submit_order_payment(
    account_id: int,
    order_id: int,
    attachments: list[dict],
    payment_details: ConfirmedPayment,
) -> dict:
    if not attachments:
        raise ValueError("Attach at least one proof of payment screenshot.")
    if len(attachments) > 3:
        raise ValueError("Attach up to 3 payment screenshots only.")

    expired = False
    result: dict = {}
    try:
        with get_transaction_cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, o.status, o.payment_due_at, o.reseller_id,
                       o.team_leader_account_id, o.created_by_account_id,
                       r.business_name
                FROM orders o
                JOIN resellers r ON r.reseller_id = o.reseller_id
                WHERE o.order_id = %s
                  AND o.order_type = 'reseller'
                  AND o.created_by_account_id = %s
                FOR UPDATE OF o;
                """,
                (order_id, account_id),
            )
            order = cur.fetchone()
            if not order:
                raise ValueError("Order was not found.")
            if order["status"] != "to_pay":
                raise ValueError("This order is no longer awaiting payment.")
            if order["payment_due_at"] is None or order["payment_due_at"] <= business_now():
                cur.execute("DELETE FROM order_stock_reservations WHERE order_id = %s;", (order_id,))
                cur.execute(
                    "UPDATE orders SET status = 'expired', expired_at = now() WHERE order_id = %s;",
                    (order_id,),
                )
                _add_log_cursor(
                    cur, actor_account_id=None, action="expired_unpaid_order",
                    entity_type="orders", entity_id=order_id,
                )
                _create_notification_cursor(
                    cur, recipient_account_id=order["created_by_account_id"],
                    category="order", severity="warning",
                    title="Payment window expired",
                    message=f"Order #{order_id} expired because payment was not submitted within 15 minutes.",
                    target_url="/portal/reseller/history", source_type="orders", source_id=order_id,
                    dedupe_key=f"order-expired-{order_id}",
                )
                expired = True
            else:
                cur.execute(
                    """
                    SELECT 1 FROM orders
                    WHERE reseller_id = %s
                      AND payment_transaction_key = %s
                      AND order_id <> %s
                    LIMIT 1;
                    """,
                    (order["reseller_id"], payment_details["transaction_key"], order_id),
                )
                if cur.fetchone():
                    raise ValueError("This payment transaction number has already been used for another order.")
                _insert_order_payment_proofs_cursor(cur, account_id, order_id, attachments)
                cur.execute(
                    """
                    UPDATE orders
                    SET status = 'pending',
                        payment_transaction_number = %s,
                        payment_transaction_key = %s,
                        payment_paid_at = %s,
                        payment_amount = %s,
                        payment_ocr_confidence = %s,
                        payment_extraction_method = %s,
                        payment_confirmed_at = %s,
                        payment_ocr_engine = %s
                    WHERE order_id = %s
                    RETURNING order_id, status, payment_due_at, total_amount;
                    """,
                    (
                        payment_details["transaction_number"], payment_details["transaction_key"],
                        payment_details["paid_at"], payment_details["payment_amount"],
                        payment_details["ocr_confidence"], payment_details["extraction_method"],
                        payment_details["confirmed_at"], payment_details["ocr_engine"], order_id,
                    ),
                )
                result = dict(cur.fetchone())
                _add_log_cursor(
                    cur, actor_account_id=account_id, action="submitted_order_payment",
                    entity_type="orders", entity_id=order_id,
                )
                _create_notification_cursor(
                    cur, recipient_account_id=order["team_leader_account_id"], category="order", severity="warning",
                    title="Pending reseller order",
                    message=f"Order #{order_id} from {order['business_name']} was paid and needs review.",
                    target_url="/portal/team-leader/orders", source_type="orders", source_id=order_id,
                    dedupe_key=f"reseller-order-{order_id}",
                )
    except psycopg2.Error as exc:
        message = str(exc)
        if "already been used" in message or "ux_orders_reseller_payment_transaction" in message:
            raise ValueError("This payment transaction number has already been used for another order.")
        raise
    if expired:
        raise ValueError("The 15-minute payment window expired. This order was released and cannot be paid.")
    return clean_row(result)


def get_order_payment_proof(proof_id: int, account_id: int | None, role_key: str | None) -> dict | None:
    proof = fetch_one(
        """
        SELECT opp.order_payment_proof_id,
               opp.order_id,
               opp.filename,
               opp.content_type,
               opp.content,
               opp.size_bytes,
               o.created_by_account_id,
               o.team_leader_account_id
        FROM order_payment_proofs opp
        JOIN orders o ON o.order_id = opp.order_id
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        WHERE opp.order_payment_proof_id = %s
        LIMIT 1;
        """,
        (proof_id,),
    )
    if not proof:
        return None
    if role_key == "owner":
        return clean_row(proof)
    if role_key == "reseller" and account_id == proof["created_by_account_id"]:
        return clean_row(proof)
    if role_key in {"team-leader", "sales-supervisor"} and account_id == proof["team_leader_account_id"]:
        return clean_row(proof)
    return None


def reseller_most_bought_products(limit: int = 6, account_id: int | None = None) -> list[dict]:
    if limit < 1:
        raise ValueError("limit must be positive")
    where = ["o.order_type = 'reseller'", "p.item_type = 'finished_product'"]
    params: list[object] = []
    if account_id is not None:
        profile = reseller_account_profile(account_id)
        where.append("o.reseller_id = %s")
        params.append(profile["reseller_id"])
    params.append(limit)
    return clean_row(fetch_all(f"""
        SELECT p.item_id AS product_id,
               p.name,
               p.category,
               p.unit,
               p.base_price,
               COALESCE(SUM(oi.quantity), 0) AS total_quantity,
               COALESCE(SUM(oi.line_total), 0) AS total_amount,
               COUNT(DISTINCT o.order_id) AS order_count
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE {" AND ".join(where)}
        GROUP BY p.item_id, p.name, p.category, p.unit, p.base_price
        ORDER BY total_quantity DESC, total_amount DESC, p.name ASC
        LIMIT %s;
    """, tuple(params)))


def reseller_sales_series(start_date: date, end_date: date, status: str = "fulfilled", account_id: int | None = None) -> list[dict]:
    if start_date > end_date:
        raise ValueError("start_date must be before end_date")
    allowed_statuses = {"", "all", "pending", "approved", "fulfilled", "rejected", "cancelled"}
    if status not in allowed_statuses:
        raise ValueError("Unknown order status")

    where = [
        "o.order_type = 'reseller'",
        "COALESCE(o.fulfilled_at::date, o.order_date) BETWEEN %s AND %s",
    ]
    params: list[object] = [start_date, end_date]
    if account_id is not None:
        profile = reseller_account_profile(account_id)
        where.append("o.reseller_id = %s")
        params.append(profile["reseller_id"])
    if status and status != "all":
        where.append("o.status = %s")
        params.append(status)

    return clean_row(fetch_all(f"""
        SELECT COALESCE(o.fulfilled_at::date, o.order_date) AS sale_date,
               COALESCE(SUM(o.total_amount), 0) AS total_sales,
               COUNT(*) AS order_count
        FROM orders o
        WHERE {" AND ".join(where)}
        GROUP BY COALESCE(o.fulfilled_at::date, o.order_date)
        ORDER BY sale_date ASC;
    """, tuple(params)))


def count_orders(
    order_type: str | None = None,
    q: str = "",
    status: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> int:
    if order_type not in {None, "reseller", "walk_in"}:
        raise ValueError("order_type must be reseller, walk_in, or None")
    query = """
        SELECT COUNT(*) AS total
        FROM orders o
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        LEFT JOIN order_refunds refund ON refund.order_id = o.order_id
    """
    where = []
    params: list[object] = []
    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)
    if status == "refunded":
        where.append("refund.status = 'completed'")
    elif status:
        where.append("o.status = %s")
        params.append(status)
    if team_leader_account_id is not None:
        if order_type == "walk_in":
            where.append("o.created_by_account_id = %s")
            params.append(team_leader_account_id)
        elif order_type == "reseller":
            where.append("o.team_leader_account_id = %s")
            params.append(team_leader_account_id)
        else:
            where.append("(o.created_by_account_id = %s OR o.team_leader_account_id = %s)")
            params.extend([team_leader_account_id, team_leader_account_id])
    if reseller_account_id is not None:
        where.append("""o.reseller_id = (
            SELECT a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
        )""")
        params.append(reseller_account_id)
    q = q.strip()
    if q:
        where.append("""(
            CAST(o.order_id AS text) ILIKE %s
            OR COALESCE(r.business_name, 'Retail counter') ILIKE %s
            OR EXISTS (
                SELECT 1
                FROM order_items oi_search
                JOIN inventory_items p_search ON p_search.item_id = oi_search.product_id
                WHERE oi_search.order_id = o.order_id
                  AND p_search.name ILIKE %s
            )
        )""")
        search = f"%{q}%"
        params.extend([search, search, search])
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def product_by_id(product_id: int) -> dict | None:
    res = fetch_one("""
        SELECT p.item_id AS product_id, p.name, p.description, p.unit, p.base_price, p.is_active,
               p.category, p.pack_size, p.pack_size_unit, p.pack_content_status,
               (
                   SELECT COUNT(*)
                   FROM product_recipes pr
                   JOIN inventory_items rm ON rm.item_id = pr.material_item_id
                   WHERE pr.product_item_id = p.item_id
                     AND rm.item_type = 'raw_material'
               ) AS recipe_count,
               GREATEST(COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
               ), 0) - COALESCE((
                   SELECT SUM(osr.quantity)
                   FROM order_stock_reservations osr
                   WHERE osr.product_id = p.item_id
               ), 0), 0) AS available
        FROM inventory_items p
        LEFT JOIN inventory_batches pb ON pb.item_id = p.item_id
        WHERE p.item_id = %s
          AND p.item_type = 'finished_product'
        GROUP BY p.item_id, p.name, p.description, p.unit, p.base_price, p.is_active, p.category,
                 p.pack_size, p.pack_size_unit, p.pack_content_status;
    """, (product_id,))
    return clean_row(res)


def list_reseller_cart_items(account_id: int) -> list[dict]:
    return clean_row(fetch_all("""
        SELECT rci.cart_item_id, rci.account_id, rci.product_id,
               p.name, p.category, p.description, p.unit, p.base_price,
               GREATEST(COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
               ), 0) - COALESCE((
                   SELECT SUM(osr.quantity)
                   FROM order_stock_reservations osr
                   WHERE osr.product_id = p.item_id
               ), 0), 0) AS available,
               rci.quantity AS cart_quantity,
               round(rci.quantity * p.base_price, 2) AS line_total,
               rci.updated_at
        FROM reseller_cart_items rci
        JOIN inventory_items p ON p.item_id = rci.product_id
        LEFT JOIN inventory_batches pb ON pb.item_id = p.item_id
        WHERE rci.account_id = %s
          AND p.item_type = 'finished_product'
          AND p.is_active = true
        GROUP BY rci.cart_item_id, rci.account_id, rci.product_id,
                 p.item_id, p.name, p.category, p.description, p.unit, p.base_price,
                 rci.quantity, rci.updated_at
        ORDER BY rci.updated_at DESC, rci.cart_item_id DESC;
    """, (account_id,)))


def reseller_cart_count(account_id: int) -> float:
    row = fetch_one("""
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM reseller_cart_items
        WHERE account_id = %s;
    """, (account_id,))
    return float(row["total"] if row else 0)


def reseller_order_confirmation(account_id: int, order_id: int) -> dict | None:
    return clean_row(fetch_one(
        """
        SELECT o.order_id, o.status, o.total_amount, o.payment_due_at
        FROM orders o
        JOIN accounts a ON a.reseller_id = o.reseller_id
        WHERE a.account_id = %s
          AND o.order_id = %s
          AND o.order_type = 'reseller'
        LIMIT 1;
        """,
        (account_id, order_id),
    ))


def _reseller_available_product(cur, product_id: int) -> dict | None:
    cur.execute("""
        SELECT p.item_id AS product_id, p.name, p.is_active,
               GREATEST(COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND pb.expiry_date >= CURRENT_DATE
               ), 0) - COALESCE((
                   SELECT SUM(osr.quantity)
                   FROM order_stock_reservations osr
                   WHERE osr.product_id = p.item_id
               ), 0), 0) AS available
        FROM inventory_items p
        LEFT JOIN inventory_batches pb ON pb.item_id = p.item_id
        WHERE p.item_id = %s
          AND p.item_type = 'finished_product'
        GROUP BY p.item_id, p.name, p.is_active;
    """, (product_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _require_reseller_quantity_available(product: dict | None, quantity: Decimal) -> None:
    if product is None or not product["is_active"]:
        raise ValueError("Unknown product")
    available = Decimal(product["available"])
    if quantity > available:
        raise ValueError(
            f"Only {display_decimal(available)} packs of {product['name']} are currently available. "
            "Some stock may already be reserved by orders awaiting payment."
        )


def add_reseller_cart_item(account_id: int, product_id: int, quantity: object) -> dict:
    clean_quantity = measurements.parse_whole_packs(quantity)
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
            (f"reseller-cart:{account_id}:{product_id}",),
        )
        product = _reseller_available_product(cur, product_id)
        cur.execute("""
            SELECT quantity
            FROM reseller_cart_items
            WHERE account_id = %s AND product_id = %s
            FOR UPDATE;
        """, (account_id, product_id))
        existing = cur.fetchone()
        requested_quantity = clean_quantity + (Decimal(existing["quantity"]) if existing else Decimal("0"))
        measurements.parse_whole_packs(requested_quantity)
        _require_reseller_quantity_available(product, requested_quantity)
        cur.execute("""
            INSERT INTO reseller_cart_items (account_id, product_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_id, product_id)
            DO UPDATE SET
                quantity = reseller_cart_items.quantity + EXCLUDED.quantity,
                updated_at = now()
            RETURNING cart_item_id, account_id, product_id, quantity;
        """, (account_id, product_id, clean_quantity))
        row = cur.fetchone()
    return clean_row(row)


def update_reseller_cart_item(account_id: int, product_id: int, quantity: object) -> None:
    clean_quantity = measurements.parse_whole_packs(quantity)
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
            (f"reseller-cart:{account_id}:{product_id}",),
        )
        product = _reseller_available_product(cur, product_id)
        _require_reseller_quantity_available(product, clean_quantity)
        cur.execute("""
            INSERT INTO reseller_cart_items (account_id, product_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_id, product_id)
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                updated_at = now();
        """, (account_id, product_id, clean_quantity))


def remove_reseller_cart_item(account_id: int, product_id: int) -> None:
    execute_write("""
        DELETE FROM reseller_cart_items
        WHERE account_id = %s
          AND product_id = %s;
    """, (account_id, product_id))


def clear_reseller_cart(account_id: int) -> None:
    execute_write("DELETE FROM reseller_cart_items WHERE account_id = %s;", (account_id,))


def _reserve_order_stock_cursor(cur, order_id: int, quantities: dict[int, Decimal]) -> None:
    _plan_fefo_allocations(cur, quantities)
    for product_id, quantity in quantities.items():
        cur.execute(
            """
            INSERT INTO order_stock_reservations (order_id, product_id, quantity)
            VALUES (%s, %s, %s);
            """,
            (order_id, product_id, quantity),
        )


def create_order_from_items(
    role: str,
    items: list[tuple[int, object]],
    notes: str = "",
    account_id: int | None = None,
    *,
    checkout_cart: bool = False,
    checkout_product_ids: list[int] | None = None,
    payment_proofs: list[dict] | None = None,
    payment_details: ConfirmedPayment | None = None,
) -> dict:
    if not items and not checkout_cart:
        raise ValueError("Add at least one product to the cart.")

    quantities: dict[int, Decimal] = {}
    for product_id, quantity in items:
        try:
            clean_product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Unknown product")
        clean_quantity = measurements.parse_whole_packs(quantity)
        quantities[clean_product_id] = quantities.get(clean_product_id, Decimal("0")) + clean_quantity
        measurements.parse_whole_packs(quantities[clean_product_id])

    order_type = "reseller" if role == "reseller" else "walk_in"
    reseller_id = None
    created_by_name = "MEATTRACK"
    team_leader_account_id = None
    creator_id = account_id
    status = "to_pay" if role == "reseller" else "pending"
    payment_due_at = None

    if role == "reseller":
        if account_id is None:
            raise ValueError("Your session expired. Please sign in again.")
        payment_window_minutes = int(business_setting("order.payment_window_minutes"))
        payment_due_at = business_now() + timedelta(minutes=payment_window_minutes)
    elif account_id is not None:
        account = fetch_one("SELECT name FROM accounts WHERE account_id = %s LIMIT 1;", (account_id,))
        if account:
            created_by_name = account["name"]

    with get_transaction_cursor() as cur:
        if role == "reseller":
            cur.execute(
                """
                SELECT a.account_id, r.reseller_id, r.business_name, r.team_leader_account_id
                FROM accounts a
                JOIN resellers r ON r.reseller_id = a.reseller_id
                WHERE a.account_id = %s AND a.account_type = 'reseller'
                  AND a.is_active = true AND a.activation_status = 'active'
                FOR UPDATE OF a, r;
                """,
                (account_id,),
            )
            locked_profile = cur.fetchone()
            if not locked_profile:
                raise ValueError("This reseller account is not linked to an active reseller profile.")
            if locked_profile["team_leader_account_id"] is None:
                raise ValueError("Your reseller account is not assigned to a team leader yet. Please contact the owner before submitting orders.")
            cur.execute(
                """
                SELECT account_type, team_leader_role, is_active
                FROM accounts
                WHERE account_id = %s
                FOR SHARE;
                """,
                (locked_profile["team_leader_account_id"],),
            )
            locked_leader = cur.fetchone()
            if (
                not locked_leader
                or locked_leader["account_type"] != "team_leader"
                or locked_leader["team_leader_role"] not in {"sales", "sales-supervisor"}
                or not locked_leader["is_active"]
            ):
                raise ValueError("Your assigned sales team leader is unavailable. Please contact the owner before submitting orders.")
            reseller_id = locked_profile["reseller_id"]
            created_by_name = locked_profile["business_name"]
            team_leader_account_id = locked_profile["team_leader_account_id"]
            _expire_unpaid_orders_cursor(cur, reseller_id=reseller_id)
            cur.execute(
                """
                SELECT order_id, payment_due_at
                FROM orders
                WHERE reseller_id = %s AND status = 'to_pay'
                LIMIT 1;
                """,
                (reseller_id,),
            )
            active_unpaid = cur.fetchone()
            if active_unpaid:
                raise ValueError(
                    f"Order #{active_unpaid['order_id']} is still awaiting payment. "
                    "Pay or wait for it to expire before placing another order."
                )

        if checkout_cart:
            if role != "reseller" or account_id is None:
                raise ValueError("Cart checkout requires a signed-in reseller.")
            selected_product_ids = None
            if checkout_product_ids is not None:
                try:
                    selected_product_ids = {int(product_id) for product_id in checkout_product_ids}
                except (TypeError, ValueError):
                    raise ValueError("One or more selected cart items are invalid.")
                if not selected_product_ids:
                    raise ValueError("Select at least one cart item to continue.")
            cur.execute(
                """
                SELECT product_id, quantity
                FROM reseller_cart_items
                WHERE account_id = %s
                ORDER BY product_id
                FOR UPDATE;
                """,
                (account_id,),
            )
            locked_cart = cur.fetchall()
            if not locked_cart:
                raise ValueError("Your cart is empty.")
            cart_product_ids = {int(row["product_id"]) for row in locked_cart}
            if selected_product_ids is not None and not selected_product_ids.issubset(cart_product_ids):
                raise ValueError("One or more selected items are no longer in your cart. Please review your cart and try again.")
            quantities = {
                int(row["product_id"]): measurements.parse_whole_packs(row["quantity"])
                for row in locked_cart
                if selected_product_ids is None or int(row["product_id"]) in selected_product_ids
            }

        cur.execute("""
            SELECT p.item_id AS product_id, p.name, p.unit, p.base_price, p.is_active,
                   GREATEST(COALESCE((
                       SELECT SUM(pb.quantity_available)
                       FROM inventory_batches pb
                       WHERE pb.item_id = p.item_id
                         AND pb.quality_status = 'approved'
                         AND pb.expiry_date >= CURRENT_DATE
                   ), 0) - COALESCE((
                       SELECT SUM(osr.quantity)
                       FROM order_stock_reservations osr
                       WHERE osr.product_id = p.item_id
                   ), 0), 0) AS available
            FROM inventory_items p
            WHERE p.item_type = 'finished_product'
              AND p.is_active = true
              AND p.item_id = ANY(%s);
        """, (list(quantities.keys()),))
        products = {row["product_id"]: row for row in cur.fetchall()}
        if len(products) != len(quantities):
            raise ValueError("Unknown product")

        if creator_id is None:
            cur.execute("SELECT account_id FROM accounts WHERE name = %s LIMIT 1;", (created_by_name,))
            acc = cur.fetchone()
            creator_id = acc["account_id"] if acc else None
        actor_name = _inventory_actor_name(cur, creator_id)

        total = Decimal("0.00")
        lines = []
        for product_id, quantity in quantities.items():
            product = products[product_id]
            if role == "reseller":
                _require_reseller_quantity_available(product, quantity)
            line_total = (product["base_price"] * quantity).quantize(Decimal("0.01"))
            measurements.require_capacity(line_total, "Order line total", scale=2)
            total += line_total
            measurements.require_capacity(total, "Order total", scale=2)
            lines.append((product_id, quantity, product["unit"], product["base_price"]))

        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, team_leader_account_id,
                                approved_by_account_id, approved_at, status, order_date, fulfilled_at, total_amount, notes,
                                payment_transaction_number, payment_transaction_key, payment_paid_at,
                                payment_amount, payment_ocr_confidence, payment_extraction_method,
                                payment_confirmed_at, payment_ocr_engine, payment_due_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING order_id, order_type, reseller_id, status, order_date, total_amount, notes, payment_due_at;
        """, (
            order_type, reseller_id, creator_id, team_leader_account_id,
            creator_id if role != "reseller" else None,
            business_now() if role != "reseller" else None,
            status, business_now(), None,
            total.quantize(Decimal("0.01")), notes,
            None, None, None, None, None, None, None, None,
            payment_due_at,
        ))
        ord_res = dict(cur.fetchone())

        order_id = ord_res["order_id"]
        for product_id, quantity, unit, unit_price in lines:
            cur.execute("""
                INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
                VALUES (%s, %s, %s, %s, %s);
            """, (order_id, product_id, quantity, unit, unit_price))

        if role == "reseller":
            _reserve_order_stock_cursor(cur, order_id, quantities)

        if role != "reseller":
            allocations = _plan_fefo_allocations(cur, quantities)
            _apply_fefo_allocations(
                cur,
                allocations,
                order_id=order_id,
                actor_account_id=creator_id,
                actor_name=actor_name,
            )
            cur.execute("""
                UPDATE orders
                SET status = 'fulfilled', fulfilled_at = %s
                WHERE order_id = %s;
            """, (business_now(), order_id))
            ord_res["status"] = "fulfilled"

        _add_log_cursor(
            cur, actor_account_id=creator_id,
            action="created_reseller_order" if role == "reseller" else "created_walk_in_sale",
            entity_type="orders", entity_id=order_id,
        )
        if checkout_cart:
            cur.execute(
                """
                DELETE FROM reseller_cart_items
                WHERE account_id = %s
                  AND product_id = ANY(%s);
                """,
                (account_id, list(quantities.keys())),
            )

    return clean_row(ord_res)


def create_order(role: str, product_id: int, quantity: object, notes: str = "", account_id: int | None = None) -> dict:
    return create_order_from_items(role, [(product_id, quantity)], notes, account_id=account_id)


def checkout_reseller_cart(
    account_id: int,
    notes: str = "",
    product_ids: list[int] | None = None,
    payment_proofs: list[dict] | None = None,
    payment_details: ConfirmedPayment | None = None,
) -> dict:
    if product_ids is None:
        return create_order_from_items(
            "reseller",
            [],
            notes,
            account_id=account_id,
            checkout_cart=True,
            payment_proofs=payment_proofs,
            payment_details=payment_details,
        )
    return create_order_from_items(
        "reseller",
        [],
        notes,
        account_id=account_id,
        checkout_cart=True,
        checkout_product_ids=product_ids,
        payment_proofs=payment_proofs,
        payment_details=payment_details,
    )


ORDER_REJECTION_REASON_MAX_LENGTH = 400


def _normalize_order_rejection_reason(value: str) -> str:
    cleaned = "\n".join(line.strip() for line in str(value).strip().splitlines() if line.strip())
    if len(cleaned) < 5:
        raise ValueError("Enter at least 5 characters explaining why the order was rejected.")
    if len(cleaned) > ORDER_REJECTION_REASON_MAX_LENGTH:
        raise ValueError(f"Keep the rejection reason under {ORDER_REJECTION_REASON_MAX_LENGTH} characters.")
    return cleaned


def decide_order(
    order_id: int,
    decision: str,
    team_leader_account_id: int | None = None,
    actor_account_id: int | None = None,
    rejection_reason: str = "",
    payment_received: bool | None = None,
) -> bool:
    if decision not in {"approve", "reject", "fulfill"}:
        return False
    cleaned_rejection_reason = ""
    refund_record: dict | None = None

    with get_transaction_cursor() as cur:
        scope = ""
        params: list[object] = [order_id]
        if team_leader_account_id is not None:
            scope = " AND o.team_leader_account_id = %s"
            params.append(team_leader_account_id)
        cur.execute(f"""
            SELECT o.*
            FROM orders o
            WHERE o.order_id = %s
              AND o.order_type = 'reseller'
              {scope}
            FOR UPDATE OF o;
        """, tuple(params))
        order_row = cur.fetchone()
        if not order_row:
            return False
        ord_res = dict(order_row)

        leader_id = actor_account_id or team_leader_account_id
        if leader_id is None:
            cur.execute("SELECT account_id FROM accounts WHERE account_type = 'team_leader' ORDER BY account_id LIMIT 1;")
            leader = cur.fetchone()
            leader_id = leader["account_id"] if leader else None
        actor_name = _inventory_actor_name(cur, leader_id)

        if decision == "approve":
            if ord_res["status"] != "pending":
                return False
            cur.execute("SELECT 1 FROM order_payment_proofs WHERE order_id = %s LIMIT 1;", (order_id,))
            if not cur.fetchone():
                raise ValueError("Proof of payment is required before approving this order.")
            cur.execute("""
                UPDATE orders
                SET status = 'approved', approved_by_account_id = %s, approved_at = %s
                WHERE order_id = %s;
            """, (leader_id, business_now(), order_id))
        elif decision == "reject":
            if ord_res["status"] != "pending":
                return False
            cleaned_rejection_reason = _normalize_order_rejection_reason(rejection_reason)
            if payment_received is None:
                raise ValueError("Indicate whether payment was received before rejecting the order.")
            if payment_received and Decimal(str(ord_res.get("payment_amount") or 0)) <= 0:
                raise ValueError("A confirmed payment amount is required before creating a refund.")
            cur.execute("""
                UPDATE orders
                SET status = 'rejected', approved_by_account_id = COALESCE(approved_by_account_id, %s),
                    approved_at = COALESCE(approved_at, %s), rejection_reason = %s,
                    rejection_payment_received = %s
                WHERE order_id = %s;
            """, (leader_id, business_now(), cleaned_rejection_reason, payment_received, order_id))
            cur.execute("DELETE FROM order_stock_reservations WHERE order_id = %s;", (order_id,))
            if payment_received:
                assigned_supervisor_id = None
                if leader_id is not None:
                    cur.execute(
                        "SELECT team_leader_role FROM accounts WHERE account_id = %s AND is_active = true;",
                        (leader_id,),
                    )
                    actor = cur.fetchone()
                    if actor and actor.get("team_leader_role") == "sales-supervisor":
                        assigned_supervisor_id = int(leader_id)
                if assigned_supervisor_id is None:
                    cur.execute(
                        """
                        WITH supervisors AS (
                            SELECT a.account_id,
                                   row_number() OVER (ORDER BY a.name, a.account_id) - 1 AS position,
                                   count(*) OVER () AS supervisor_count
                            FROM accounts a
                            WHERE a.account_type = 'team_leader'
                              AND a.team_leader_role = 'sales-supervisor'
                              AND a.is_active = true
                        ), assignment_count AS (
                            SELECT COUNT(*)::bigint AS total
                            FROM inquiries i
                            JOIN accounts a ON a.account_id = i.assigned_team_leader_account_id
                            WHERE a.team_leader_role = 'sales-supervisor'
                        )
                        SELECT s.account_id
                        FROM supervisors s CROSS JOIN assignment_count c
                        WHERE s.position = c.total % s.supervisor_count
                        LIMIT 1;
                        """
                    )
                    supervisor = cur.fetchone()
                    assigned_supervisor_id = int(supervisor["account_id"]) if supervisor else None
                cur.execute(
                    """
                    INSERT INTO order_refunds (
                        order_id, amount, currency, status, requested_by_account_id,
                        assigned_supervisor_account_id, requested_at, updated_at
                    ) VALUES (%s, %s, 'PHP', 'pending', %s, %s, %s, %s)
                    RETURNING order_refund_id, order_id, amount, assigned_supervisor_account_id;
                    """,
                    (
                        order_id, ord_res["payment_amount"], leader_id,
                        assigned_supervisor_id, business_now(), business_now(),
                    ),
                )
                refund_record = dict(cur.fetchone())
        else:
            if ord_res["status"] != "approved":
                raise ValueError("Order must be approved before it can be fulfilled.")
            cur.execute("""
                SELECT product_id, SUM(quantity) AS quantity
                FROM order_items
                WHERE order_id = %s
                GROUP BY product_id
                ORDER BY product_id;
            """, (order_id,))
            quantities = {row["product_id"]: Decimal(row["quantity"]) for row in cur.fetchall()}
            allocations = _plan_fefo_allocations(cur, quantities, reservation_order_id=order_id)
            _apply_fefo_allocations(
                cur,
                allocations,
                order_id=order_id,
                actor_account_id=leader_id,
                actor_name=actor_name,
            )
            cur.execute("DELETE FROM order_stock_reservations WHERE order_id = %s;", (order_id,))
            cur.execute("""
                UPDATE orders
                SET status = 'fulfilled', fulfilled_at = %s
                WHERE order_id = %s;
            """, (business_now(), order_id))

        action = {
            "approve": "approved_reseller_order",
            "reject": "rejected_reseller_order",
            "fulfill": "fulfilled_reseller_order",
        }[decision]
        _add_log_cursor(cur, actor_account_id=leader_id, action=action, entity_type="orders", entity_id=order_id)
        system_message = {
            "approve": (
                "approved",
                "Order approved",
                f"Your reseller order #{order_id} was approved and is ready for fulfillment.",
                None,
            ),
            "reject": (
                "rejected",
                "Order rejected",
                f"Your reseller order #{order_id} was rejected.",
                cleaned_rejection_reason,
            ),
            "fulfill": (
                "fulfilled",
                "Order fulfilled",
                f"Your reseller order #{order_id} was fulfilled and recorded as a completed sale.",
                None,
            ),
        }[decision]
        recipient_account_id = ord_res.get("created_by_account_id")
        if recipient_account_id is not None:
            system_message_id = _create_order_system_message_cursor(
                cur,
                reseller_account_id=int(recipient_account_id),
                order_id=order_id,
                event_type=system_message[0],
                title=system_message[1],
                message=system_message[2],
                rejection_reason=system_message[3],
            )
            notification = {
                "approve": (
                    "info",
                    "Order approved",
                    f"Order #{order_id} was approved. Open its system message for details.",
                ),
                "reject": (
                    "critical",
                    "Order rejected",
                    f"Order #{order_id} was rejected. Open its system message for the reason.",
                ),
                "fulfill": (
                    "info",
                    "Order fulfilled",
                    f"Order #{order_id} was fulfilled. Open its system message for details.",
                ),
            }[decision]
            target_url = (
                f"/portal/reseller/inbox?system_message={system_message_id}"
                f"#system-message-{system_message_id}"
            )
            _create_notification_cursor(
                cur,
                recipient_account_id=int(recipient_account_id),
                category="order",
                severity=notification[0],
                title=notification[1],
                message=notification[2],
                target_url=target_url,
                source_type="order_system_messages",
                source_id=system_message_id,
                dedupe_key=f"order-{decision}-{order_id}",
            )
            if decision == "reject" and refund_record is not None:
                amount = Decimal(str(refund_record["amount"]))
                refund_message_id = _create_order_system_message_cursor(
                    cur,
                    reseller_account_id=int(recipient_account_id),
                    order_id=order_id,
                    event_type="refund_pending",
                    title="Refund pending",
                    message=f"PHP {amount:,.2f} is awaiting manual refund processing for Order #{order_id}.",
                    dedupe_key=f"refund-{refund_record['order_refund_id']}-pending",
                )
                _create_notification_cursor(
                    cur,
                    recipient_account_id=int(recipient_account_id),
                    category="order",
                    severity="warning",
                    title="Refund pending",
                    message=f"A manual refund of PHP {amount:,.2f} is pending for Order #{order_id}.",
                    target_url=f"/portal/reseller/inbox?system_message={refund_message_id}#system-message-{refund_message_id}",
                    source_type="order_system_messages",
                    source_id=refund_message_id,
                    dedupe_key=f"refund-{refund_record['order_refund_id']}-pending-reseller",
                )
        if decision == "reject" and refund_record is not None:
            refund_id = int(refund_record["order_refund_id"])
            supervisor_id = refund_record.get("assigned_supervisor_account_id")
            recipient_kwargs = (
                {"recipient_account_id": int(supervisor_id)} if supervisor_id is not None
                else {"recipient_role": "owner"}
            )
            _create_notification_cursor(
                cur,
                **recipient_kwargs,
                category="order",
                severity="warning",
                title="Refund pending",
                message=f"Order #{order_id} needs a manual refund of PHP {Decimal(str(refund_record['amount'])):,.2f}.",
                target_url=(
                    f"/portal/sales-supervisor/refunds?refund={refund_id}"
                    if supervisor_id is not None else f"/portal/owner/refunds?refund={refund_id}"
                ),
                source_type="order_refunds",
                source_id=refund_id,
                dedupe_key=f"refund-{refund_id}-pending-processor",
            )
    return True


__all__ = ['RESELLER_ORDER_CANCELLATION_REASONS', 'list_orders', 'add_order_payment_proofs', 'submit_order_payment', 'cancel_reseller_order', 'expire_unpaid_orders', 'get_order_payment_proof', 'reseller_most_bought_products', 'reseller_sales_series', 'count_orders', 'product_by_id', 'list_reseller_cart_items', 'reseller_cart_count', 'reseller_order_confirmation', '_reseller_available_product', '_require_reseller_quantity_available', 'add_reseller_cart_item', 'update_reseller_cart_item', 'remove_reseller_cart_item', 'clear_reseller_cart', 'create_order_from_items', 'create_order', 'checkout_reseller_cart', 'decide_order']
