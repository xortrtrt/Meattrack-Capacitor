from __future__ import annotations

import logging
import time

from app.config import APP_BASE_URL
from app.database import get_transaction_cursor
from app.emailer import send_account_activation, send_inquiry_status_update
from app.web_sessions import cleanup_expired_sessions


LOGGER = logging.getLogger("meattrack.worker")


def enqueue_due_inquiry_followups() -> int:
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('meattrack-inquiry-followups')) AS locked;")
        if not cur.fetchone()["locked"]:
            return 0
        cur.execute(
            """
            INSERT INTO notification_outbox (event_type, payload, dedupe_key)
            SELECT 'inquiry_followup',
                   jsonb_build_object('inquiry_id', inquiry_id, 'to_email', email, 'name', name, 'business_name', business_name),
                   'inquiry-followup-' || inquiry_id
            FROM inquiries
            WHERE status IN ('pending', 'assigned', 'contacted')
              AND follow_up_sent_at IS NULL
              AND created_at <= now() - interval '1 day'
            ON CONFLICT (dedupe_key) DO NOTHING;
            """
        )
        return cur.rowcount


def scan_batch_expiry() -> int:
    from app.repositories import _create_notification_cursor

    changed = 0
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('meattrack-expiry-scan')) AS locked;")
        if not cur.fetchone()["locked"]:
            return 0
        cur.execute(
            """
            UPDATE inventory_batches
            SET quality_status = 'expired'
            WHERE quality_status = 'approved' AND expiry_date < CURRENT_DATE
            RETURNING batch_id;
            """
        )
        changed += len(cur.fetchall())
        cur.execute(
            """
            UPDATE alerts a SET status = 'resolved'
            FROM inventory_batches b
            WHERE a.product_batch_id = b.batch_id
              AND a.status IN ('open', 'acknowledged')
              AND (b.quantity_available = 0 OR b.quality_status IN ('rejected', 'spoiled')
                   OR (b.quality_status = 'expired' AND a.alert_type = 'near_expiry'));
            """
        )
        cur.execute(
            """
            SELECT b.batch_id, b.batch_code, b.item_id, i.name
            FROM inventory_batches b JOIN inventory_items i ON i.item_id = b.item_id
            WHERE b.quality_status = 'expired' AND b.quantity_available > 0
            ORDER BY b.batch_id FOR UPDATE OF b;
            """
        )
        for batch in cur.fetchall():
            message = f"{batch['name']} batch {batch['batch_code']} is expired and still has stock."
            cur.execute(
                """
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message)
                VALUES ('expired_batch', 'critical', %s, %s, %s)
                ON CONFLICT (product_batch_id, alert_type)
                    WHERE product_batch_id IS NOT NULL AND status IN ('open', 'acknowledged')
                DO UPDATE SET message = EXCLUDED.message, severity = 'critical';
                """,
                (batch["item_id"], batch["batch_id"], message),
            )
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory", severity="critical",
                title="Expired batch requires action", message=message,
                target_url="/portal/team-leader/batches", source_type="inventory_batches",
                source_id=batch["batch_id"], dedupe_key=f"batch-expired-{batch['batch_id']}",
            )
        cur.execute(
            """
            SELECT b.batch_id, b.batch_code, b.expiry_date, b.quantity_available, i.item_id, i.name,
                   (b.expiry_date - CURRENT_DATE) AS days_left
            FROM inventory_batches b
            JOIN inventory_items i ON i.item_id = b.item_id
            WHERE b.quality_status = 'approved' AND b.quantity_available > 0
              AND b.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7
            ORDER BY b.expiry_date, b.batch_id
            FOR UPDATE OF b;
            """
        )
        for batch in cur.fetchall():
            days = int(batch["days_left"])
            severity = "critical" if days <= 2 else "warning"
            cur.execute(
                """
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message)
                VALUES ('near_expiry', %s, %s, %s, %s)
                ON CONFLICT (product_batch_id, alert_type)
                    WHERE product_batch_id IS NOT NULL AND status IN ('open', 'acknowledged')
                DO UPDATE SET severity = EXCLUDED.severity, message = EXCLUDED.message;
                """,
                (severity, batch["item_id"], batch["batch_id"],
                 f"{batch['name']} batch {batch['batch_code']} expires in {days} day(s)."),
            )
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory", severity=severity,
                title="Batch expiry warning",
                message=f"{batch['name']} batch {batch['batch_code']} expires in {days} day(s).",
                target_url="/portal/team-leader/batches", source_type="inventory_batches",
                source_id=batch["batch_id"], dedupe_key=f"batch-expiry-{batch['batch_id']}-{severity}",
            )
    return changed


def _claim_outbox() -> dict | None:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT outbox_id, event_type, payload, attempt_count
            FROM notification_outbox
            WHERE sent_at IS NULL AND attempt_count < 8 AND next_attempt_at <= now()
              AND (locked_at IS NULL OR locked_at < now() - interval '5 minutes')
            ORDER BY outbox_id
            FOR UPDATE SKIP LOCKED
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("UPDATE notification_outbox SET locked_at = now() WHERE outbox_id = %s;", (row["outbox_id"],))
        return dict(row)


def _deliver(row: dict) -> tuple[bool, str]:
    payload = dict(row["payload"])
    if row["event_type"] == "account_activation":
        return send_account_activation(
            to_email=payload["to_email"], name=payload["name"], account_label=payload["account_label"],
            activation_url=APP_BASE_URL + payload["activation_path"],
        )
    if row["event_type"] == "inquiry_followup":
        return send_inquiry_status_update(
            to_email=payload["to_email"], name=payload["name"], business_name=payload["business_name"],
        )
    return False, f"Unknown outbox event type: {row['event_type']}"


def process_one_outbox() -> bool:
    row = _claim_outbox()
    if not row:
        return False
    try:
        sent, message = _deliver(row)
    except Exception as exc:  # provider/network failure is retried by durable state
        sent, message = False, f"{exc.__class__.__name__}: {exc}"
    with get_transaction_cursor() as cur:
        if sent:
            cur.execute(
                "UPDATE notification_outbox SET sent_at = now(), locked_at = NULL, last_error = NULL WHERE outbox_id = %s;",
                (row["outbox_id"],),
            )
            if row["event_type"] == "inquiry_followup":
                cur.execute(
                    "UPDATE inquiries SET follow_up_sent_at = now() WHERE inquiry_id = %s AND follow_up_sent_at IS NULL;",
                    (row["payload"]["inquiry_id"],),
                )
        else:
            attempt = int(row["attempt_count"]) + 1
            delay_minutes = min(2 ** max(0, attempt - 1), 240)
            cur.execute(
                """
                UPDATE notification_outbox
                SET attempt_count = %s, next_attempt_at = now() + make_interval(mins => %s),
                    locked_at = NULL, last_error = %s
                WHERE outbox_id = %s;
                """,
                (attempt, delay_minutes, message[:1000], row["outbox_id"]),
            )
    return True


def cleanup_ephemeral_security_data() -> None:
    with get_transaction_cursor() as cur:
        cur.execute("DELETE FROM security_events WHERE created_at < now() - interval '30 days'")
        cur.execute("DELETE FROM account_login_otps WHERE consumed_at < now() - interval '30 days'")
        cur.execute("DELETE FROM account_password_otps WHERE consumed_at < now() - interval '30 days'")
        cur.execute("DELETE FROM account_activation_tokens WHERE consumed_at < now() - interval '30 days'")


def run_once() -> None:
    enqueue_due_inquiry_followups()
    scan_batch_expiry()
    cleanup_expired_sessions()
    cleanup_ephemeral_security_data()
    while process_one_outbox():
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            run_once()
        except Exception:
            LOGGER.exception("Worker cycle failed")
        time.sleep(60)


if __name__ == "__main__":
    main()
