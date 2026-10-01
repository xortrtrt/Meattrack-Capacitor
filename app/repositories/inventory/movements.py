from __future__ import annotations

from decimal import Decimal


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



