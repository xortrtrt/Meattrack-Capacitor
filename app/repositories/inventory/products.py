from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import wraps
from psycopg2.errors import UniqueViolation

from app import inventory_measurements as measurements
from app.business_time import business_now, business_today
from .._shared import (
    _add_log_cursor,
    clean_row,
    content_units,
    convert_inventory_quantity,
    display_decimal,
    get_transaction_cursor,
    normalize_inventory_name,
    normalize_inventory_text,
    parse_inventory_decimal,
    parse_money,
    product_categories,
    recipe_units,
    require_inventory_choice,
)
from ..notifications import _create_notification_cursor
from .movements import _insert_inventory_movement, _inventory_actor_name


def _batch_code_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except UniqueViolation as exc:
            if exc.diag.constraint_name == "inventory_batches_batch_code_key":
                raise ValueError("This batch code already exists.") from exc
            raise
    return wrapped


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


@_batch_code_errors
def produce_product(
    product_id: int,
    batch_code: str,
    quantity: object,
    expiry_date: date,
    actor_account_id: int | None = None,
) -> dict:
    from ..superuser import business_setting

    near_expiry_days = int(business_setting("inventory.near_expiry_days"))
    critical_expiry_days = int(business_setting("inventory.critical_expiry_days"))
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
            raise ValueError("This batch code already exists.")

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
        if days <= near_expiry_days:
            cur.execute("""
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message, status, triggered_at)
                VALUES ('near_expiry', %s, %s, %s, %s, 'open', %s);
            """, (
                'warning' if days > critical_expiry_days else 'critical',
                product_id, batch["product_batch_id"],
                f"{produced_quantity:g} {product['unit']} expires in {days} day(s).",
                business_now()
            ))
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="produced_product_batch", entity_type="inventory_batches", entity_id=batch["product_batch_id"])
        if days <= near_expiry_days:
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory",
                severity="critical" if days <= critical_expiry_days else "warning", title="Batch expiry warning",
                message=f"{product['name']} batch {clean_batch_code} expires in {days} day(s).",
                target_url="/portal/team-leader/inventory", source_type="inventory_batches",
                source_id=batch["product_batch_id"], dedupe_key=f"batch-expiry-{batch['product_batch_id']}",
            )
    return clean_row(batch)


@_batch_code_errors
def add_product_batch(
    product_id: int,
    batch_code: str,
    quantity: object,
    expiry_date: date,
    source_type: str = "direct_received",
    actor_account_id: int | None = None,
) -> dict:
    from ..superuser import business_setting

    near_expiry_days = int(business_setting("inventory.near_expiry_days"))
    critical_expiry_days = int(business_setting("inventory.critical_expiry_days"))
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
        if days <= near_expiry_days:
            cur.execute("""
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message, status, triggered_at)
                VALUES ('near_expiry', %s, %s, %s, %s, 'open', %s);
            """, (
                'warning' if days > critical_expiry_days else 'critical',
                product_id, batch["product_batch_id"],
                f"{clean_quantity:g} {product['unit']} expires in {days} day(s).",
                business_now(),
            ))
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="registered_product_batch", entity_type="inventory_batches", entity_id=batch["product_batch_id"])
        if days <= near_expiry_days:
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory",
                severity="critical" if days <= critical_expiry_days else "warning", title="Batch expiry warning",
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

