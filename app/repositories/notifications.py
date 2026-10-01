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


def list_notifications(role_key: str, account_id: int | None = None, limit: int = 8) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT n.notification_id, n.category, n.severity, n.title, n.message, n.target_url,
               nr.read_at, n.created_at
        FROM notification_recipients nr
        JOIN notifications n ON n.notification_id = nr.notification_id
        WHERE nr.account_id = %s
        ORDER BY nr.read_at NULLS FIRST, n.created_at DESC
        LIMIT %s;
        """,
        (account_id, limit),
    ))


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


__all__ = ['_notification_recipient_ids', '_create_notification_cursor', 'create_notification', 'list_notifications', 'unread_notification_count', 'mark_notifications_read']
