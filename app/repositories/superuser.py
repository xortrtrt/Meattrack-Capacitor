from __future__ import annotations

import json
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.audit_context import current_audit_context
from app.business_time import business_now, business_today
from app import inventory_measurements as measurements
from app.security import hash_password, verify_password
from app.web_sessions import revoke_account_sessions

from ._shared import clean_row, fetch_all, fetch_one, get_transaction_cursor


SETTING_DEFAULTS: dict[str, Any] = {
    "order.payment_window_minutes": 15,
    "inventory.near_expiry_days": 14,
    "inventory.critical_expiry_days": 2,
    "forecast.default_horizon_days": 7,
    "notifications.owner_critical_alerts": True,
    "business.contact_email": "",
    "business.contact_phone": "",
    "business.contact_address": "",
    "business.hours": "",
}


def _audit_cursor(
    cur,
    *,
    actor_account_id: int | None,
    actor_role: str,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    reason: str = "",
    before_state: dict | None = None,
    after_state: dict | None = None,
    outcome: str = "success",
    request_id: str = "",
    source_key: str = "",
) -> None:
    context_request_id, context_source_key = current_audit_context()
    request_id = request_id or context_request_id
    source_key = source_key or context_source_key
    cur.execute(
        """
        INSERT INTO activity_logs (
            account_id, actor_role, action, entity_type, entity_id, reason,
            before_state, after_state, outcome, request_id, source_key, created_at
        ) VALUES (%s, %s, %s, %s, %s, NULLIF(%s, ''), %s::jsonb, %s::jsonb,
                  %s, NULLIF(%s, ''), NULLIF(%s, ''), now());
        """,
        (
            actor_account_id,
            actor_role,
            action,
            entity_type,
            entity_id,
            reason.strip(),
            json.dumps(before_state) if before_state is not None else None,
            json.dumps(after_state) if after_state is not None else None,
            outcome,
            request_id,
            source_key,
        ),
    )


def record_owner_audit(
    actor_account_id: int,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    *,
    reason: str = "",
    before_state: dict | None = None,
    after_state: dict | None = None,
    outcome: str = "success",
    request_id: str = "",
    source_key: str = "",
) -> None:
    with get_transaction_cursor() as cur:
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            before_state=before_state,
            after_state=after_state,
            outcome=outcome,
            request_id=request_id,
            source_key=source_key,
        )


def request_owner_step_up(account_id: int) -> dict:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = business_now() + timedelta(minutes=10)
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT account_id, name, email FROM accounts
            WHERE account_id = %s AND account_type = 'owner' AND is_active = true
            FOR UPDATE;
            """,
            (account_id,),
        )
        owner = cur.fetchone()
        if not owner:
            raise ValueError("An active owner account is required.")
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM owner_step_up_challenges
            WHERE account_id = %s AND created_at >= now() - interval '10 minutes';
            """,
            (account_id,),
        )
        if int(cur.fetchone()["total"]) >= 3:
            raise ValueError("Too many verification codes were requested. Try again in 10 minutes.")
        cur.execute(
            """
            UPDATE owner_step_up_challenges SET consumed_at = now()
            WHERE account_id = %s AND consumed_at IS NULL;
            """,
            (account_id,),
        )
        cur.execute(
            """
            INSERT INTO owner_step_up_challenges (account_id, otp_hash, expires_at)
            VALUES (%s, %s, %s)
            RETURNING owner_step_up_challenge_id;
            """,
            (account_id, hash_password(code), expires_at),
        )
        challenge_id = int(cur.fetchone()["owner_step_up_challenge_id"])
        _audit_cursor(
            cur,
            actor_account_id=account_id,
            actor_role="owner",
            action="requested_owner_step_up",
            entity_type="owner_step_up_challenges",
            entity_id=challenge_id,
        )
    return {
        "challenge_id": challenge_id,
        "otp_code": code,
        "expires_at": expires_at,
        "name": owner["name"],
        "email": owner["email"],
    }


def cancel_owner_step_up(account_id: int, challenge_id: int) -> None:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE owner_step_up_challenges SET consumed_at = now()
            WHERE owner_step_up_challenge_id = %s AND account_id = %s AND consumed_at IS NULL;
            """,
            (challenge_id, account_id),
        )


def confirm_owner_step_up(account_id: int, challenge_id: int, otp_code: str) -> None:
    error_message: str | None = None
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT * FROM owner_step_up_challenges
            WHERE owner_step_up_challenge_id = %s AND account_id = %s
            FOR UPDATE;
            """,
            (challenge_id, account_id),
        )
        challenge = cur.fetchone()
        if not challenge or challenge["consumed_at"] is not None:
            error_message = "This verification code is no longer available."
        elif challenge["expires_at"] <= business_now():
            cur.execute(
                "UPDATE owner_step_up_challenges SET consumed_at = now() WHERE owner_step_up_challenge_id = %s;",
                (challenge_id,),
            )
            error_message = "This verification code has expired. Request a new code."
        elif int(challenge["attempt_count"]) >= 5:
            error_message = "Too many incorrect attempts. Request a new code."
        elif not verify_password(str(otp_code).strip(), challenge["otp_hash"]):
            cur.execute(
                """
                UPDATE owner_step_up_challenges
                SET attempt_count = attempt_count + 1,
                    consumed_at = CASE WHEN attempt_count + 1 >= 5 THEN now() ELSE consumed_at END
                WHERE owner_step_up_challenge_id = %s;
                """,
                (challenge_id,),
            )
            error_message = "The verification code is incorrect."
        else:
            cur.execute(
                "UPDATE owner_step_up_challenges SET consumed_at = now() WHERE owner_step_up_challenge_id = %s;",
                (challenge_id,),
            )
        if challenge:
            _audit_cursor(
                cur,
                actor_account_id=account_id,
                actor_role="owner",
                action="confirmed_owner_step_up",
                entity_type="owner_step_up_challenges",
                entity_id=challenge_id,
                outcome="failed" if error_message else "success",
                after_state={"failure": error_message} if error_message else None,
            )
    if error_message:
        raise ValueError(error_message)


def set_account_active(account_id: int, active: bool, actor_account_id: int, reason: str) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('meattrack-owner-account-admin'));")
        cur.execute("SELECT * FROM accounts WHERE account_id = %s FOR UPDATE;", (account_id,))
        target = cur.fetchone()
        if not target:
            raise ValueError("Account not found.")
        before = {
            "is_active": bool(target["is_active"]),
            "activation_status": target["activation_status"],
            "account_type": target["account_type"],
        }
        if not active and target["account_type"] == "owner":
            cur.execute(
                "SELECT COUNT(*) AS total FROM accounts WHERE account_type = 'owner' AND is_active = true AND account_id <> %s;",
                (account_id,),
            )
            if int(cur.fetchone()["total"]) < 1:
                raise ValueError("The final active owner cannot be deactivated.")
        if not active and target["account_type"] == "team_leader":
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM resellers WHERE team_leader_account_id = %s AND reseller_status = 'active') AS resellers,
                    (SELECT COUNT(*) FROM inquiries WHERE assigned_team_leader_account_id = %s AND status IN ('pending','assigned','contacted')) AS inquiries,
                    (SELECT COUNT(*) FROM orders WHERE team_leader_account_id = %s AND status IN ('to_pay','pending','approved')) AS orders,
                    (SELECT COUNT(*) FROM order_refunds WHERE assigned_supervisor_account_id = %s AND status IN ('pending','processing','failed')) AS refunds,
                    (SELECT COUNT(*) FROM chat_conversations WHERE assigned_team_leader_account_id = %s
                        AND status IN ('queued','active','awaiting_contact','follow_up')) AS conversations;
                """,
                (account_id, account_id, account_id, account_id, account_id),
            )
            workload = dict(cur.fetchone())
            if any(int(value) for value in workload.values()):
                summary = ", ".join(f"{value} {key}" for key, value in workload.items() if int(value))
                raise ValueError(f"Transfer this team leader's active work before deactivation: {summary}.")
        cur.execute(
            "UPDATE accounts SET is_active = %s WHERE account_id = %s RETURNING account_id, account_type, name, email, is_active, activation_status, reseller_id;",
            (active, account_id),
        )
        updated = dict(cur.fetchone())
        if updated.get("reseller_id") is not None:
            cur.execute(
                "UPDATE resellers SET reseller_status = %s WHERE reseller_id = %s;",
                ("active" if active else "suspended", updated["reseller_id"]),
            )
        after = {"is_active": active, "activation_status": updated["activation_status"], "account_type": updated["account_type"]}
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="reactivated_account" if active else "deactivated_account",
            entity_type="accounts",
            entity_id=account_id,
            reason=clean_reason,
            before_state=before,
            after_state=after,
        )
    if not active:
        revoke_account_sessions(account_id)
    return clean_row(updated)


def revoke_managed_account_sessions(account_id: int, actor_account_id: int, reason: str) -> None:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    account = fetch_one("SELECT account_id, name, account_type FROM accounts WHERE account_id = %s;", (account_id,))
    if not account:
        raise ValueError("Account not found.")
    revoke_account_sessions(account_id)
    record_owner_audit(
        actor_account_id,
        "revoked_account_sessions",
        "accounts",
        account_id,
        reason=clean_reason,
        before_state={"sessions": "active"},
        after_state={"sessions": "revoked"},
    )


def list_active_sessions(limit: int = 100) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT ws.account_id, a.name, a.email, a.account_type, a.team_leader_role,
               COUNT(*) AS session_count, MAX(ws.updated_at) AS last_activity,
               MAX(ws.expires_at) AS latest_expiry
        FROM web_sessions ws JOIN accounts a ON a.account_id = ws.account_id
        WHERE ws.expires_at > now()
        GROUP BY ws.account_id, a.name, a.email, a.account_type, a.team_leader_role
        ORDER BY last_activity DESC LIMIT %s;
        """,
        (limit,),
    ))


def list_security_events(limit: int = 100) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT security_event_id, event_type, subject_key, succeeded, created_at
        FROM security_events ORDER BY security_event_id DESC LIMIT %s;
        """,
        (limit,),
    ))


def transfer_team_leader_work(
    from_account_id: int,
    to_account_id: int,
    actor_account_id: int,
    reason: str,
) -> dict[str, int]:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    if from_account_id == to_account_id:
        raise ValueError("Choose a different destination team leader.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('meattrack-owner-account-admin'));")
        cur.execute(
            """
            SELECT account_id, name, team_leader_role FROM accounts
            WHERE account_id IN (%s, %s) AND account_type = 'team_leader'
            ORDER BY account_id FOR UPDATE;
            """,
            (from_account_id, to_account_id),
        )
        leaders = {int(row["account_id"]): row for row in cur.fetchall()}
        if from_account_id not in leaders:
            raise ValueError("Source team leader was not found.")
        destination = leaders.get(to_account_id)
        if not destination or destination["team_leader_role"] not in {"sales", "sales-supervisor"}:
            raise ValueError("Choose an active sales team leader as the destination.")
        cur.execute("SELECT is_active FROM accounts WHERE account_id = %s;", (to_account_id,))
        if not cur.fetchone()["is_active"]:
            raise ValueError("The destination team leader must be active.")

        updates = (
            ("resellers", "team_leader_account_id", "reseller_status IN ('pending','active','suspended')"),
            ("inquiries", "assigned_team_leader_account_id", "status IN ('pending','assigned','contacted')"),
            ("orders", "team_leader_account_id", "status IN ('to_pay','pending','approved')"),
            ("order_refunds", "assigned_supervisor_account_id", "status IN ('pending','processing','failed')"),
            ("chat_conversations", "assigned_team_leader_account_id", "status IN ('queued','active','awaiting_contact','follow_up')"),
        )
        counts: dict[str, int] = {}
        for table, column, condition in updates:
            cur.execute(
                f"UPDATE {table} SET {column} = %s WHERE {column} = %s AND {condition};",
                (to_account_id, from_account_id),
            )
            counts[table] = cur.rowcount
        cur.execute(
            "UPDATE team_leader_presence SET active_conversation_id = NULL, availability = 'offline', updated_at = now() WHERE account_id = %s;",
            (from_account_id,),
        )
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="transferred_team_leader_work",
            entity_type="accounts",
            entity_id=from_account_id,
            reason=clean_reason,
            before_state={"assigned_to": from_account_id},
            after_state={"assigned_to": to_account_id, "transferred": counts},
        )
    return counts


def administer_conversation(
    conversation_id: str,
    actor_account_id: int,
    reason: str,
    *,
    assigned_team_leader_account_id: int | None = None,
    close: bool = False,
) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    if not close and assigned_team_leader_account_id is None:
        raise ValueError("Choose a team leader or close the conversation.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM chat_conversations WHERE conversation_id = %s FOR UPDATE;", (conversation_id,))
        conversation = cur.fetchone()
        if not conversation:
            raise ValueError("Conversation not found.")
        if conversation["status"] in {"closed", "cancelled"}:
            raise ValueError("This conversation is already closed.")
        before = {
            "status": conversation["status"],
            "assigned_team_leader_account_id": conversation["assigned_team_leader_account_id"],
        }
        if close:
            cur.execute(
                """
                UPDATE chat_conversations SET status = 'closed', closed_at = now(), updated_at = now()
                WHERE conversation_id = %s RETURNING *;
                """,
                (conversation_id,),
            )
            action = "administratively_closed_conversation"
        else:
            cur.execute(
                """
                SELECT account_id FROM accounts
                WHERE account_id = %s AND account_type = 'team_leader' AND is_active = true
                  AND team_leader_role IN ('sales','sales-supervisor') FOR SHARE;
                """,
                (assigned_team_leader_account_id,),
            )
            if not cur.fetchone():
                raise ValueError("Choose an active sales team leader.")
            cur.execute(
                """
                UPDATE chat_conversations
                SET assigned_team_leader_account_id = %s, status = 'active',
                    claimed_at = COALESCE(claimed_at, now()), updated_at = now()
                WHERE conversation_id = %s RETURNING *;
                """,
                (assigned_team_leader_account_id, conversation_id),
            )
            action = "reassigned_conversation"
        updated = dict(cur.fetchone())
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action=action,
            entity_type="chat_conversations",
            reason=clean_reason,
            before_state=before,
            after_state={
                "conversation_id": conversation_id,
                "status": updated["status"],
                "assigned_team_leader_account_id": updated["assigned_team_leader_account_id"],
            },
        )
    return clean_row(updated)


def compensate_inventory(
    item_id: int,
    quantity_delta: object,
    actor_account_id: int,
    reason: str,
    *,
    batch_id: int | None = None,
) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    raw = str(quantity_delta).strip()
    sign = -1 if raw.startswith("-") else 1
    magnitude = raw[1:] if raw.startswith(("-", "+")) else raw
    delta = measurements.parse_decimal_exact(magnitude, "Correction quantity", positive=True) * sign
    with get_transaction_cursor() as cur:
        # Same lock order as reservations and FEFO fulfillment.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));", (f"inventory-product:{item_id}",))
        cur.execute("SELECT * FROM inventory_items WHERE item_id = %s AND is_active = true FOR UPDATE;", (item_id,))
        item = cur.fetchone()
        if not item:
            raise ValueError("Inventory item not found.")
        product_before = product_after = reserved = None
        if item["item_type"] == "finished_product":
            if delta != delta.to_integral_value():
                raise ValueError("Finished-product corrections must use whole packs.")
            if batch_id is None:
                raise ValueError("Choose the finished-product batch to correct.")
            cur.execute("SELECT * FROM inventory_batches WHERE batch_id = %s AND item_id = %s FOR UPDATE;", (batch_id, item_id))
            batch = cur.fetchone()
            if not batch:
                raise ValueError("Inventory batch not found.")
            cur.execute("SELECT COALESCE(SUM(quantity),0) reserved FROM replacement_stock_reservations WHERE batch_id=%s;", (batch_id,))
            batch_reserved = Decimal(cur.fetchone()["reserved"])
            before = Decimal(batch["quantity_available"])
            after = measurements.require_capacity(before + delta, "Resulting batch stock")
            if after < 0:
                raise ValueError("The correction would make batch stock negative.")
            if after < batch_reserved:
                raise ValueError("The correction would remove stock reserved for replacements.")
            cur.execute("""
                SELECT COALESCE(SUM(quantity_available), 0) AS available
                FROM inventory_batches WHERE item_id = %s AND quality_status = 'approved'
                  AND expiry_date > (now() AT TIME ZONE 'Asia/Manila')::date;
            """, (item_id,))
            product_before = Decimal(cur.fetchone()["available"])
            sellable = batch["quality_status"] == "approved" and batch["expiry_date"] > business_today()
            product_after = product_before + (delta if sellable else 0)
            cur.execute("SELECT COALESCE(SUM(quantity), 0) AS reserved FROM all_stock_reservations WHERE product_id = %s;", (item_id,))
            reserved = Decimal(cur.fetchone()["reserved"])
            if delta < 0 and sellable and product_after < reserved:
                raise ValueError("The correction would remove stock reserved for existing orders.")
            received = measurements.require_capacity(Decimal(batch["quantity_received"]) + max(delta, Decimal(0)), "Resulting received quantity")
            cur.execute("""
                UPDATE inventory_batches SET quantity_received = %s, quantity_available = %s
                WHERE batch_id = %s;
            """, (received, after, batch_id))
        else:
            if batch_id is not None:
                raise ValueError("Raw-material corrections do not use a batch.")
            before = Decimal(item["quantity_available"])
            after = measurements.require_capacity(before + delta, "Resulting material stock")
            if after < 0:
                raise ValueError("The correction would make available stock negative.")
            cur.execute("UPDATE inventory_items SET quantity_available = %s WHERE item_id = %s;", (after, item_id))
        cur.execute("SELECT name FROM accounts WHERE account_id = %s;", (actor_account_id,))
        actor = cur.fetchone()
        cur.execute("""
            INSERT INTO inventory_movements (
                item_id, affected_batch_id, movement_type, quantity_delta, unit,
                balance_before, balance_after, actor_account_id, actor_name, note
            ) VALUES (%s, %s, 'owner_correction', %s, %s, %s, %s, %s, %s, %s)
            RETURNING movement_id;
        """, (item_id, batch_id, delta, item["unit"], before, after, actor_account_id,
              actor["name"] if actor else "Owner", clean_reason))
        movement_id = int(cur.fetchone()["movement_id"])
        _audit_cursor(
            cur, actor_account_id=actor_account_id, actor_role="owner",
            action="compensated_inventory", entity_type="inventory_movements", entity_id=movement_id,
            reason=clean_reason,
            before_state={"item_id": item_id, "available": str(before), "batch_id": batch_id,
                          "sellable_stock": str(product_before) if product_before is not None else None,
                          "reserved": str(reserved) if reserved is not None else None},
            after_state={"item_id": item_id, "available": str(after), "delta": str(delta), "batch_id": batch_id,
                         "sellable_stock": str(product_after) if product_after is not None else None},
        )
    return {"movement_id": movement_id, "available": after}


def retry_outbox_item(outbox_id: int, actor_account_id: int, reason: str) -> None:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM notification_outbox WHERE outbox_id = %s FOR UPDATE;", (outbox_id,))
        item = cur.fetchone()
        if not item or item["sent_at"] is not None or not item["last_error"]:
            raise ValueError("Failed outbox item not found.")
        cur.execute(
            "UPDATE notification_outbox SET attempt_count = 0, next_attempt_at = now(), locked_at = NULL WHERE outbox_id = %s;",
            (outbox_id,),
        )
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="retried_outbox_item",
            entity_type="notification_outbox",
            entity_id=outbox_id,
            reason=clean_reason,
            before_state={"attempt_count": item["attempt_count"], "last_error": str(item["last_error"])[:500]},
            after_state={"attempt_count": 0, "queued": True},
        )


def list_business_settings() -> dict[str, Any]:
    rows = fetch_all("SELECT setting_key, setting_value FROM business_settings ORDER BY setting_key;")
    values = dict(SETTING_DEFAULTS)
    values.update({row["setting_key"]: row["setting_value"] for row in rows})
    return values


def business_setting(key: str) -> Any:
    if key not in SETTING_DEFAULTS:
        raise ValueError("Unknown business setting.")
    row = fetch_one("SELECT setting_value FROM business_settings WHERE setting_key = %s;", (key,))
    return row["setting_value"] if row else SETTING_DEFAULTS[key]


def update_business_settings(values: dict[str, object], actor_account_id: int, reason: str) -> dict[str, Any]:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    unknown = set(values) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError("One or more settings are not editable.")
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if key == "order.payment_window_minutes":
            normalized[key] = int(value)
            if not 1 <= normalized[key] <= 1440:
                raise ValueError("Payment window must be between 1 and 1440 minutes.")
        elif key in {"inventory.near_expiry_days", "inventory.critical_expiry_days"}:
            normalized[key] = int(value)
            if not (14 if key == "inventory.near_expiry_days" else 0) <= normalized[key] <= 90:
                raise ValueError("Expiry windows must be between 0 and 90 days; near-expiry warnings require at least 14 days.")
        elif key == "forecast.default_horizon_days":
            normalized[key] = int(value)
            if not 1 <= normalized[key] <= 365:
                raise ValueError("Forecast horizon must be between 1 and 365 days.")
        elif key == "notifications.owner_critical_alerts":
            normalized[key] = str(value).lower() in {"1", "true", "yes", "on"}
        else:
            normalized[key] = " ".join(str(value).strip().split())
            if len(normalized[key]) > 500:
                raise ValueError("Business contact fields cannot exceed 500 characters.")
    if normalized.get("inventory.critical_expiry_days", 2) > normalized.get("inventory.near_expiry_days", 14):
        raise ValueError("Critical expiry days cannot exceed the near-expiry window.")
    before = list_business_settings()
    with get_transaction_cursor() as cur:
        for key, value in normalized.items():
            cur.execute(
                """
                INSERT INTO business_settings (setting_key, setting_value, updated_by_account_id, updated_at)
                VALUES (%s, %s::jsonb, %s, now())
                ON CONFLICT (setting_key) DO UPDATE
                SET setting_value = EXCLUDED.setting_value,
                    updated_by_account_id = EXCLUDED.updated_by_account_id,
                    updated_at = now();
                """,
                (key, json.dumps(value), actor_account_id),
            )
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="updated_business_settings",
            entity_type="business_settings",
            reason=clean_reason,
            before_state=before,
            after_state={**before, **normalized},
        )
    return {**before, **normalized}


def owner_operations_summary() -> dict:
    row = fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM inquiries WHERE status IN ('pending','assigned','contacted')) AS pending_inquiries,
          (SELECT COUNT(*) FROM orders WHERE status IN ('to_pay','pending','approved')) AS pending_orders,
          (SELECT COUNT(*) FROM resellers WHERE team_leader_account_id IS NULL) AS unassigned_resellers,
          (SELECT COUNT(*) FROM order_refunds WHERE status IN ('pending','processing','failed')) AS open_refunds,
          (SELECT COUNT(*) FROM alerts WHERE status IN ('open','acknowledged')) AS open_alerts,
          (SELECT COUNT(*) FROM accounts WHERE activation_status = 'pending') AS pending_activations,
          (SELECT COUNT(*) FROM notification_outbox WHERE sent_at IS NULL AND last_error IS NOT NULL) AS failed_outbox,
          (SELECT COUNT(*) FROM forecast_runs WHERE status = 'failed') AS failed_forecasts;
        """
    )
    return {key: int(value or 0) for key, value in dict(row or {}).items()}


def list_team_workload() -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT a.account_id, a.name, a.team_leader_role, a.is_active,
               (SELECT COUNT(*) FROM resellers r WHERE r.team_leader_account_id = a.account_id
                    AND r.reseller_status = 'active') AS active_resellers,
               (SELECT COUNT(*) FROM inquiries i WHERE i.assigned_team_leader_account_id = a.account_id
                    AND i.status IN ('pending','assigned','contacted')) AS open_inquiries,
               (SELECT COUNT(*) FROM orders o WHERE o.team_leader_account_id = a.account_id
                    AND o.status IN ('to_pay','pending','approved')) AS open_orders,
               (SELECT COUNT(*) FROM order_refunds rf WHERE rf.assigned_supervisor_account_id = a.account_id
                    AND rf.status IN ('pending','processing','failed')) AS open_refunds,
               (SELECT COUNT(*) FROM chat_conversations c WHERE c.assigned_team_leader_account_id = a.account_id
                    AND c.status IN ('queued','active','awaiting_contact','follow_up')) AS open_conversations
        FROM accounts a
        WHERE a.account_type = 'team_leader'
        ORDER BY a.is_active DESC, a.name, a.account_id;
        """
    ))


def _conversation_metadata_filters(q: str, status: str, leader_id: int | None) -> tuple[str, list]:
    clauses = []
    params: list = []
    if q.strip():
        clauses.append("(c.display_name ILIKE %s OR c.fallback_contact ILIKE %s OR a.name ILIKE %s)")
        params.extend([f"%{q.strip()}%"] * 3)
    if status == "open":
        clauses.append("c.status IN ('queued', 'active', 'awaiting_contact', 'follow_up')")
    elif status in {"queued", "active", "awaiting_contact", "follow_up", "closed", "cancelled"}:
        clauses.append("c.status = %s")
        params.append(status)
    if leader_id is not None:
        clauses.append("c.assigned_team_leader_account_id = %s")
        params.append(leader_id)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def count_conversation_metadata(q: str = "", status: str = "", leader_id: int | None = None) -> int:
    where, params = _conversation_metadata_filters(q, status, leader_id)
    row = fetch_one(
        "SELECT COUNT(*) AS total FROM chat_conversations c "
        "LEFT JOIN accounts a ON a.account_id = c.assigned_team_leader_account_id" + where,
        tuple(params) or None,
    )
    return int(row["total"] if row else 0)


def list_conversation_metadata(
    limit: int = 100, *, q: str = "", status: str = "", leader_id: int | None = None, page: int = 1,
) -> list[dict]:
    where, params = _conversation_metadata_filters(q, status, leader_id)
    return clean_row(fetch_all(
        f"""
        SELECT c.conversation_id, c.display_name,
               c.fallback_contact_type AS contact_type, c.fallback_contact AS contact_value,
               c.status, c.escalation_reason, c.queued_at, c.claimed_at, c.closed_at,
               c.updated_at, c.assigned_team_leader_account_id, a.name AS assigned_team_leader_name,
               COUNT(m.chat_message_id) AS message_count
        FROM chat_conversations c
        LEFT JOIN accounts a ON a.account_id = c.assigned_team_leader_account_id
        LEFT JOIN chat_messages m ON m.conversation_id = c.conversation_id
        {where}
        GROUP BY c.conversation_id, a.name
        ORDER BY c.updated_at DESC, c.conversation_id DESC LIMIT %s OFFSET %s;
        """,
        tuple(params + [limit, (max(1, page) - 1) * limit]),
    ))


def owner_conversation_transcript(conversation_id: str, actor_account_id: int, reason: str) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    conversation = fetch_one("SELECT * FROM chat_conversations WHERE conversation_id = %s;", (conversation_id,))
    if not conversation:
        raise ValueError("Conversation not found.")
    messages = clean_row(fetch_all(
        """
        SELECT chat_message_id, sender_type, content, created_at
        FROM chat_messages WHERE conversation_id = %s ORDER BY chat_message_id;
        """,
        (conversation_id,),
    ))
    record_owner_audit(
        actor_account_id,
        "viewed_conversation_transcript",
        "chat_conversations",
        reason=clean_reason,
        after_state={"conversation_id": conversation_id, "message_count": len(messages)},
    )
    return {"conversation": clean_row(dict(conversation)), "messages": messages}


def list_system_health() -> dict:
    status_rows = clean_row(fetch_all(
        "SELECT component, status, detail, metadata, checked_at FROM system_status_reports ORDER BY component;"
    ))
    outbox = fetch_one(
        """
        SELECT COUNT(*) FILTER (WHERE sent_at IS NULL) AS pending,
               COUNT(*) FILTER (WHERE sent_at IS NULL AND last_error IS NOT NULL) AS failed
        FROM notification_outbox;
        """
    ) or {"pending": 0, "failed": 0}
    forecasts = fetch_one(
        """
        SELECT COUNT(*) FILTER (WHERE status IN ('queued','running','cancelling')) AS active,
               COUNT(*) FILTER (WHERE status = 'failed') AS failed
        FROM forecast_runs;
        """
    ) or {"active": 0, "failed": 0}
    failed_outbox = clean_row(fetch_all(
        """
        SELECT outbox_id, event_type, attempt_count, last_error, created_at
        FROM notification_outbox
        WHERE sent_at IS NULL AND last_error IS NOT NULL
        ORDER BY outbox_id DESC LIMIT 25;
        """
    ))
    components = {row["component"]: row for row in status_rows}
    worker = components.get("worker")
    if worker and worker["checked_at"] < business_now() - timedelta(minutes=3):
        worker["status"] = "warning"
        worker["detail"] = "Worker heartbeat is stale."
    for component, detail in (
        ("worker", "No worker heartbeat has been reported."),
        ("backup", "No backup verification report has been received."),
        ("retention_job", "No retention-job report has been received."),
    ):
        if component not in components:
            components[component] = {
                "component": component,
                "status": "unknown",
                "detail": detail,
                "metadata": {},
                "checked_at": None,
            }
    status_rows = sorted(components.values(), key=lambda item: item["component"])
    migration = fetch_one("SELECT version, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 1;")
    return {
        "components": status_rows,
        "outbox": {key: int(value or 0) for key, value in dict(outbox).items()},
        "forecasts": {key: int(value or 0) for key, value in dict(forecasts).items()},
        "failed_outbox": failed_outbox,
        "latest_migration": clean_row(dict(migration)) if migration else None,
    }


def owner_export_rows(resource: str) -> list[dict]:
    queries = {
        "accounts": """
            SELECT account_id, account_type, team_leader_role, name, email, is_active,
                   activation_status, activated_at FROM accounts ORDER BY account_id;
        """,
        "resellers": """
            SELECT r.reseller_id, r.business_name, r.contact_person, r.email, r.contact_number,
                   r.address, r.reseller_status, r.team_leader_account_id, a.name AS team_leader_name,
                   r.approved_at, r.created_at
            FROM resellers r LEFT JOIN accounts a ON a.account_id = r.team_leader_account_id
            ORDER BY r.reseller_id;
        """,
        "orders": """
            SELECT o.order_id, o.order_type, o.status, o.reseller_id, r.business_name,
                   o.team_leader_account_id, a.name AS team_leader_name, o.total_amount,
                   o.order_date, o.approved_at, o.fulfilled_at
            FROM orders o LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
            LEFT JOIN accounts a ON a.account_id = o.team_leader_account_id ORDER BY o.order_id;
        """,
        "refunds": """
            SELECT order_refund_id, order_id, status, amount, currency,
                   requested_by_account_id, assigned_supervisor_account_id,
                   processing_by_account_id, requested_at, processing_started_at,
                   completed_at, failed_at, updated_at
            FROM order_refunds ORDER BY order_refund_id;
        """,
        "movements": """
            SELECT im.movement_id, im.item_id, i.name AS item_name, im.affected_batch_id,
                   im.production_batch_id, im.order_id, im.movement_type, im.quantity_delta,
                   im.unit, im.balance_before, im.balance_after, im.actor_account_id,
                   im.actor_name, im.note, im.created_at
            FROM inventory_movements im JOIN inventory_items i ON i.item_id = im.item_id
            ORDER BY im.movement_id;
        """,
        "reports": """
            SELECT sr.sales_report_id,sr.report_source,sr.submitted_by_account_id,sr.reseller_id,
                   sr.period_start,sr.period_end,sr.total_sales submitted_sales,sr.total_orders submitted_orders,
                   b.gross_sales,b.refunded_sales,CASE WHEN sr.report_source='reseller' THEN sr.total_sales WHEN b.reconciliation_required THEN NULL ELSE b.retained_sales END total_sales,
                   CASE WHEN b.reconciliation_required THEN NULL ELSE b.retained_sales END retained_sales,b.late_refund_adjustment,b.retained_orders,
                   b.reconciliation_required,sr.notes,sr.submitted_at
            FROM sales_reports sr LEFT JOIN sales_report_balances b USING(sales_report_id) ORDER BY sr.sales_report_id;
        """,
        "forecasts": """
            SELECT forecast_run_id, run_by_account_id, model_name, input_period_start,
                   input_period_end, forecast_horizon_days, status, queued_at, started_at,
                   completed_at, attempt_count, last_error, retry_of_forecast_run_id
            FROM forecast_runs ORDER BY forecast_run_id;
        """,
    }
    query = queries.get(resource)
    if not query:
        raise ValueError("This export is not available.")
    return clean_row(fetch_all(query))


def report_system_status(component: str, status: str, detail: str = "", metadata: dict | None = None) -> None:
    component = component.strip().lower()
    if not 2 <= len(component) <= 80 or not all(character.isalnum() or character in {"_", "-"} for character in component):
        raise ValueError("Invalid system component name.")
    if status not in {"ok", "warning", "error", "unknown"}:
        raise ValueError("Invalid system status.")
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            INSERT INTO system_status_reports (component, status, detail, metadata, checked_at)
            VALUES (%s, %s, NULLIF(%s, ''), %s::jsonb, now())
            ON CONFLICT (component) DO UPDATE SET status = EXCLUDED.status,
                detail = EXCLUDED.detail, metadata = EXCLUDED.metadata, checked_at = now();
            """,
            (component, status, detail.strip()[:1000], json.dumps(metadata or {})),
        )


def acknowledge_alert(alert_id: int, actor_account_id: int, reason: str, resolved: bool = False) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM alerts WHERE alert_id = %s FOR UPDATE;", (alert_id,))
        alert = cur.fetchone()
        if not alert:
            raise ValueError("Alert not found.")
        before = {"status": alert["status"]}
        new_status = "resolved" if resolved else "acknowledged"
        cur.execute("UPDATE alerts SET status = %s WHERE alert_id = %s RETURNING *;", (new_status, alert_id))
        updated = dict(cur.fetchone())
        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="resolved_alert" if resolved else "acknowledged_alert",
            entity_type="alerts",
            entity_id=alert_id,
            reason=clean_reason,
            before_state=before,
            after_state={"status": new_status},
        )
    return clean_row(updated)


__all__ = [
    "SETTING_DEFAULTS",
    "administer_conversation",
    "acknowledge_alert",
    "business_setting",
    "cancel_owner_step_up",
    "confirm_owner_step_up",
    "count_conversation_metadata",
    "list_active_sessions",
    "list_business_settings",
    "list_conversation_metadata",
    "list_security_events",
    "list_system_health",
    "list_team_workload",
    "owner_export_rows",
    "compensate_inventory",
    "owner_conversation_transcript",
    "owner_operations_summary",
    "record_owner_audit",
    "report_system_status",
    "retry_outbox_item",
    "request_owner_step_up",
    "revoke_managed_account_sessions",
    "set_account_active",
    "transfer_team_leader_work",
    "update_business_settings",
]
