"""Small, role-scoped operation summaries; financial totals remain in settlement_summary."""

from app.database import clean_row, fetch_all, fetch_one
from app.conversation_notifications import unread_summary


def leader_work_summary(account_id: int, leader_role: str) -> dict:
    if leader_role == "inventory":
        from .superuser import business_setting
        threshold = int(business_setting("inventory.low_stock_packs"))
        counts = fetch_one("""SELECT
            (SELECT COUNT(*) FROM finished_stock_availability WHERE is_active AND available<=%s) low_stock,
            (SELECT COUNT(*) FROM expired_pullouts p JOIN inventory_batches b USING(batch_id) WHERE b.quantity_available>0 AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date) pending_pullouts,
            (SELECT COUNT(*) FROM product_returns WHERE status IN ('awaiting_receipt','awaiting_inventory_verification')) return_receipts,
            (SELECT COUNT(*) FROM product_returns r WHERE resolution='replace' AND approved_by_account_id IS NOT NULL
             AND status='settlement_pending' AND verified_at IS NOT NULL AND settlement_authorized_at IS NOT NULL
             AND settlement_authorized_quote_revision=quote_revision AND accepted_revision=quote_revision
             AND replacement_released_at IS NULL AND (NOT EXISTS(SELECT 1 FROM replacement_stock_reservations s WHERE s.return_id=r.return_id)
                 OR replacement_total<=credit OR payment_verified_at IS NOT NULL)) replacements,
            COALESCE((SELECT SUM(quantity) FROM all_stock_reservations),0) reserved,
            COALESCE((SELECT SUM(b.quantity_available) FROM inventory_batches b JOIN inventory_items i ON i.item_id=b.item_id
             WHERE i.item_type='finished_product'),0) physical_stock""", (threshold,))
        rows = [
            (
                "Return receipts",
                counts["return_receipts"],
                "returns",
                "Check received goods and supporting evidence",
                "package-check",
            ),
            (
                "Pending pull-outs",
                counts["pending_pullouts"],
                "expired-stock",
                "Expired stock awaiting physical collection",
                "clock-alert",
            ),
            (
                "Replacement work",
                counts["replacements"],
                "returns",
                "Follow cleared stock reservations and releases",
                "refresh-cw",
            ),
            (
                "Low-stock products",
                counts["low_stock"],
                "alerts#replenishment",
                "Check available quantities before production",
                "triangle-alert",
            ),
        ]
        recent = clean_row(
            fetch_all("""SELECT m.movement_type action,i.name entity,m.quantity_delta,m.created_at
            FROM inventory_movements m JOIN inventory_items i ON i.item_id=m.item_id
            WHERE m.movement_type<>'opening_balance' ORDER BY m.created_at DESC,m.movement_id DESC LIMIT 5""")
        )
    else:
        counts = fetch_one(
            """SELECT
            COUNT(*) FILTER(WHERE status='pending') paid_review,
            COUNT(*) FILTER(WHERE status='approved') ready_fulfillment
            FROM orders WHERE order_type='reseller' AND team_leader_account_id=%s""",
            (account_id,),
        )
        counts.update(
            fetch_one(
                """SELECT COUNT(*) FILTER(WHERE r.status='submitted') new_returns,
            COUNT(*) FILTER(WHERE r.handed_off_at IS NOT NULL AND r.processing_supervisor_account_id=%s AND r.status NOT IN ('completed','rejected')) transferred_cases
            FROM product_returns r JOIN orders o USING(order_id)
            WHERE o.team_leader_account_id=%s OR r.processing_supervisor_account_id=%s""",
                (account_id, account_id, account_id),
            )
        )
        counts["unread"] = unread_summary(
            account_id, "sales-supervisor" if leader_role == "sales-supervisor" else "team-leader"
        )["unread_conversations"]
        rows = [
            (
                "Paid orders to review",
                counts["paid_review"],
                "orders?status=pending",
                "Check payment evidence and accept or reject",
                "clipboard-check",
            ),
            (
                "Ready for fulfillment",
                counts["ready_fulfillment"],
                "orders?status=approved",
                "Complete delivery from reserved stock",
                "truck",
            ),
            (
                "New return requests",
                counts["new_returns"],
                "returns?status=submitted",
                "Review the request before processor handoff",
                "undo-2",
            ),
            (
                "Unread conversations",
                counts["unread"],
                "messages",
                "Reply to your assigned resellers",
                "message-circle",
            ),
        ]
        if leader_role == "sales-supervisor":
            counts.update(
                fetch_one(
                    """SELECT
                (SELECT COUNT(*) FROM inquiries WHERE status='pending' AND (assigned_team_leader_account_id=%s OR assigned_team_leader_account_id IS NULL)) inquiries,
                (SELECT COUNT(*) FROM order_refunds WHERE status IN ('pending','failed')
                  AND (assigned_supervisor_account_id=%s OR assigned_supervisor_account_id IS NULL)) refund_actions""",
                    (account_id, account_id),
                )
            )
            rows += [
                (
                    "Transferred cases",
                    counts["transferred_cases"],
                    "returns",
                    "Manage accepted requests and reseller follow-ups",
                    "arrow-right-left",
                ),
                (
                    "Refunds awaiting action",
                    counts["refund_actions"],
                    "refunds",
                    "Verify pending payments, process refunds, or retry failures",
                    "wallet-cards",
                ),
                (
                    "Reseller inquiries",
                    counts["inquiries"],
                    "inquiries?status=pending",
                    "Review assigned and unassigned prospects",
                    "user-check",
                ),
            ]
        recent = []
    queue = [
        dict(label=label, count=int(count), path=path, description=description, icon=icon)
        for label, count, path, description, icon in rows
    ]
    return {
        "counts": clean_row(counts),
        "queue": queue,
        "attention_total": sum(row["count"] for row in queue),
        "recent": recent,
    }
