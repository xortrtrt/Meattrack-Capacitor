from __future__ import annotations

from decimal import Decimal
from app.inventory_measurements import require_capacity

from .._shared import (
    _add_log_cursor,
    canonical_inventory_unit,
    clean_row,
    get_transaction_cursor,
    normalize_inventory_name,
    parse_inventory_decimal,
    raw_material_categories,
    require_inventory_choice,
    stock_units,
)
from .movements import _insert_inventory_movement, _inventory_actor_name


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

        require_capacity(Decimal(item["quantity_available"]) + amount, "Resulting material stock")

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



