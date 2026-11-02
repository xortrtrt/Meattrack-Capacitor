"""Scoped conversation counts and read cursors, independent of page pagination."""

from app.database import fetch_all, fetch_one, get_transaction_cursor


def _legacy_unread_summary(account_id: int, role_key: str) -> dict:
    if role_key in {"team-leader", "sales-supervisor"}:
        rows = fetch_all(
            """SELECT c.reseller_account_id,COUNT(*) unread_count FROM reseller_conversations c
            JOIN reseller_messages m USING(conversation_id)
            JOIN accounts a ON a.account_id=c.reseller_account_id AND a.is_active
            JOIN resellers r ON (r.reseller_id=a.reseller_id OR (a.reseller_id IS NULL AND lower(r.email)=lower(a.email))) AND r.reseller_status='active'
            WHERE c.team_leader_account_id=%s AND m.sender_account_id<>%s AND m.read_at IS NULL
              AND (c.team_leader_deleted_at IS NULL OR m.created_at>c.team_leader_deleted_at)
            GROUP BY c.reseller_account_id""",
            (account_id, account_id),
        )
        return {"unread_conversations": len(rows), "threads": rows}
    if role_key != "reseller":
        return {"unread_conversations": 0, "threads": []}
    sales = fetch_one(
        """SELECT COUNT(*) unread_count,MAX(c.team_leader_account_id) peer_account_id FROM reseller_conversations c
        JOIN reseller_messages m USING(conversation_id)
        JOIN accounts a ON a.account_id=c.reseller_account_id
        JOIN resellers r ON (r.reseller_id=a.reseller_id OR (a.reseller_id IS NULL AND lower(r.email)=lower(a.email))) AND r.team_leader_account_id=c.team_leader_account_id
        WHERE c.reseller_account_id=%s AND m.sender_account_id<>%s AND m.read_at IS NULL
          AND (c.reseller_deleted_at IS NULL OR m.created_at>c.reseller_deleted_at)""",
        (account_id, account_id),
    )
    orders = fetch_all(
        """SELECT order_id,SUM(unread_count)::integer unread_count FROM (
        SELECT order_id,COUNT(*) unread_count FROM order_system_messages
        WHERE reseller_account_id=%s AND read_at IS NULL GROUP BY order_id
        UNION ALL
        SELECT f.order_id,COUNT(*) FROM order_refund_messages m JOIN order_refunds f USING(order_refund_id)
        JOIN orders o ON o.order_id=f.order_id
        LEFT JOIN refund_message_reads r ON r.order_refund_id=f.order_refund_id AND r.account_id=%s
        WHERE o.created_by_account_id=%s AND m.sender_account_id<>%s
          AND m.order_refund_message_id>COALESCE(r.last_message_id,0) GROUP BY f.order_id
    ) unread GROUP BY order_id""",
        (account_id, account_id, account_id, account_id),
    )
    return {
        "unread_conversations": len(orders) + int(sales["unread_count"] > 0),
        "sales_unread_count": int(sales["unread_count"]),
        "sales_peer_account_id": sales["peer_account_id"],
        "orders": orders,
    }


def unread_summary(account_id: int, role_key: str) -> dict:
    result = _legacy_unread_summary(account_id,role_key)
    if role_key in {"owner","sales-supervisor","reseller"}:
        from app.repositories.replacement_messages import threads
        incoming = [row for row in threads(account_id) if row["unread_count"]>0]
        if incoming:
            result["replacement_threads"] = incoming
            if role_key == "reseller":
                peers = {row["processor_account_id"] for row in incoming}
                overlap = int(result.get("sales_unread_count", 0) > 0 and result.get("sales_peer_account_id") in peers)
                result["unread_conversations"] += len(peers) - overlap
            else:
                result["unread_conversations"] += len(incoming)
    return result


def mark_refund_read(refund_id: int, account_id: int, role_key: str, through_id: int) -> None:
    from app.repositories.refunds import list_order_refund_messages

    # Authorize and cap the cursor to a real message in this conversation.
    list_order_refund_messages(refund_id, account_id, role_key, after_id=max(0, through_id - 1))
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM order_refund_messages WHERE order_refund_id=%s AND order_refund_message_id=%s",
            (refund_id, through_id),
        )
        if not cur.fetchone():
            return
        cur.execute(
            """INSERT INTO refund_message_reads(order_refund_id,account_id,last_message_id)
            VALUES(%s,%s,%s) ON CONFLICT(order_refund_id,account_id) DO UPDATE
            SET last_message_id=GREATEST(refund_message_reads.last_message_id,EXCLUDED.last_message_id),read_at=now()""",
            (refund_id, account_id, through_id),
        )
