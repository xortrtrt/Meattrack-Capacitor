from __future__ import annotations

import logging
import time

from app.config import APP_BASE_URL
from app.database import get_transaction_cursor
from app.emailer import (
    send_account_activation,
    send_account_password_reset,
    send_inquiry_rejection,
    send_inquiry_status_update,
)
from app.web_sessions import cleanup_expired_sessions
from app import live_chat
from app.repositories import forecasts as repositories
from app.repositories import orders as order_repositories


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
    from app.repositories.notifications import _create_notification_cursor
    from app.repositories.superuser import business_setting

    changed = 0
    near_expiry_days = int(business_setting("inventory.near_expiry_days"))
    critical_expiry_days = int(business_setting("inventory.critical_expiry_days"))
    notify_owners = bool(business_setting("notifications.owner_critical_alerts"))
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('meattrack-expiry-scan')) AS locked;")
        if not cur.fetchone()["locked"]:
            return 0
        # Return actions lock the case before its reserved batches. Use the same
        # order so expiry scanning cannot deadlock a replacement release.
        cur.execute("""SELECT return_id FROM product_returns r WHERE EXISTS (
            SELECT 1 FROM replacement_stock_reservations s WHERE s.return_id=r.return_id
        ) ORDER BY return_id FOR UPDATE;""")
        cur.fetchall()
        cur.execute(
            """
            UPDATE inventory_batches
            SET quality_status = 'expired'
            WHERE quality_status = 'approved' AND expiry_date <= (now() AT TIME ZONE 'Asia/Manila')::date
            RETURNING batch_id;
            """
        )
        changed += len(cur.fetchall())
        from app.repositories.product_returns import sync_expired_custody
        sync_expired_custody(cur)
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
            SELECT b.batch_id, b.batch_code, b.item_id, b.quantity_available, b.expiry_date, i.name
            FROM inventory_batches b JOIN inventory_items i ON i.item_id = b.item_id
            WHERE b.quality_status = 'expired' AND b.quantity_available > 0
            ORDER BY b.batch_id FOR UPDATE OF b;
            """
        )
        for batch in cur.fetchall():
            message = f"{batch['name']} batch {batch['batch_code']}: {batch['quantity_available']:g} packs expired on {batch['expiry_date']}. Pull-out required."
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
                target_url="/portal/team-leader/expired-stock", source_type="inventory_batches",
                source_id=batch["batch_id"], dedupe_key=f"batch-expired-{batch['batch_id']}",
            )
            if notify_owners:
                _create_notification_cursor(
                    cur, recipient_role="owner", category="inventory", severity="critical",
                    title="Expired batch requires action", message=message,
                    target_url="/portal/owner/alerts", source_type="inventory_batches",
                    source_id=batch["batch_id"], dedupe_key=f"owner-batch-expired-{batch['batch_id']}",
                )
        cur.execute(
            """
            SELECT b.batch_id, b.batch_code, b.expiry_date, b.quantity_available, i.item_id, i.name,
                   (b.expiry_date - (now() AT TIME ZONE 'Asia/Manila')::date) AS days_left
            FROM inventory_batches b
            JOIN inventory_items i ON i.item_id = b.item_id
            WHERE b.quality_status = 'approved' AND b.quantity_available > 0
              AND b.expiry_date > (now() AT TIME ZONE 'Asia/Manila')::date AND b.expiry_date <= (now() AT TIME ZONE 'Asia/Manila')::date + %s
            ORDER BY b.expiry_date, b.batch_id
            FOR UPDATE OF b;
            """
        , (near_expiry_days,))
        for batch in cur.fetchall():
            days = int(batch["days_left"])
            severity = "critical" if days <= critical_expiry_days else "warning"
            cur.execute(
                """
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message)
                VALUES ('near_expiry', %s, %s, %s, %s)
                ON CONFLICT (product_batch_id, alert_type)
                    WHERE product_batch_id IS NOT NULL AND status IN ('open', 'acknowledged')
                DO UPDATE SET severity = EXCLUDED.severity, message = EXCLUDED.message;
                """,
                (severity, batch["item_id"], batch["batch_id"],
                 f"{batch['name']} batch {batch['batch_code']}: {batch['quantity_available']:g} packs expire on {batch['expiry_date']} ({days} day(s))."),
            )
            _create_notification_cursor(
                cur, recipient_role="team-leader", category="inventory", severity=severity,
                title="Batch expiry warning",
                message=f"{batch['name']} batch {batch['batch_code']}: {batch['quantity_available']:g} packs expire on {batch['expiry_date']} ({days} day(s)).",
                target_url="/portal/team-leader/batches", source_type="inventory_batches",
                source_id=batch["batch_id"], dedupe_key=f"batch-expiry-{batch['batch_id']}-{severity}",
            )
            if notify_owners and severity == "critical":
                _create_notification_cursor(
                    cur, recipient_role="owner", category="inventory", severity="critical",
                    title="Critical batch expiry warning",
                    message=f"{batch['name']} batch {batch['batch_code']}: {batch['quantity_available']:g} packs expire on {batch['expiry_date']} ({days} day(s)).",
                    target_url="/portal/owner/alerts", source_type="inventory_batches",
                    source_id=batch["batch_id"], dedupe_key=f"owner-batch-expiry-{batch['batch_id']}-{severity}",
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
    if row["event_type"] == "account_password_reset":
        return send_account_password_reset(
            to_email=payload["to_email"], name=payload["name"],
            reset_url=APP_BASE_URL + payload["reset_path"],
        )
    if row["event_type"] == "inquiry_followup":
        return send_inquiry_status_update(
            to_email=payload["to_email"], name=payload["name"], business_name=payload["business_name"],
        )
    if row["event_type"] == "inquiry_rejected":
        return send_inquiry_rejection(
            to_email=payload["to_email"],
            name=payload["name"],
            business_name=payload["business_name"],
            rejection_reason=payload["rejection_reason"],
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


def retry_live_chat_publications() -> None:
    if not live_chat.LIVE_CHAT_ENABLED or not live_chat.ABLY_API_KEY:
        return
    for message in live_chat.pending_messages():
        try:
            live_chat.publish_message(message, str(message["conversation_id"]))
        except live_chat.AblyUnavailable as exc:
            live_chat.mark_publish_failed(int(message["chat_message_id"]), str(exc))


def process_one_forecast() -> bool:
    run = repositories.claim_forecast_run()
    if not run:
        return False
    try:
        repositories.process_forecast_run(run)
    except Exception as exc:
        LOGGER.exception("Forecast run %s failed", run["forecast_run_id"])
        repositories.fail_forecast_run(run, exc)
    return True


def run_fast_once() -> None:
    order_repositories.expire_unpaid_orders()
    while process_one_outbox():
        pass
    process_one_forecast()
    retry_live_chat_publications()


def run_maintenance_once() -> None:
    from app.repositories.superuser import report_system_status

    enqueue_due_inquiry_followups()
    scan_batch_expiry()
    cleanup_expired_sessions()
    cleanup_ephemeral_security_data()
    report_system_status("retention_job", "ok", "Expired sessions and security records were cleaned up.")
    live_chat.maintenance()
    repositories.recover_stale_forecast_runs()
    report_system_status("worker", "ok", "Background worker maintenance completed.")


def run_once() -> None:
    run_maintenance_once()
    run_fast_once()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    last_maintenance = 0.0
    while True:
        try:
            now = time.monotonic()
            if now - last_maintenance >= 60:
                run_maintenance_once()
                last_maintenance = now
            run_fast_once()
        except Exception:
            LOGGER.exception("Worker cycle failed")
        time.sleep(5)


if __name__ == "__main__":
    main()
