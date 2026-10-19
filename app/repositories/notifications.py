from __future__ import annotations


from ._shared import *


def _notification_recipient_ids(cur, recipient_role: str | None, recipient_account_id: int | None, category: str) -> list[int]:
    if recipient_account_id is not None:
        cur.execute("SELECT account_id FROM accounts WHERE account_id = %s AND is_active = true;", (recipient_account_id,))
    elif recipient_role == "owner":
        cur.execute("SELECT account_id FROM accounts WHERE account_type = 'owner' AND is_active = true;")
    elif recipient_role == "reseller":
        cur.execute("SELECT account_id FROM accounts WHERE account_type = 'reseller' AND is_active = true;")
    elif recipient_role == "team-leader":
        specialization = "inventory" if category == "inventory" else "sales"
        cur.execute(
            "SELECT account_id FROM accounts WHERE account_type = 'team_leader' AND team_leader_role = %s AND is_active = true;",
            (specialization,),
        )
    else:
        return []
    return [int(row["account_id"]) for row in cur.fetchall()]


def _create_notification_cursor(
    cur,
    *,
    recipient_role: str | None = None,
    recipient_account_id: int | None = None,
    category: str,
    severity: str = "info",
    title: str,
    message: str,
    target_url: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    dedupe_key: str | None = None,
) -> int | None:
    if recipient_role is None and recipient_account_id is None:
        return None
    cur.execute(
        """
        INSERT INTO notifications (
            recipient_role, recipient_account_id, category, severity, title, message,
            target_url, source_type, source_id, dedupe_key
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO UPDATE SET dedupe_key = EXCLUDED.dedupe_key
        RETURNING notification_id;
        """,
        (recipient_role, recipient_account_id, category, severity, title, message,
         target_url, source_type, source_id, dedupe_key),
    )
    notification_id = int(cur.fetchone()["notification_id"])
    recipients = _notification_recipient_ids(cur, recipient_role, recipient_account_id, category)
    for account_id in recipients:
        cur.execute(
            "INSERT INTO notification_recipients (notification_id, account_id) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
            (notification_id, account_id),
        )
    return notification_id


def create_notification(
    *,
    recipient_role: str | None = None,
    recipient_account_id: int | None = None,
    category: str,
    severity: str = "info",
    title: str,
    message: str,
    target_url: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    dedupe_key: str | None = None,
) -> None:
    if recipient_role is None and recipient_account_id is None:
        return
    with get_transaction_cursor() as cur:
        _create_notification_cursor(
            cur, recipient_role=recipient_role, recipient_account_id=recipient_account_id,
            category=category, severity=severity, title=title, message=message,
            target_url=target_url, source_type=source_type, source_id=source_id, dedupe_key=dedupe_key,
        )


def _create_order_system_message_cursor(
    cur,
    *,
    reseller_account_id: int,
    order_id: int,
    event_type: str,
    title: str,
    message: str,
    rejection_reason: str | None = None,
    dedupe_key: str | None = None,
    order_refund_attempt_id: int | None = None,
) -> int:
    if event_type not in {
        "approved", "rejected", "fulfilled", "cancelled", "refund_pending",
        "refund_processing", "refund_completed", "refund_failed",
    }:
        raise ValueError("Unsupported order system message type.")
    message_key = dedupe_key or f"order-{order_id}-{event_type}"
    cur.execute(
        """
        INSERT INTO order_system_messages (
            reseller_account_id, order_id, event_type, title, message, rejection_reason,
            dedupe_key, order_refund_attempt_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key)
        DO UPDATE SET
            title = EXCLUDED.title,
            message = EXCLUDED.message,
            rejection_reason = EXCLUDED.rejection_reason,
            order_refund_attempt_id = EXCLUDED.order_refund_attempt_id
        RETURNING order_system_message_id;
        """,
        (
            reseller_account_id,
            order_id,
            event_type,
            title,
            message,
            rejection_reason,
            message_key,
            order_refund_attempt_id,
        ),
    )
    return int(cur.fetchone()["order_system_message_id"])


def list_notifications(role_key: str, account_id: int | None = None, limit: int = 8) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT n.notification_id, n.category, n.severity, n.title, n.message, n.target_url,
               nr.read_at, n.created_at
        FROM notification_recipients nr
        JOIN notifications n ON n.notification_id = nr.notification_id
        WHERE nr.account_id = %s
        ORDER BY n.created_at DESC, n.notification_id DESC
        LIMIT %s;
        """,
        (account_id, limit),
    ))


def list_order_system_messages(account_id: int | None, limit: int = 20) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT osm.order_system_message_id, osm.event_type,
               CASE WHEN osm.event_type IN ('rejected', 'refund_failed') THEN 'critical'
                    WHEN osm.event_type = 'refund_pending' THEN 'warning' ELSE 'info' END AS severity,
               osm.title, osm.message, osm.rejection_reason, osm.order_id,
               ora.refund_transaction_number AS refund_transaction_reference,
               orp.order_refund_proof_id AS refund_proof_id,
               osm.read_at, osm.created_at
        FROM order_system_messages osm
        LEFT JOIN order_refund_attempts ora
          ON ora.order_refund_attempt_id = osm.order_refund_attempt_id
        LEFT JOIN order_refund_proofs orp
          ON orp.order_refund_attempt_id = ora.order_refund_attempt_id
        WHERE osm.reseller_account_id = %s
        ORDER BY osm.created_at DESC, osm.order_system_message_id DESC
        LIMIT %s;
        """,
        (account_id, min(max(1, int(limit)), 50)),
    ))


def get_order_system_message(account_id: int | None, system_message_id: int) -> dict | None:
    return clean_row(fetch_one(
        """
        SELECT osm.order_system_message_id, osm.event_type,
               CASE WHEN osm.event_type IN ('rejected', 'refund_failed') THEN 'critical'
                    WHEN osm.event_type = 'refund_pending' THEN 'warning' ELSE 'info' END AS severity,
               osm.title, osm.message, osm.rejection_reason, osm.order_id,
               ora.refund_transaction_number AS refund_transaction_reference,
               orp.order_refund_proof_id AS refund_proof_id,
               osm.read_at, osm.created_at
        FROM order_system_messages osm
        LEFT JOIN order_refund_attempts ora
          ON ora.order_refund_attempt_id = osm.order_refund_attempt_id
        LEFT JOIN order_refund_proofs orp
          ON orp.order_refund_attempt_id = ora.order_refund_attempt_id
        WHERE osm.reseller_account_id = %s
          AND osm.order_system_message_id = %s;
        """,
        (account_id, system_message_id),
    ))


def get_latest_order_system_message(account_id: int | None, order_id: int) -> dict | None:
    return clean_row(fetch_one(
        """
        SELECT osm.order_system_message_id, osm.event_type,
               CASE WHEN osm.event_type IN ('rejected', 'refund_failed') THEN 'critical'
                    WHEN osm.event_type = 'refund_pending' THEN 'warning' ELSE 'info' END AS severity,
               osm.title, osm.message, osm.rejection_reason, osm.order_id,
               ora.refund_transaction_number AS refund_transaction_reference,
               orp.order_refund_proof_id AS refund_proof_id,
               osm.read_at, osm.created_at
        FROM order_system_messages osm
        LEFT JOIN order_refund_attempts ora
          ON ora.order_refund_attempt_id = osm.order_refund_attempt_id
        LEFT JOIN order_refund_proofs orp
          ON orp.order_refund_attempt_id = ora.order_refund_attempt_id
        WHERE osm.reseller_account_id = %s AND osm.order_id = %s
        ORDER BY osm.created_at DESC, osm.order_system_message_id DESC
        LIMIT 1;
        """,
        (account_id, order_id),
    ))


def unread_order_system_message_count(account_id: int | None) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM order_system_messages
        WHERE reseller_account_id = %s
          AND read_at IS NULL;
        """,
        (account_id,),
    )
    return int(row["total"])


def mark_order_system_message_read(account_id: int | None, system_message_id: int) -> bool:
    if account_id is None:
        return False
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE order_system_messages
            SET read_at = COALESCE(read_at, now())
            WHERE order_system_message_id = %s
              AND reseller_account_id = %s
            RETURNING order_system_message_id;
            """,
            (system_message_id, account_id),
        )
        return cur.fetchone() is not None


def mark_notification_read(account_id: int | None, notification_id: int) -> dict | None:
    if account_id is None:
        return None
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE notification_recipients nr
            SET read_at = COALESCE(nr.read_at, now())
            FROM notifications n
            WHERE nr.notification_id = n.notification_id
              AND nr.notification_id = %s
              AND nr.account_id = %s
            RETURNING n.notification_id, n.target_url, n.source_type, n.source_id;
            """,
            (notification_id, account_id),
        )
        row = cur.fetchone()
    return clean_row(row)


def list_notification_history(
    account_id: int | None,
    *,
    status: str = "",
    category: str = "",
    page: int = 1,
    page_size: int = 15,
) -> list[dict]:
    offset = (max(page, 1) - 1) * page_size
    return clean_row(fetch_all(
        """
        SELECT n.notification_id, n.category, n.severity, n.title, n.message, n.target_url,
               nr.read_at, n.created_at
        FROM notification_recipients nr
        JOIN notifications n ON n.notification_id = nr.notification_id
        WHERE nr.account_id = %s
          AND (
              %s = ''
              OR (%s = 'unread' AND nr.read_at IS NULL)
              OR (%s = 'read' AND nr.read_at IS NOT NULL)
          )
          AND (%s = '' OR lower(n.category) = lower(%s))
        ORDER BY n.created_at DESC, n.notification_id DESC
        LIMIT %s OFFSET %s;
        """,
        (account_id, status, status, status, category, category, page_size, offset),
    ))


def count_notification_history(
    account_id: int | None,
    *,
    status: str = "",
    category: str = "",
) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM notification_recipients nr
        JOIN notifications n ON n.notification_id = nr.notification_id
        WHERE nr.account_id = %s
          AND (
              %s = ''
              OR (%s = 'unread' AND nr.read_at IS NULL)
              OR (%s = 'read' AND nr.read_at IS NOT NULL)
          )
          AND (%s = '' OR lower(n.category) = lower(%s));
        """,
        (account_id, status, status, status, category, category),
    )
    return int(row["total"])


def unread_notification_count(role_key: str, account_id: int | None = None) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM notification_recipients
        WHERE read_at IS NULL AND account_id = %s;
        """,
        (account_id,),
    )
    return int(row["total"])


def mark_notifications_read(role_key: str, account_id: int | None = None) -> None:
    execute_write(
        """
        UPDATE notification_recipients
        SET read_at = now()
        WHERE read_at IS NULL AND account_id = %s;
        """,
        (account_id,),
    )


__all__ = ['_notification_recipient_ids', '_create_notification_cursor', '_create_order_system_message_cursor', 'create_notification', 'list_notifications', 'list_order_system_messages', 'get_order_system_message', 'get_latest_order_system_message', 'unread_order_system_message_count', 'mark_order_system_message_read', 'mark_notification_read', 'list_notification_history', 'count_notification_history', 'unread_notification_count', 'mark_notifications_read']
