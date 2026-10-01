from __future__ import annotations


from ._shared import *
from .notifications import _create_notification_cursor


def inventory_item_filter(category: str) -> tuple[str, str]:
    raw = (category or "").strip()
    if ":" in raw:
        item_type, item_category = raw.split(":", 1)
        if item_type in {"raw_material", "finished_product"}:
            return item_type, item_category.strip().replace("_", " ")
    if raw in {"raw_material", "finished_product"}:
        return raw, ""
    return "", raw.replace("_", " ")


def list_inventory_items(q: str = "", category: str = "", page: int | None = None, page_size: int = 10, sort: str = "") -> list[dict]:
    where = ["ii.item_type IN ('raw_material', 'finished_product')"]
    params: list[object] = []
    q = q.strip()
    item_type, item_category = inventory_item_filter(category)
    if q:
        where.append("(ii.name ILIKE %s OR ii.category ILIKE %s OR ii.unit ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if item_type:
        where.append("ii.item_type = %s")
        params.append(item_type)
    if item_category:
        where.append("LOWER(ii.category) = LOWER(%s)")
        params.append(item_category)
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, INVENTORY_ITEM_SORTS, "CASE ii.item_type WHEN 'raw_material' THEN 1 ELSE 2 END, ii.category, ii.name")
    return clean_row(fetch_all("""
        SELECT ii.item_id, ii.item_type,
               CASE ii.item_type
                   WHEN 'raw_material' THEN 'Raw material'
                   WHEN 'finished_product' THEN 'Finished product'
               END AS item_type_label,
               ii.category, ii.name, ii.unit, ii.base_price,
               ii.pack_size, ii.pack_size_unit, ii.pack_content_status, ii.is_active,
               CASE
                   WHEN ii.item_type = 'raw_material' THEN ii.quantity_available
                   ELSE COALESCE(SUM(ib.quantity_available) FILTER (
                       WHERE ib.quality_status = 'approved'
                         AND ib.expiry_date >= CURRENT_DATE
                   ), 0)
               END AS available
        FROM inventory_items ii
        LEFT JOIN inventory_batches ib ON ib.item_id = ii.item_id
        WHERE {where_sql}
        GROUP BY ii.item_id, ii.item_type, ii.category, ii.name, ii.unit,
                 ii.base_price, ii.pack_size, ii.pack_size_unit, ii.pack_content_status,
                 ii.quantity_available, ii.is_active
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=" AND ".join(where), order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


def count_inventory_items(q: str = "", category: str = "") -> int:
    where = ["ii.item_type IN ('raw_material', 'finished_product')"]
    params: list[object] = []
    q = q.strip()
    item_type, item_category = inventory_item_filter(category)
    if q:
        where.append("(ii.name ILIKE %s OR ii.category ILIKE %s OR ii.unit ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if item_type:
        where.append("ii.item_type = %s")
        params.append(item_type)
    if item_category:
        where.append("LOWER(ii.category) = LOWER(%s)")
        params.append(item_category)
    row = fetch_one(f"""
        SELECT COUNT(*) AS total
        FROM inventory_items ii
        WHERE {" AND ".join(where)};
    """, tuple(params) or None)
    return int(row["total"])


def list_inventory_batches(q: str = "", category: str = "", page: int | None = None, page_size: int = 10, sort: str = "") -> list[dict]:
    where = ["ii.item_type = 'finished_product'"]
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        where.append("(ib.batch_code ILIKE %s OR ii.name ILIKE %s OR ib.source_type ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if category:
        where.append("ii.category = %s")
        params.append(category)
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, BATCH_SORTS, BATCH_SORTS["newest"])
    return clean_row(fetch_all("""
        SELECT ib.batch_id, ib.item_id, ii.item_type,
               CASE ii.item_type
                   WHEN 'raw_material' THEN 'Raw material'
                   WHEN 'finished_product' THEN 'Finished product'
               END AS item_type_label,
               ii.name AS item_name, ii.category, ib.batch_code, ib.source_type,
               ib.quantity_received, ib.quantity_available, ib.unit,
               ib.received_date, ib.expiry_date, ib.quality_status
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE {where_sql}
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=" AND ".join(where), order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


def count_inventory_batches(q: str = "", category: str = "") -> int:
    where = ["ii.item_type = 'finished_product'"]
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        where.append("(ib.batch_code ILIKE %s OR ii.name ILIKE %s OR ib.source_type ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if category:
        where.append("ii.category = %s")
        params.append(category)
    row = fetch_one(f"""
        SELECT COUNT(*) AS total
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE {" AND ".join(where)};
    """, tuple(params) or None)
    return int(row["total"])


def list_products(
    q: str = "",
    category: str = "",
    page: int | None = None,
    page_size: int = 12,
    sort: str = "",
    active_only: bool = False,
) -> list[dict]:
    where_extra = []
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        where_extra.append("(p.name ILIKE %s OR p.description ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        where_extra.append("p.category = %s")
        params.append(category)
    if active_only:
        where_extra.append("p.is_active = true")
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, PRODUCT_SORTS, "p.item_id")
    return clean_row(fetch_all("""
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
        WHERE p.item_type = 'finished_product'
          {where_extra}
        GROUP BY p.item_id, p.name, p.description, p.unit, p.base_price, p.is_active, p.category,
                 p.pack_size, p.pack_size_unit, p.pack_content_status
        ORDER BY {order_sql}
        {paging_sql};
    """.format(
        where_extra=("AND " + " AND ".join(where_extra)) if where_extra else "",
        order_sql=order_sql,
        paging_sql=paging_sql,
    ), tuple(params) or None))


def count_products(q: str = "", category: str = "", active_only: bool = False) -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM inventory_items p
        WHERE p.item_type = 'finished_product'
    """
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        query += " AND (p.name ILIKE %s OR p.description ILIKE %s)"
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        query += " AND p.category = %s"
        params.append(category)
    if active_only:
        query += " AND p.is_active = true"
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_raw_materials() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT item_id AS raw_material_id, category, name, unit,
               quantity_available AS available
        FROM inventory_items
        WHERE item_type = 'raw_material'
          AND is_active = true
        ORDER BY category, name;
    """))


def list_product_recipes(product_ids: list[int] | None = None) -> list[dict]:
    where = [
        "p.item_type = 'finished_product'",
        "rm.item_type = 'raw_material'",
    ]
    params: list[object] = []
    if product_ids is not None:
        scoped_product_ids = [int(product_id) for product_id in product_ids]
        if not scoped_product_ids:
            return []
        where.append("pr.product_item_id = ANY(%s)")
        params.append(scoped_product_ids)

    return clean_row(fetch_all(f"""
        SELECT pr.recipe_id, pr.product_item_id AS product_id, p.name AS product_name,
               pr.material_item_id AS raw_material_id, rm.name AS raw_material_name,
               rm.category AS raw_material_category,
               pr.quantity_required, pr.unit
        FROM product_recipes pr
        JOIN inventory_items p ON p.item_id = pr.product_item_id
        JOIN inventory_items rm ON rm.item_id = pr.material_item_id
        WHERE {" AND ".join(where)}
        ORDER BY p.name, rm.category, rm.name;
    """, tuple(params) or None))


def list_alerts() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT al.alert_id, al.alert_type, al.severity, al.message, al.status, al.triggered_at,
               (CASE
                    WHEN al.product_batch_id IS NOT NULL THEN (
                        SELECT p.name || ' batch ' || pb.batch_code
                        FROM inventory_batches pb
                        JOIN inventory_items p ON p.item_id = pb.item_id
                        WHERE pb.batch_id = al.product_batch_id
                    )
                    WHEN al.product_id IS NOT NULL THEN (SELECT name FROM inventory_items WHERE item_id = al.product_id)
                    WHEN al.raw_material_id IS NOT NULL THEN (SELECT name FROM inventory_items WHERE item_id = al.raw_material_id)
                    ELSE 'System Alert'
                END) AS subject
        FROM alerts al
        ORDER BY al.alert_id DESC;
    """))


def list_inventory_movements(
    q: str = "",
    page: int | None = None,
    page_size: int = 10,
    sort: str = "",
) -> list[dict]:
    params: list[object] = []
    where_sql = ""
    q = q.strip()
    if q:
        search = f"%{q}%"
        where_sql = """
            WHERE im.actor_name ILIKE %s
               OR im.movement_type ILIKE %s
               OR ii.name ILIKE %s
               OR COALESCE(ib.batch_code, '') ILIKE %s
               OR COALESCE(im.order_id::text, '') ILIKE %s
        """
        params.extend([search, search, search, search, search])
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, MOVEMENT_SORTS, MOVEMENT_SORTS["newest"])
    return clean_row(fetch_all(f"""
        SELECT im.movement_id,
               im.actor_name AS actor,
               im.movement_type AS action,
               ii.name AS entity,
               im.quantity_delta,
               im.unit,
               im.balance_before,
               im.balance_after,
               ib.batch_code,
               im.order_id,
               im.note,
               im.created_at
        FROM inventory_movements im
        JOIN inventory_items ii ON ii.item_id = im.item_id
        LEFT JOIN inventory_batches ib ON ib.batch_id = im.affected_batch_id
        {where_sql}
        ORDER BY {order_sql}
        {paging_sql};
    """, tuple(params) or None))


def count_inventory_movements(q: str = "") -> int:
    params: list[object] = []
    where_sql = ""
    q = q.strip()
    if q:
        search = f"%{q}%"
        where_sql = """
            WHERE im.actor_name ILIKE %s
               OR im.movement_type ILIKE %s
               OR ii.name ILIKE %s
               OR COALESCE(ib.batch_code, '') ILIKE %s
               OR COALESCE(im.order_id::text, '') ILIKE %s
        """
        params.extend([search, search, search, search, search])
    row = fetch_one(f"""
        SELECT COUNT(*) AS total
        FROM inventory_movements im
        JOIN inventory_items ii ON ii.item_id = im.item_id
        LEFT JOIN inventory_batches ib ON ib.batch_id = im.affected_batch_id
        {where_sql};
    """, tuple(params) or None)
    return int(row["total"])


def _inventory_actor_name(cur, actor_account_id: int | None) -> str:
    if actor_account_id is None:
        return "MEATTRACK"
    cur.execute("SELECT name FROM accounts WHERE account_id = %s LIMIT 1;", (actor_account_id,))
    actor = cur.fetchone()
    return actor["name"] if actor else "MEATTRACK"


def _insert_inventory_movement(
    cur,
    *,
    item_id: int,
    movement_type: str,
    quantity_delta: Decimal,
    unit: str,
    balance_before: Decimal,
    balance_after: Decimal,
    actor_account_id: int | None,
    actor_name: str,
    affected_batch_id: int | None = None,
    production_batch_id: int | None = None,
    order_id: int | None = None,
    note: str | None = None,
) -> None:
    cur.execute("""
        INSERT INTO inventory_movements (
            item_id, affected_batch_id, production_batch_id, order_id,
            movement_type, quantity_delta, unit, balance_before, balance_after,
            actor_account_id, actor_name, note
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (
        item_id, affected_batch_id, production_batch_id, order_id,
        movement_type, quantity_delta, unit, balance_before, balance_after,
        actor_account_id, actor_name, note,
    ))


def _plan_fefo_allocations(cur, quantities: dict[int, Decimal]) -> list[dict]:
    allocations: list[dict] = []
    for product_id in sorted(quantities):
        required = quantities[product_id]
        cur.execute("""
            SELECT ii.name, ii.unit
            FROM inventory_items ii
            WHERE ii.item_id = %s
              AND ii.item_type = 'finished_product'
              AND ii.is_active = true;
        """, (product_id,))
        product = cur.fetchone()
        if product is None:
            raise ValueError("Unknown product")

        cur.execute("""
            SELECT ib.batch_id, ib.quantity_available, ib.expiry_date
            FROM inventory_batches ib
            WHERE ib.item_id = %s
              AND ib.quality_status = 'approved'
              AND ib.quantity_available > 0
              AND ib.expiry_date >= CURRENT_DATE
            ORDER BY ib.expiry_date ASC, ib.batch_id ASC
            FOR UPDATE;
        """, (product_id,))
        batches = cur.fetchall()
        available = sum((Decimal(row["quantity_available"]) for row in batches), Decimal("0"))
        if available < required:
            raise ValueError(
                f"Not enough {product['name']}. Required {display_decimal(required)} packs, "
                f"available {display_decimal(available)} packs."
            )

        remaining = required
        for batch in batches:
            if remaining == 0:
                break
            before = Decimal(batch["quantity_available"])
            take = min(before, remaining)
            allocations.append({
                "product_id": product_id,
                "product_name": product["name"],
                "unit": product["unit"],
                "batch_id": batch["batch_id"],
                "before": before,
                "take": take,
                "after": before - take,
            })
            remaining -= take
    return allocations


def _apply_fefo_allocations(
    cur,
    allocations: list[dict],
    *,
    order_id: int,
    actor_account_id: int | None,
    actor_name: str,
) -> None:
    for allocation in allocations:
        cur.execute("""
            UPDATE inventory_batches
            SET quantity_available = %s
            WHERE batch_id = %s
              AND quantity_available = %s;
        """, (allocation["after"], allocation["batch_id"], allocation["before"]))
        if cur.rowcount != 1:
            raise RuntimeError("Inventory batch changed while it was locked.")
        _insert_inventory_movement(
            cur,
            item_id=allocation["product_id"],
            affected_batch_id=allocation["batch_id"],
            order_id=order_id,
            movement_type="sale_fulfillment",
            quantity_delta=-allocation["take"],
            unit=allocation["unit"],
            balance_before=allocation["before"],
            balance_after=allocation["after"],
            actor_account_id=actor_account_id,
            actor_name=actor_name,
            note=f"Fulfilled order #{order_id}",
        )


def add_raw_inventory_item(
    name: str,
    category: str,
    unit: str,
    quantity: object,
    actor_account_id: int | None = None,
) -> dict:
    item_name = normalize_inventory_name(name, "Item name")
    item_category = require_inventory_choice(category, raw_material_categories, "Category")
    item_unit = require_inventory_choice(unit, stock_units, "Unit")
    amount = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if amount <= 0:
        raise ValueError("Quantity must be greater than zero.")

    with get_transaction_cursor() as cur:
        actor_name = _inventory_actor_name(cur, actor_account_id)
        cur.execute("""
            SELECT item_id, name, unit
            FROM inventory_items
            WHERE item_type = 'raw_material'
              AND lower(name) = lower(%s)
            ORDER BY item_id
            FOR UPDATE;
        """, (item_name,))
        matches = cur.fetchall()

        if matches:
            existing = matches[0]
            mismatched = next(
                (item for item in matches if canonical_inventory_unit(item["unit"]) != canonical_inventory_unit(item_unit)),
                None,
            )
            if mismatched or canonical_inventory_unit(existing["unit"]) != canonical_inventory_unit(item_unit):
                raise ValueError(f"{existing['name']} is already tracked in {existing['unit']}. Use the same unit.")
            raise ValueError(f"{existing['name']} already exists. Use Add quantity instead.")

        cur.execute("""
            INSERT INTO inventory_items (item_type, name, category, unit, quantity_available, base_price, is_active)
            VALUES ('raw_material', %s, %s, %s, %s, 0, true)
            RETURNING item_id AS raw_material_id, category, name, unit, quantity_available AS available;
        """, (item_name, item_category, item_unit, amount))
        item = cur.fetchone()

        _insert_inventory_movement(
            cur,
            item_id=item["raw_material_id"],
            movement_type="raw_receipt",
            quantity_delta=amount,
            unit=item_unit,
            balance_before=Decimal("0.000"),
            balance_after=amount,
            actor_account_id=actor_account_id,
            actor_name=actor_name,
            note="Initial raw-material receipt",
        )
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="updated_raw_inventory", entity_type="inventory_items", entity_id=item["raw_material_id"])
    return clean_row(item)


def add_raw_inventory_quantity(
    raw_material_id: int,
    quantity: object,
    actor_account_id: int | None = None,
) -> dict:
    amount = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if amount <= 0:
        raise ValueError("Quantity must be greater than zero.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id, name, unit, quantity_available
            FROM inventory_items
            WHERE item_id = %s
              AND item_type = 'raw_material'
              AND is_active = true
            FOR UPDATE;
        """, (raw_material_id,))
        item = cur.fetchone()
        if item is None:
            raise ValueError("Choose a valid raw inventory item.")

        cur.execute("""
            UPDATE inventory_items
            SET quantity_available = quantity_available + %s
            WHERE item_id = %s
            RETURNING item_id AS raw_material_id, category, name, unit, quantity_available AS available;
        """, (amount, raw_material_id))
        updated_item = cur.fetchone()

        actor_name = _inventory_actor_name(cur, actor_account_id)
        before = Decimal(item["quantity_available"])
        after = Decimal(updated_item["available"])
        _insert_inventory_movement(
            cur,
            item_id=raw_material_id,
            movement_type="raw_receipt",
            quantity_delta=amount,
            unit=item["unit"],
            balance_before=before,
            balance_after=after,
            actor_account_id=actor_account_id,
            actor_name=actor_name,
            note="Raw-material quantity added",
        )
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="added_raw_inventory_quantity", entity_type="inventory_items", entity_id=raw_material_id)
    return clean_row(updated_item)


def create_product_with_recipe(
    name: str,
    category: str,
    base_price: object,
    material_item_ids: list[object],
    quantity_required: list[object],
    quantity_required_units: list[object],
    pack_size: object = "",
    pack_size_unit: str = "",
    actor_account_id: int | None = None,
) -> dict:
    product_name = normalize_inventory_name(name, "Product name")
    product_category = require_inventory_choice(category, product_categories, "Category")
    product_unit = "pack"
    price = parse_inventory_decimal(base_price or 0, "Base price", "0.01")
    if price < 0:
        raise ValueError("Base price cannot be negative.")

    if not str(pack_size).strip():
        raise ValueError("Contents per pack is required.")
    size = parse_inventory_decimal(pack_size, "Contents per pack", "0.001")
    if size <= 0:
        raise ValueError("Contents per pack must be greater than zero.")
    size_unit = require_inventory_choice(pack_size_unit, content_units, "Contents unit")
    product_description = f"{product_name} - {display_decimal(size)} {size_unit} per pack."

    if len(material_item_ids) != len(quantity_required) or len(material_item_ids) != len(quantity_required_units):
        raise ValueError("Ingredient rows are incomplete.")
    if not material_item_ids:
        raise ValueError("Add at least one ingredient.")

    requirements = []
    seen_material_ids = set()
    for material_item_id, required_quantity, required_unit in zip(material_item_ids, quantity_required, quantity_required_units):
        try:
            material_id = int(material_item_id)
        except (TypeError, ValueError):
            raise ValueError("Choose valid raw inventory items.")
        if material_id in seen_material_ids:
            raise ValueError("Each ingredient can only be selected once.")
        seen_material_ids.add(material_id)

        required = parse_inventory_decimal(required_quantity, "Ingredient quantity", "0.001")
        if required <= 0:
            raise ValueError("Ingredient quantity must be greater than zero.")
        requirements.append((material_id, required, require_inventory_choice(required_unit, recipe_units, "Ingredient unit")))

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id
            FROM inventory_items
            WHERE item_type = 'finished_product'
              AND lower(name) = lower(%s)
            LIMIT 1;
        """, (product_name,))
        if cur.fetchone() is not None:
            raise ValueError("Product already exists.")

        material_ids = [material_id for material_id, _, _ in requirements]
        cur.execute("""
            SELECT item_id, name, unit
            FROM inventory_items
            WHERE item_type = 'raw_material'
              AND is_active = true
              AND item_id = ANY(%s);
        """, (material_ids,))
        materials = {row["item_id"]: row for row in cur.fetchall()}
        if len(materials) != len(material_ids):
            raise ValueError("Choose valid raw inventory items.")

        cur.execute("""
            INSERT INTO inventory_items (
                item_type, name, category, description, unit, base_price,
                quantity_available, pack_size, pack_size_unit, pack_content_status, is_active
            )
            VALUES ('finished_product', %s, %s, %s, %s, %s, 0, %s, %s, 'declared', true)
            RETURNING item_id AS product_id, name, category, unit, base_price,
                      pack_size, pack_size_unit, pack_content_status;
        """, (
            product_name,
            product_category,
            product_description,
            product_unit,
            price,
            size,
            size_unit,
        ))
        product = cur.fetchone()

        for material_id, required, required_unit in requirements:
            material = materials[material_id]
            converted_required = convert_inventory_quantity(
                required,
                required_unit,
                material["unit"],
                material["name"],
            )
            cur.execute("""
                INSERT INTO product_recipes (product_item_id, material_item_id, quantity_required, unit)
                VALUES (%s, %s, %s, %s);
            """, (product["product_id"], material_id, converted_required, material["unit"]))

        _add_log_cursor(cur, actor_account_id=actor_account_id, action="created_product_recipe", entity_type="inventory_items", entity_id=product["product_id"])
    return clean_row(product)


def update_product_pack_content(
    product_id: int,
    pack_size: object,
    pack_size_unit: str,
    actor_account_id: int | None = None,
) -> dict:
    size = measurements.parse_decimal_exact(pack_size, "Contents per pack", scale=3, positive=True)
    size_unit = measurements.require_unit(pack_size_unit, measurements.CONTENT_UNITS, "Contents unit")
    with get_transaction_cursor() as cur:
        cur.execute("""
            UPDATE inventory_items
            SET pack_size = %s,
                pack_size_unit = %s,
                pack_content_status = 'declared'
            WHERE item_id = %s
              AND item_type = 'finished_product'
            RETURNING item_id AS product_id, name, pack_size, pack_size_unit, pack_content_status;
        """, (size, size_unit, product_id))
        product = cur.fetchone()
        if product is None:
            raise ValueError("Unknown product")
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="updated_product_pack_content", entity_type="inventory_items", entity_id=product_id)
    return clean_row(product)


def produce_product(
    product_id: int,
    batch_code: str,
    quantity: object,
    expiry_date: date,
    actor_account_id: int | None = None,
) -> dict:
    produced_quantity = measurements.parse_whole_packs(quantity, "Produced quantity")
    clean_batch_code = normalize_inventory_text(batch_code, "Batch code").upper()
    if len(clean_batch_code) < 4:
        raise ValueError("Batch code is too short.")
    if expiry_date < business_today():
        raise ValueError("Expiry date cannot be in the past.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id AS product_id, name, unit
            FROM inventory_items
            WHERE item_id = %s
              AND item_type = 'finished_product'
              AND is_active = true;
        """, (product_id,))
        product = cur.fetchone()
        if product is None:
            raise ValueError("Unknown product")

        cur.execute("SELECT batch_id FROM inventory_batches WHERE batch_code = %s;", (clean_batch_code,))
        if cur.fetchone() is not None:
            raise ValueError("Batch code already exists.")

        cur.execute("""
            SELECT pr.material_item_id AS raw_material_id, pr.quantity_required, pr.unit AS recipe_unit,
                   rm.name AS raw_material_name, rm.unit AS material_unit,
                   rm.quantity_available
            FROM product_recipes pr
            JOIN inventory_items rm ON rm.item_id = pr.material_item_id
            WHERE pr.product_item_id = %s
              AND rm.item_type = 'raw_material'
            ORDER BY pr.recipe_id
            FOR UPDATE OF rm;
        """, (product_id,))
        recipe_rows = cur.fetchall()
        if not recipe_rows:
            raise ValueError("No recipe is configured for this product.")

        requirements = []
        for row in recipe_rows:
            if row["recipe_unit"] != row["material_unit"]:
                raise ValueError(f"{row['raw_material_name']} recipe unit must match its stock unit.")
            required = (Decimal(row["quantity_required"]) * produced_quantity).quantize(Decimal("0.001"))
            available = Decimal(row["quantity_available"])
            if required > available:
                raise ValueError(
                    f"Not enough {row['raw_material_name']}. "
                    f"Required {required:g} {row['material_unit']}, "
                    f"available {available:g} {row['material_unit']}."
                )
            requirements.append({
                "raw_material_id": row["raw_material_id"],
                "name": row["raw_material_name"],
                "unit": row["material_unit"],
                "required": required,
                "before": available,
                "after": available - required,
            })

        cur.execute("""
            INSERT INTO inventory_batches (item_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status)
            VALUES (%s, %s, 'production', %s, %s, %s, %s, %s, 'approved')
            RETURNING batch_id AS product_batch_id, item_id AS product_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status;
        """, (product_id, clean_batch_code, produced_quantity, produced_quantity, product["unit"], business_today(), expiry_date))
        batch = cur.fetchone()
        actor_name = _inventory_actor_name(cur, actor_account_id)

        for requirement in requirements:
            cur.execute("""
                UPDATE inventory_items
                SET quantity_available = %s
                WHERE item_id = %s
                  AND quantity_available = %s;
            """, (requirement["after"], requirement["raw_material_id"], requirement["before"]))
            if cur.rowcount != 1:
                raise RuntimeError("Raw-material stock changed while it was locked.")
            _insert_inventory_movement(
                cur,
                item_id=requirement["raw_material_id"],
                production_batch_id=batch["product_batch_id"],
                movement_type="production_consumption",
                quantity_delta=-requirement["required"],
                unit=requirement["unit"],
                balance_before=requirement["before"],
                balance_after=requirement["after"],
                actor_account_id=actor_account_id,
                actor_name=actor_name,
                note=f"Consumed for production batch {clean_batch_code}",
            )

        _insert_inventory_movement(
            cur,
            item_id=product_id,
            affected_batch_id=batch["product_batch_id"],
            production_batch_id=batch["product_batch_id"],
            movement_type="production_output",
            quantity_delta=produced_quantity,
            unit=product["unit"],
            balance_before=Decimal("0.000"),
            balance_after=produced_quantity,
            actor_account_id=actor_account_id,
            actor_name=actor_name,
            note=f"Produced batch {clean_batch_code}",
        )

        days = (expiry_date - business_today()).days
        if days <= 7:
            cur.execute("""
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message, status, triggered_at)
                VALUES ('near_expiry', %s, %s, %s, %s, 'open', %s);
            """, (
                'warning' if days > 2 else 'critical',
                product_id, batch["product_batch_id"],
                f"{produced_quantity:g} {product['unit']} expires in {days} day(s).",
                business_now()
            ))
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="produced_product_batch", entity_type="inventory_batches", entity_id=batch["product_batch_id"])
        if days <= 7:
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory",
                severity="critical" if days <= 2 else "warning", title="Batch expiry warning",
                message=f"{product['name']} batch {clean_batch_code} expires in {days} day(s).",
                target_url="/portal/team-leader/inventory", source_type="inventory_batches",
                source_id=batch["product_batch_id"], dedupe_key=f"batch-expiry-{batch['product_batch_id']}",
            )
    return clean_row(batch)


def add_product_batch(
    product_id: int,
    batch_code: str,
    quantity: object,
    expiry_date: date,
    source_type: str = "direct_received",
    actor_account_id: int | None = None,
) -> dict:
    clean_quantity = measurements.parse_whole_packs(quantity)
    clean_batch_code = normalize_inventory_text(batch_code, "Batch code").upper()
    if source_type != "direct_received":
        raise ValueError("Direct batch registration must use the direct_received source type.")
    if expiry_date < business_today():
        raise ValueError("Expiry date cannot be in the past.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id AS product_id, name, unit
            FROM inventory_items
            WHERE item_id = %s AND item_type = 'finished_product' AND is_active = true
            FOR UPDATE;
        """, (product_id,))
        product = cur.fetchone()
        if product is None:
            raise ValueError("Unknown product")
        actor_name = _inventory_actor_name(cur, actor_account_id)
        cur.execute("""
            INSERT INTO inventory_batches (item_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status)
            VALUES (%s, %s, 'direct_received', %s, %s, %s, %s, %s, 'approved')
            RETURNING batch_id AS product_batch_id, item_id AS product_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status;
        """, (product_id, clean_batch_code, clean_quantity, clean_quantity, product["unit"], business_today(), expiry_date))
        batch = cur.fetchone()
        _insert_inventory_movement(
            cur,
            item_id=product_id,
            affected_batch_id=batch["product_batch_id"],
            movement_type="direct_finished_receipt",
            quantity_delta=clean_quantity,
            unit=product["unit"],
            balance_before=Decimal("0.000"),
            balance_after=clean_quantity,
            actor_account_id=actor_account_id,
            actor_name=actor_name,
            note=f"Direct receipt batch {clean_batch_code}",
        )

        days = (expiry_date - business_today()).days
        if days <= 7:
            cur.execute("""
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message, status, triggered_at)
                VALUES ('near_expiry', %s, %s, %s, %s, 'open', %s);
            """, (
                'warning' if days > 2 else 'critical',
                product_id, batch["product_batch_id"],
                f"{clean_quantity:g} {product['unit']} expires in {days} day(s).",
                business_now(),
            ))
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="registered_product_batch", entity_type="inventory_batches", entity_id=batch["product_batch_id"])
        if days <= 7:
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory",
                severity="critical" if days <= 2 else "warning", title="Batch expiry warning",
                message=f"{product['name']} batch {clean_batch_code} expires in {days} day(s).",
                target_url="/portal/team-leader/inventory", source_type="inventory_batches",
                source_id=batch["product_batch_id"], dedupe_key=f"batch-expiry-{batch['product_batch_id']}",
            )
    return clean_row(batch)


def update_product_price(product_id: int, base_price: object, actor_account_id: int | None = None) -> None:
    clean_price = parse_money(base_price, "Base price")
    with get_transaction_cursor() as cur:
        cur.execute("""
            UPDATE inventory_items SET base_price = %s
            WHERE item_id = %s AND item_type = 'finished_product'
            RETURNING item_id;
        """, (clean_price, product_id))
        if not cur.fetchone():
            raise ValueError("Unknown product.")
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="updated_product_price", entity_type="inventory_items", entity_id=product_id)


__all__ = ['inventory_item_filter', 'list_inventory_items', 'count_inventory_items', 'list_inventory_batches', 'count_inventory_batches', 'list_products', 'count_products', 'list_raw_materials', 'list_product_recipes', 'list_alerts', 'list_inventory_movements', 'count_inventory_movements', '_inventory_actor_name', '_insert_inventory_movement', '_plan_fefo_allocations', '_apply_fefo_allocations', 'add_raw_inventory_item', 'add_raw_inventory_quantity', 'create_product_with_recipe', 'update_product_pack_content', 'produce_product', 'add_product_batch', 'update_product_price']
