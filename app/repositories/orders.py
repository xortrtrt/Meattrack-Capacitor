from __future__ import annotations


from ._shared import *
from .accounts import reseller_account_profile
from .inventory import _apply_fefo_allocations, _inventory_actor_name, _plan_fefo_allocations
from .notifications import _create_notification_cursor


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
    if status:
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
               o.status, o.order_date, o.total_amount, o.notes
        FROM orders o
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = o.team_leader_account_id
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


def add_order_payment_proofs(account_id: int, order_id: int, attachments: list[dict]) -> int:
    if not attachments:
        raise ValueError("Attach at least one proof of payment screenshot.")
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
        inserted = 0
        for attachment in attachments:
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
        _add_log_cursor(cur, actor_account_id=account_id, action="uploaded_payment_proof", entity_type="orders", entity_id=order_id)
        _create_notification_cursor(
            cur, recipient_account_id=order["team_leader_account_id"], category="order", severity="info",
            title="Payment proof uploaded",
            message=f"{order['business_name']} uploaded payment proof for order #{order_id}.",
            target_url="/portal/team-leader/orders", source_type="orders", source_id=order_id,
            dedupe_key=f"order-proof-{order_id}-{existing + inserted}",
        )
    return inserted


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
    """
    where = []
    params: list[object] = []
    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)
    if status:
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
               COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
               ), 0) AS available
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
               COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
               ), 0) AS available,
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
                 p.name, p.category, p.description, p.unit, p.base_price,
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


def _reseller_available_product(cur, product_id: int) -> dict | None:
    cur.execute("""
        SELECT p.item_id AS product_id, p.name, p.is_active,
               COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND pb.expiry_date >= CURRENT_DATE
               ), 0) AS available
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
            "Stock is not reserved until fulfillment."
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


def create_order_from_items(
    role: str,
    items: list[tuple[int, object]],
    notes: str = "",
    account_id: int | None = None,
    *,
    checkout_cart: bool = False,
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

    order_type = "reseller" if role == "reseller" else "walk_in"
    reseller_id = None
    created_by_name = "MEATTRACK"
    team_leader_account_id = None
    creator_id = account_id
    status = "pending"

    if role == "reseller":
        if account_id is None:
            raise ValueError("Your session expired. Please sign in again.")
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

        if checkout_cart:
            if role != "reseller" or account_id is None:
                raise ValueError("Cart checkout requires a signed-in reseller.")
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
            quantities = {
                int(row["product_id"]): measurements.parse_whole_packs(row["quantity"])
                for row in locked_cart
            }

        cur.execute("""
            SELECT p.item_id AS product_id, p.name, p.unit, p.base_price, p.is_active,
                   COALESCE((
                       SELECT SUM(pb.quantity_available)
                       FROM inventory_batches pb
                       WHERE pb.item_id = p.item_id
                         AND pb.quality_status = 'approved'
                         AND pb.expiry_date >= CURRENT_DATE
                   ), 0) AS available
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
            total += line_total
            lines.append((product_id, quantity, product["unit"], product["base_price"]))

        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, team_leader_account_id,
                                approved_by_account_id, approved_at, status, order_date, fulfilled_at, total_amount, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING order_id, order_type, reseller_id, status, order_date, total_amount, notes;
        """, (
            order_type, reseller_id, creator_id, team_leader_account_id,
            creator_id if role != "reseller" else None,
            business_now() if role != "reseller" else None,
            status, business_now(), None,
            total.quantize(Decimal("0.01")), notes
        ))
        ord_res = dict(cur.fetchone())

        order_id = ord_res["order_id"]
        for product_id, quantity, unit, unit_price in lines:
            cur.execute("""
                INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
                VALUES (%s, %s, %s, %s, %s);
            """, (order_id, product_id, quantity, unit, unit_price))

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
        if role == "reseller":
            _create_notification_cursor(
                cur, recipient_account_id=team_leader_account_id, category="order", severity="warning",
                title="Pending reseller order", message=f"Order #{order_id} from {created_by_name} needs review.",
                target_url="/portal/team-leader/orders", source_type="orders", source_id=order_id,
                dedupe_key=f"reseller-order-{order_id}",
            )
        if checkout_cart:
            cur.execute("DELETE FROM reseller_cart_items WHERE account_id = %s;", (account_id,))

    return clean_row(ord_res)


def create_order(role: str, product_id: int, quantity: object, notes: str = "", account_id: int | None = None) -> dict:
    return create_order_from_items(role, [(product_id, quantity)], notes, account_id=account_id)


def checkout_reseller_cart(account_id: int, notes: str = "") -> dict:
    return create_order_from_items("reseller", [], notes, account_id=account_id, checkout_cart=True)


def decide_order(order_id: int, decision: str, team_leader_account_id: int | None = None) -> bool:
    if decision not in {"approve", "reject", "fulfill"}:
        return False

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
        ord_res = dict(cur.fetchone())
        if not ord_res:
            return False

        leader_id = team_leader_account_id
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
            if ord_res["status"] not in {"pending", "approved"}:
                return False
            cur.execute("""
                UPDATE orders
                SET status = 'rejected', approved_by_account_id = COALESCE(approved_by_account_id, %s),
                    approved_at = COALESCE(approved_at, %s)
                WHERE order_id = %s;
            """, (leader_id, business_now(), order_id))
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
            allocations = _plan_fefo_allocations(cur, quantities)
            _apply_fefo_allocations(
                cur,
                allocations,
                order_id=order_id,
                actor_account_id=leader_id,
                actor_name=actor_name,
            )
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
        notification = {
            "approve": ("info", "Order approved", f"Your reseller order #{order_id} was approved."),
            "reject": ("critical", "Order rejected", f"Your reseller order #{order_id} was rejected."),
            "fulfill": ("info", "Order fulfilled", f"Your reseller order #{order_id} was marked fulfilled."),
        }[decision]
        _create_notification_cursor(
            cur, recipient_account_id=ord_res["created_by_account_id"], category="order",
            severity=notification[0], title=notification[1], message=notification[2],
            target_url="/portal/reseller/history", source_type="orders", source_id=order_id,
            dedupe_key=f"order-{decision}-{order_id}",
        )
    return True


__all__ = ['list_orders', 'add_order_payment_proofs', 'get_order_payment_proof', 'reseller_most_bought_products', 'reseller_sales_series', 'count_orders', 'product_by_id', 'list_reseller_cart_items', 'reseller_cart_count', '_reseller_available_product', '_require_reseller_quantity_available', 'add_reseller_cart_item', 'update_reseller_cart_item', 'remove_reseller_cart_item', 'clear_reseller_cart', 'create_order_from_items', 'create_order', 'checkout_reseller_cart', 'decide_order']
