from __future__ import annotations

from decimal import Decimal

from .._shared import display_decimal
from .movements import _insert_inventory_movement


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



