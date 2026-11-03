"""Participant-grouped presentation without copying append-only case messages."""

from app import reseller_messaging
from app.database import get_transaction_cursor
from app.repositories import replacement_messages, product_returns


def workspace(actor_id: int, return_id: int, after_id: int = 0, direct_after_id: int = 0) -> dict:
    rows = replacement_messages.threads(actor_id)
    selected = next((row for row in rows if row["return_id"] == return_id), None)
    if not selected:
        raise ValueError("This conversation is unavailable.")
    related = [row for row in rows if row["processor_account_id"] == selected["processor_account_id"]]
    # Recheck every case, independently of the list projection and caller's selector.
    with get_transaction_cursor() as cur:
        for row in related:
            replacement_messages.participant(cur, row["return_id"], actor_id)
        cur.execute(
            """SELECT m.message_id,m.sender_account_id,COALESCE(a.name,'MEATTRACK') sender_name,
            m.content,m.created_at,m.return_id,r.order_id FROM return_conversation_messages m
            JOIN product_returns r ON r.return_id=m.return_id
            LEFT JOIN accounts a ON a.account_id=m.sender_account_id
            WHERE m.return_id=ANY(%s) AND m.message_id>%s ORDER BY m.message_id LIMIT 200""",
            ([row["return_id"] for row in related], max(0, after_id)),
        )
        messages = [dict(row) for row in cur.fetchall()]
    direct: dict | None = reseller_messaging.reseller_workspace(actor_id, after_id=max(0, direct_after_id))
    if not selected["processor_account_id"] or direct is None or direct["participant"].get("team_leader_account_id") != selected["processor_account_id"]:
        direct = None
    case = product_returns.get_product_return(return_id, actor_id)
    return {
        "threads": related, "messages": messages, "direct": direct,
        "orders": orders(actor_id) if direct else [],
        "context": {key: case.get(key) for key in (
            "return_id", "order_id", "status", "resolution", "credit", "replacement_total", "replacement_workflow_version", "additional_due", "unused_credit",
            "refund_converted_at", "refund_due", "payment_verified_at", "payment_amount", "verified_at",
        )},
    }


def orders(actor_id: int) -> list[dict]:
    with get_transaction_cursor() as cur:
        cur.execute("SELECT order_id FROM orders WHERE created_by_account_id=%s ORDER BY order_id DESC", (actor_id,))
        return [dict(row) for row in cur.fetchall()]


def order_card(actor_id: int, order_id: int) -> dict:
    """Return only public order context after checking the reseller's ownership."""
    with get_transaction_cursor() as cur:
        cur.execute(
            """SELECT o.order_id,o.status,o.order_date,o.total_amount,o.fulfilled_at,
            (SELECT received_at FROM reseller_order_receipts WHERE order_id=o.order_id) AS received_at
            FROM orders o WHERE o.order_id=%s AND o.created_by_account_id=%s""",
            (order_id, actor_id),
        )
        order = cur.fetchone()
        if not order:
            raise ValueError("Order unavailable.")
        result = dict(order)
        cur.execute(
            """SELECT p.name,oi.quantity FROM order_items oi
            JOIN inventory_items p ON p.item_id=oi.product_id
            WHERE oi.order_id=%s ORDER BY oi.order_item_id""", (order_id,),
        )
        result["items"] = [dict(row) for row in cur.fetchall()]
        result["details_url"] = f"/portal/reseller/history?view=all&q={order_id}&order={order_id}#order-{order_id}"
        return result
