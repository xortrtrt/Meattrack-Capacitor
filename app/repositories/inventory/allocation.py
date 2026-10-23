from __future__ import annotations

from decimal import Decimal

from .._shared import display_decimal
from .movements import _insert_inventory_movement


def _plan_fefo_allocations(
    cur,
    quantities: dict[int, Decimal],
    *,
    reservation_order_id: int | None = None,
    reservation_return_id: int | None = None,
) -> list[dict]:
    allocations: list[dict] = []
    for product_id in sorted(quantities):
        required = quantities[product_id]
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
            (f"inventory-product:{product_id}",),
        )
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
            SELECT ib.batch_id, ib.quantity_available, ib.expiry_date,
                   ib.quantity_available - COALESCE((
                       SELECT SUM(r.quantity) FROM replacement_stock_reservations r
                       WHERE r.batch_id = ib.batch_id AND r.return_id IS DISTINCT FROM %s
                   ), 0) AS free_quantity
            FROM inventory_batches ib
            WHERE ib.item_id = %s
              AND ib.quality_status = 'approved'
              AND ib.quantity_available > 0
              AND ib.expiry_date > (now() AT TIME ZONE 'Asia/Manila')::date
            ORDER BY ib.expiry_date ASC, ib.batch_id ASC
            FOR UPDATE;
        """, (reservation_return_id, product_id))
        batches = cur.fetchall()
        if reservation_order_id is None:
            cur.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS reserved FROM order_stock_reservations WHERE product_id = %s;",
                (product_id,),
            )
        else:
            cur.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS reserved
                FROM order_stock_reservations
                WHERE product_id = %s AND order_id <> %s;
                """,
                (product_id, reservation_order_id),
            )
        reserved = Decimal(cur.fetchone()["reserved"])
        available = sum((max(Decimal(row["free_quantity"]), Decimal("0")) for row in batches), Decimal("0")) - reserved
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
            take = min(max(Decimal(batch["free_quantity"]), Decimal("0")), remaining)
            if take == 0:
                continue
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
        cur.execute("""
            SELECT order_item_id, quantity FROM order_items
            WHERE order_id = %s AND product_id = %s ORDER BY order_item_id;
        """, (order_id, allocation["product_id"]))
        remaining = allocation["take"]
        for line in cur.fetchall():
            cur.execute("SELECT COALESCE(SUM(quantity),0) AS total FROM fulfillment_allocations WHERE order_item_id = %s;", (line["order_item_id"],))
            take = min(remaining, Decimal(line["quantity"]) - Decimal(cur.fetchone()["total"]))
            if take > 0:
                cur.execute("""
                    INSERT INTO fulfillment_allocations(order_item_id,batch_id,quantity)
                    VALUES (%s,%s,%s) ON CONFLICT(order_item_id,batch_id)
                    DO UPDATE SET quantity = fulfillment_allocations.quantity + EXCLUDED.quantity;
                """, (line["order_item_id"], allocation["batch_id"], take))
                remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise ValueError("Fulfillment quantities do not match the order lines.")
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



