from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from psycopg2.errors import UniqueViolation

from app.business_time import business_now

from ._shared import *
from .notifications import _create_notification_cursor, _create_order_system_message_cursor
from .refund_integrity import check_refund_eligibility, fingerprint, replay_refund_operation, record_refund_operation


REFUND_STATUSES = {"pending", "processing", "completed", "failed"}


def _refund_scope(role_key: str, account_id: int | None, alias: str = "r") -> tuple[str, list[object]]:
    if role_key == "owner" and account_id is not None:
        return "", []
    if role_key == "sales-supervisor" and account_id is not None:
        return (
            f" AND ({alias}.assigned_supervisor_account_id = %s "
            f"OR ({alias}.assigned_supervisor_account_id IS NULL "
            f"AND {alias}.status IN ('pending', 'failed')))",
            [account_id],
        )
    if role_key == "team-leader" and account_id is not None:
        return (f" AND EXISTS(SELECT 1 FROM orders sales_order JOIN accounts viewer ON viewer.account_id=%s "
                f"WHERE sales_order.order_id={alias}.order_id AND sales_order.team_leader_account_id=viewer.account_id "
                "AND viewer.account_type='team_leader' AND viewer.team_leader_role='sales' AND viewer.is_active)", [account_id])
    raise ValueError("You are not authorized to access refunds.")


def _clean_text(value: str, label: str, minimum: int, maximum: int) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if len(cleaned) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} characters.")
    if len(cleaned) > maximum:
        raise ValueError(f"{label} must contain no more than {maximum} characters.")
    return cleaned


def _normalize_reference(value: str) -> tuple[str, str]:
    display = _clean_text(value, "Transaction reference", 1, 100)
    key = re.sub(r"[^A-Za-z0-9]", "", display).upper()
    if not 6 <= len(key) <= 64:
        raise ValueError("Transaction reference must contain 6 to 64 letters or numbers.")
    return display, key


def list_order_refunds(
    role_key: str,
    account_id: int | None,
    *,
    status: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 12,
) -> list[dict]:
    scope, params = _refund_scope(role_key, account_id)
    filters = ""
    if status in REFUND_STATUSES:
        filters += " AND r.status = %s"
        params.append(status)
    if q.strip():
        filters += " AND (o.order_id::text ILIKE %s OR reseller.name ILIKE %s OR business.business_name ILIKE %s)"
        term = f"%{q.strip()}%"
        params.extend([term, term, term])
    page_size = min(max(int(page_size), 1), 50)
    offset = max(int(page) - 1, 0) * page_size
    params.extend([page_size, offset])
    return clean_row(fetch_all(
        f"""
        SELECT r.order_refund_id, r.order_id, r.return_id, r.amount, r.currency, r.status,
               CASE WHEN r.return_id IS NULL THEN 'Rejected paid order'
                    WHEN pr.resolution='replace' THEN 'Replacement refund difference' ELSE 'Returned expired products' END refund_type,
               r.assigned_supervisor_account_id, r.processing_by_account_id,
               r.requested_at, r.processing_started_at, r.completed_at, r.failed_at,
               reseller.name AS reseller_name, business.business_name,
               supervisor.name AS assigned_supervisor_name,
               processor.name AS processor_name,
               COALESCE(attempts.attempt_count, 0) AS attempt_count
        FROM order_refunds r
        JOIN orders o ON o.order_id = r.order_id
        LEFT JOIN product_returns pr ON pr.return_id=r.return_id
        LEFT JOIN accounts reseller ON reseller.account_id = o.created_by_account_id
        LEFT JOIN resellers business ON business.reseller_id = o.reseller_id
        LEFT JOIN accounts supervisor ON supervisor.account_id = r.assigned_supervisor_account_id
        LEFT JOIN accounts processor ON processor.account_id = r.processing_by_account_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS attempt_count
            FROM order_refund_attempts a
            WHERE a.order_refund_id = r.order_refund_id
        ) attempts ON true
        WHERE true {scope} {filters}
        ORDER BY CASE r.status WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 WHEN 'processing' THEN 3 ELSE 4 END,
                 r.updated_at DESC, r.order_refund_id DESC
        LIMIT %s OFFSET %s;
        """,
        tuple(params),
    ))


def count_order_refunds(role_key: str, account_id: int | None, *, status: str = "", q: str = "") -> int:
    scope, params = _refund_scope(role_key, account_id)
    filters = ""
    if status in REFUND_STATUSES:
        filters += " AND r.status = %s"
        params.append(status)
    if q.strip():
        filters += " AND (o.order_id::text ILIKE %s OR reseller.name ILIKE %s OR business.business_name ILIKE %s)"
        term = f"%{q.strip()}%"
        params.extend([term, term, term])
    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM order_refunds r
        JOIN orders o ON o.order_id = r.order_id
        LEFT JOIN accounts reseller ON reseller.account_id = o.created_by_account_id
        LEFT JOIN resellers business ON business.reseller_id = o.reseller_id
        WHERE true {scope} {filters};
        """,
        tuple(params),
    )
    return int(row["total"])


def refund_status_counts(role_key: str, account_id: int | None) -> dict[str, int]:
    scope, params = _refund_scope(role_key, account_id)
    rows = fetch_all(
        f"SELECT status, COUNT(*) AS total FROM order_refunds r WHERE true {scope} GROUP BY status;",
        tuple(params),
    )
    counts = {status: 0 for status in REFUND_STATUSES}
    counts.update({str(row["status"]): int(row["total"]) for row in rows})
    return counts


def refund_financial_summary(role_key: str, account_id: int | None) -> dict:
    scope, params = _refund_scope(role_key, account_id)
    return clean_row(fetch_one("SELECT COALESCE(SUM(r.amount) FILTER(WHERE r.status<>'completed'),0) pending_amount, COALESCE(SUM(r.amount) FILTER(WHERE r.status='completed'),0) completed_amount FROM order_refunds r WHERE true " + scope, tuple(params)))


def get_order_refund(refund_id: int, role_key: str, account_id: int | None) -> dict | None:
    scope, params = _refund_scope(role_key, account_id)
    row = fetch_one(
        f"""
        SELECT r.*, o.created_by_account_id AS reseller_account_id, o.rejection_reason,
               o.rejection_payment_received, o.payment_amount,
               reseller.name AS reseller_name, business.business_name,
               requester.name AS requested_by_name,
               supervisor.name AS assigned_supervisor_name,
               processor.name AS processor_name
        FROM order_refunds r
        JOIN orders o ON o.order_id = r.order_id
        LEFT JOIN accounts reseller ON reseller.account_id = o.created_by_account_id
        LEFT JOIN resellers business ON business.reseller_id = o.reseller_id
        LEFT JOIN accounts requester ON requester.account_id = r.requested_by_account_id
        LEFT JOIN accounts supervisor ON supervisor.account_id = r.assigned_supervisor_account_id
        LEFT JOIN accounts processor ON processor.account_id = r.processing_by_account_id
        WHERE r.order_refund_id = %s {scope};
        """,
        tuple([refund_id, *params]),
    )
    if not row:
        return None
    result = clean_row(row)
    result["attempts"] = clean_row(fetch_all(
        """
        SELECT a.*, processor.name AS processor_name,
               p.order_refund_proof_id AS proof_id, p.filename AS proof_filename
        FROM order_refund_attempts a
        LEFT JOIN accounts processor ON processor.account_id = a.processor_account_id
        LEFT JOIN order_refund_proofs p ON p.order_refund_attempt_id = a.order_refund_attempt_id
        WHERE a.order_refund_id = %s
        ORDER BY a.attempt_number ASC;
        """,
        (refund_id,),
    ))
    result["messages"] = clean_row(fetch_all(
        """
        SELECT m.order_refund_message_id, m.order_refund_id, m.sender_account_id,
               m.sender_role, m.message, m.created_at, sender.name AS sender_name
        FROM order_refund_messages m
        JOIN accounts sender ON sender.account_id = m.sender_account_id
        WHERE m.order_refund_id = %s
        ORDER BY m.created_at ASC, m.order_refund_message_id ASC;
        """,
        (refund_id,),
    ))
    if role_key == "team-leader":
        for attempt in result["attempts"]:
            attempt.pop("internal_note", None)
        result["messages"] = []
    result["return_case"] = None
    if result.get("return_id"):
        from .product_returns import get_product_return
        result["return_case"] = get_product_return(result["return_id"], int(account_id))
    result["refund_type"] = ("Rejected paid order" if not result.get("return_id") else
                             "Replacement refund difference" if result["return_case"]["resolution"] == "replace" else "Returned expired products")
    return result


def list_reseller_refund_conversations(account_id: int | None) -> list[dict]:
    if account_id is None:
        return []
    conversations = clean_row(fetch_all(
        """
        SELECT r.order_refund_id, r.order_id, r.status, r.amount, r.currency,
               r.assigned_supervisor_account_id, supervisor.name AS assigned_supervisor_name,
               MAX(m.created_at) AS latest_message_at
        FROM order_refunds r
        JOIN orders o ON o.order_id = r.order_id
        LEFT JOIN accounts supervisor ON supervisor.account_id = r.assigned_supervisor_account_id
        LEFT JOIN order_refund_messages m ON m.order_refund_id = r.order_refund_id
        WHERE o.created_by_account_id = %s
        GROUP BY r.order_refund_id, supervisor.name
        ORDER BY COALESCE(MAX(m.created_at), r.requested_at) DESC;
        """,
        (account_id,),
    ))
    if not conversations:
        return []
    by_refund = {int(item["order_refund_id"]): item for item in conversations}
    rows = clean_row(fetch_all(
        """
        SELECT m.order_refund_message_id, m.order_refund_id, m.sender_account_id,
               m.sender_role, m.message, m.created_at, sender.name AS sender_name
        FROM order_refund_messages m
        JOIN order_refunds r ON r.order_refund_id = m.order_refund_id
        JOIN orders o ON o.order_id = r.order_id
        JOIN accounts sender ON sender.account_id = m.sender_account_id
        WHERE o.created_by_account_id = %s
        ORDER BY m.created_at ASC, m.order_refund_message_id ASC;
        """,
        (account_id,),
    ))
    for item in conversations:
        item["messages"] = []
    for message in rows:
        by_refund[int(message["order_refund_id"])]["messages"].append(message)
    return conversations


def list_order_refund_messages(
    refund_id: int,
    actor_id: int,
    role_key: str,
    *,
    after_id: int = 0,
) -> list[dict]:
    if role_key == "reseller":
        access = fetch_one(
            """
            SELECT r.order_refund_id
            FROM order_refunds r
            JOIN orders o ON o.order_id = r.order_id
            WHERE r.order_refund_id = %s AND o.created_by_account_id = %s;
            """,
            (refund_id, actor_id),
        )
    else:
        scope, params = _refund_scope(role_key, actor_id)
        access = fetch_one(
            f"SELECT r.order_refund_id FROM order_refunds r WHERE r.order_refund_id = %s {scope};",
            tuple([refund_id, *params]),
        )
    if not access:
        raise ValueError("Refund conversation was not found.")
    return clean_row(fetch_all(
        """
        SELECT m.order_refund_message_id, m.order_refund_id, m.sender_account_id,
               m.sender_role, m.message, m.created_at, sender.name AS sender_name
        FROM order_refund_messages m
        JOIN accounts sender ON sender.account_id = m.sender_account_id
        WHERE m.order_refund_id = %s AND m.order_refund_message_id > %s
        ORDER BY m.created_at ASC, m.order_refund_message_id ASC;
        """,
        (refund_id, max(int(after_id), 0)),
    ))


def send_order_refund_message(
    refund_id: int,
    actor_id: int,
    role_key: str,
    message: str,
) -> dict:
    content = str(message or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise ValueError("Enter a message before sending.")
    if len(content) > 1000:
        raise ValueError("Refund messages must contain no more than 1000 characters.")
    if role_key not in {"owner", "sales-supervisor", "reseller"}:
        raise ValueError("You are not authorized to use this refund conversation.")

    with get_transaction_cursor() as cur:
        refund = _locked_refund(cur, refund_id)
        if not refund:
            raise ValueError("Refund case was not found.")
        if refund["status"] == "completed":
            raise ValueError("This refund is completed, so its conversation is now read-only.")
        if role_key == "reseller":
            if int(refund.get("reseller_account_id") or 0) != actor_id:
                raise ValueError("You are not authorized to use this refund conversation.")
        else:
            _authorize_refund(refund, actor_id, role_key)

        cur.execute(
            """
            INSERT INTO order_refund_messages (
                order_refund_id, sender_account_id, sender_role, message
            ) VALUES (%s, %s, %s, %s)
            RETURNING order_refund_message_id, created_at;
            """,
            (refund_id, actor_id, role_key, content),
        )
        inserted = dict(cur.fetchone())
        message_id = int(inserted["order_refund_message_id"])
        _add_log_cursor(
            cur,
            actor_account_id=actor_id,
            action="sent_refund_conversation_message",
            entity_type="order_refund_messages",
            entity_id=message_id,
        )

        if role_key == "reseller":
            assigned_id = refund.get("assigned_supervisor_account_id")
            notification_args = (
                {"recipient_account_id": int(assigned_id)}
                if assigned_id is not None
                else {"recipient_role": "owner"}
            )
            target_role = "sales-supervisor" if assigned_id is not None else "owner"
            _create_notification_cursor(
                cur,
                **notification_args,
                category="order",
                title="Reseller replied about a refund",
                message=f"Order #{refund['order_id']}: {content[:180]}",
                target_url=f"/portal/{target_role}/refunds?refund={refund_id}&pane=conversation",
                source_type="order_refund_messages",
                source_id=message_id,
                dedupe_key=f"refund-message-{message_id}-internal",
            )
        else:
            _create_notification_cursor(
                cur,
                recipient_account_id=int(refund["reseller_account_id"]),
                category="order",
                title="Refund information requested",
                message=f"Order #{refund['order_id']}: {content[:180]}",
                target_url=f"/portal/reseller/inbox?refund={refund_id}#refund-conversation-{refund_id}",
                source_type="order_refund_messages",
                source_id=message_id,
                dedupe_key=f"refund-message-{message_id}-reseller",
            )

        return {
            "order_refund_message_id": message_id,
            "order_refund_id": refund_id,
            "order_id": int(refund["order_id"]),
            "message": content,
            "created_at": inserted["created_at"],
        }


def _locked_refund(cur, refund_id: int) -> dict | None:
    cur.execute("SELECT order_id,return_id FROM order_refunds WHERE order_refund_id=%s;", (refund_id,))
    parent = cur.fetchone()
    if not parent:
        return None
    cur.execute("SELECT order_id FROM orders WHERE order_id=%s FOR UPDATE;", (parent["order_id"],))
    if parent["return_id"]:
        cur.execute("SELECT return_id FROM product_returns WHERE return_id=%s FOR UPDATE;", (parent["return_id"],))
    cur.execute(
        """
        SELECT r.*, o.created_by_account_id AS reseller_account_id
        FROM order_refunds r
        JOIN orders o ON o.order_id = r.order_id
        WHERE r.order_refund_id = %s
        FOR UPDATE OF r;
        """,
        (refund_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _authorize_refund(refund: dict, actor_id: int, role_key: str) -> None:
    if role_key == "owner":
        return
    if role_key == "sales-supervisor" and refund.get("assigned_supervisor_account_id") == actor_id:
        return
    raise ValueError("You are not authorized to process this refund.")


def _create_attempt(cur, refund: dict, actor_id: int) -> int:
    cur.execute(
        "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_attempt FROM order_refund_attempts WHERE order_refund_id = %s;",
        (refund["order_refund_id"],),
    )
    attempt_number = int(cur.fetchone()["next_attempt"])
    cur.execute(
        """
        INSERT INTO order_refund_attempts (order_refund_id, attempt_number, processor_account_id)
        VALUES (%s, %s, %s)
        RETURNING order_refund_attempt_id;
        """,
        (refund["order_refund_id"], attempt_number, actor_id),
    )
    return int(cur.fetchone()["order_refund_attempt_id"])


def _publish_refund_event(
    cur,
    refund: dict,
    *,
    event_type: str,
    title: str,
    message: str,
    dedupe_key: str,
    attempt_id: int | None = None,
    severity: str = "info",
) -> None:
    if refund.get("return_id"):
        from .product_returns import _event
        cur.execute("SELECT r.*,o.created_by_account_id reseller_account_id,o.team_leader_account_id FROM product_returns r JOIN orders o USING(order_id) WHERE return_id=%s;", (refund["return_id"],))
        case = dict(cur.fetchone())
        actor_id = None
        if attempt_id:
            cur.execute("SELECT processor_account_id FROM order_refund_attempts WHERE order_refund_attempt_id=%s;", (attempt_id,))
            actor_id = cur.fetchone()["processor_account_id"]
        _event(cur,case,actor_id,event_type,message,refund_id=refund["order_refund_id"],attempt_id=attempt_id,amount=refund["amount"])
    reseller_id = refund.get("reseller_account_id")
    if reseller_id is None:
        return
    system_message_id = _create_order_system_message_cursor(
        cur,
        reseller_account_id=int(reseller_id),
        order_id=int(refund["order_id"]),
        event_type=event_type,
        title=title,
        message=message,
        dedupe_key=dedupe_key,
        order_refund_attempt_id=attempt_id,
    )
    _create_notification_cursor(
        cur,
        recipient_account_id=int(reseller_id),
        category="order",
        severity=severity,
        title=title,
        message=f"Order #{refund['order_id']}: {message}",
        target_url=f"/portal/reseller/inbox?system_message={system_message_id}#system-message-{system_message_id}",
        source_type="order_system_messages",
        source_id=system_message_id,
        dedupe_key=f"notification-{dedupe_key}",
    )


def start_order_refund(refund_id: int, actor_id: int, role_key: str, operation_key: str | None = None) -> int:
    with get_transaction_cursor() as cur:
        refund = _locked_refund(cur, refund_id)
        if not refund:
            raise ValueError("Refund case was not found.")
        from .product_returns import _actor
        if _actor(cur,actor_id,{"owner", "sales-supervisor"}) != role_key:
            raise ValueError("Your authenticated role cannot process this refund.")
        if role_key == "sales-supervisor" and refund.get("assigned_supervisor_account_id") is None:
            if refund["status"] not in {"pending", "failed"}:
                raise ValueError("This unassigned refund cannot be claimed in its current state.")
            cur.execute(
                """
                UPDATE order_refunds
                SET assigned_supervisor_account_id = %s, updated_at = %s
                WHERE order_refund_id = %s;
                """,
                (actor_id, business_now(), refund_id),
            )
            refund["assigned_supervisor_account_id"] = actor_id
        _authorize_refund(refund, actor_id, role_key)
        request_hash = fingerprint(refund_id,actor_id,"start")
        replay = replay_refund_operation(cur,operation_key,refund_id,actor_id,"start",request_hash)
        if replay is not None:
            return replay
        check_refund_eligibility(cur,refund)
        if refund["status"] == "processing":
            if refund["processing_by_account_id"] == actor_id:
                cur.execute(
                    "SELECT order_refund_attempt_id FROM order_refund_attempts WHERE order_refund_id = %s AND status = 'processing' ORDER BY attempt_number DESC LIMIT 1;",
                    (refund_id,),
                )
                attempt_id = int(cur.fetchone()["order_refund_attempt_id"])
                record_refund_operation(cur,operation_key,refund_id,actor_id,"start",request_hash,attempt_id)
                return attempt_id
            raise ValueError("Another authorized user is already processing this refund.")
        if refund["status"] not in {"pending", "failed"}:
            raise ValueError("Completed refunds cannot be processed again.")
        now = business_now()
        cur.execute(
            """
            UPDATE order_refunds
            SET status = 'processing', processing_by_account_id = %s,
                processing_started_at = %s, failed_at = NULL, updated_at = %s
            WHERE order_refund_id = %s;
            """,
            (actor_id, now, now, refund_id),
        )
        attempt_id = _create_attempt(cur, refund, actor_id)
        record_refund_operation(cur,operation_key,refund_id,actor_id,"start",request_hash,attempt_id)
        cur.execute("SELECT attempt_number FROM order_refund_attempts WHERE order_refund_attempt_id = %s;", (attempt_id,))
        attempt_number = int(cur.fetchone()["attempt_number"])
        _add_log_cursor(cur, actor_account_id=actor_id, action="started_manual_refund", entity_type="order_refunds", entity_id=refund_id)
        _publish_refund_event(
            cur, refund, event_type="refund_processing", title="Processing refund",
            message=f"The manual refund for Order #{refund['order_id']} is being processed.",
            dedupe_key=f"refund-{refund_id}-attempt-{attempt_number}-processing", attempt_id=attempt_id,
        )
        return attempt_id


def fail_order_refund(refund_id: int, actor_id: int, role_key: str, failure_reason: str, operation_key: str | None = None) -> None:
    reason = _clean_text(failure_reason, "Failure reason", 5, 500)
    with get_transaction_cursor() as cur:
        refund = _locked_refund(cur, refund_id)
        if not refund:
            raise ValueError("Refund case was not found.")
        _authorize_refund(refund, actor_id, role_key)
        from .product_returns import _actor
        if _actor(cur,actor_id,{"owner", "sales-supervisor"}) != role_key:
            raise ValueError("Your authenticated role cannot process this refund.")
        request_hash = fingerprint(refund_id,actor_id,reason)
        if replay_refund_operation(cur,operation_key,refund_id,actor_id,"fail",request_hash) is not None:
            return
        if refund["status"] != "processing" or refund["processing_by_account_id"] != actor_id:
            raise ValueError("Only the current processor can mark this refund as failed.")
        cur.execute(
            """
            SELECT order_refund_attempt_id, attempt_number
            FROM order_refund_attempts
            WHERE order_refund_id = %s AND status = 'processing'
            ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE;
            """,
            (refund_id,),
        )
        attempt = cur.fetchone()
        if not attempt:
            raise ValueError("The active refund attempt could not be found.")
        record_refund_operation(cur,operation_key,refund_id,actor_id,"fail",request_hash,attempt["order_refund_attempt_id"])
        now = business_now()
        cur.execute(
            "UPDATE order_refund_attempts SET status = 'failed', failure_reason = %s, failed_at = %s WHERE order_refund_attempt_id = %s;",
            (reason, now, attempt["order_refund_attempt_id"]),
        )
        cur.execute(
            "UPDATE order_refunds SET status = 'failed', failed_at = %s, updated_at = %s WHERE order_refund_id = %s;",
            (now, now, refund_id),
        )
        _add_log_cursor(cur, actor_account_id=actor_id, action="failed_manual_refund", entity_type="order_refunds", entity_id=refund_id)
        _publish_refund_event(
            cur, refund, event_type="refund_failed", title="Refund failed",
            message=f"The refund transfer for Order #{refund['order_id']} was not completed and needs another attempt.",
            dedupe_key=f"refund-{refund_id}-attempt-{attempt['attempt_number']}-failed",
            attempt_id=int(attempt["order_refund_attempt_id"]), severity="critical",
        )


def complete_order_refund(
    refund_id: int,
    actor_id: int,
    role_key: str,
    *,
    refund_method: str,
    transaction_reference: str,
    transferred_at: datetime,
    internal_note: str = "",
    proof: dict | None = None,
    operation_key: str | None = None,
) -> None:
    method = _clean_text(refund_method, "Refund method", 2, 100)
    reference, reference_key = _normalize_reference(transaction_reference)
    note = " ".join(str(internal_note or "").strip().split()) or None
    if note and len(note) > 1000:
        raise ValueError("Internal note must contain no more than 1000 characters.")
    if transferred_at is None:
        raise ValueError("Transfer date and time are required.")
    if transferred_at > business_now():
        raise ValueError("Transfer date and time cannot be in the future.")
    note = _clean_text(internal_note, "Settlement remarks", 5, 1000)
    if not proof:
        raise ValueError("Attach transfer proof before completing the refund.")
    request_hash = fingerprint(refund_id,actor_id,method,reference_key,transferred_at.isoformat(),note,proof["checksum_sha256"])
    try:
        with get_transaction_cursor() as cur:
            refund = _locked_refund(cur, refund_id)
            if not refund:
                raise ValueError("Refund case was not found.")
            _authorize_refund(refund, actor_id, role_key)
            from .product_returns import _actor
            if _actor(cur,actor_id,{"owner", "sales-supervisor"}) != role_key:
                raise ValueError("Your authenticated role cannot process this refund.")
            if replay_refund_operation(cur,operation_key,refund_id,actor_id,"complete",request_hash) is not None:
                return
            check_refund_eligibility(cur,refund)
            if refund["status"] != "processing" or refund["processing_by_account_id"] != actor_id:
                raise ValueError("Only the current processor can complete this refund.")
            cur.execute(
                """
                SELECT order_refund_attempt_id, attempt_number
                FROM order_refund_attempts
                WHERE order_refund_id = %s AND status = 'processing'
                ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE;
                """,
                (refund_id,),
            )
            attempt = cur.fetchone()
            if not attempt:
                raise ValueError("The active refund attempt could not be found.")
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0));", (f"refund-proof:{proof['checksum_sha256']}",))
            cur.execute("SELECT 1 FROM order_refund_proofs p JOIN order_refund_attempts a USING(order_refund_attempt_id) WHERE p.checksum_sha256=%s AND a.status='completed' AND a.order_refund_id<>%s;", (proof["checksum_sha256"],refund_id))
            if cur.fetchone():
                raise ValueError("This transfer proof was already used for another completed refund.")
            record_refund_operation(cur,operation_key,refund_id,actor_id,"complete",request_hash,attempt["order_refund_attempt_id"])
            now = business_now()
            cur.execute(
                """
                UPDATE order_refund_attempts
                SET status = 'completed', refund_method = %s, refund_transaction_number = %s,
                    refund_transaction_key = %s, transferred_at = %s, internal_note = %s,
                    completed_at = %s
                WHERE order_refund_attempt_id = %s;
                """,
                (method, reference, reference_key, transferred_at, note, now, attempt["order_refund_attempt_id"]),
            )
            if proof:
                cur.execute(
                    """
                    INSERT INTO order_refund_proofs (
                        order_refund_attempt_id, uploaded_by_account_id, filename, content_type,
                        content, size_bytes, checksum_sha256
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        attempt["order_refund_attempt_id"], actor_id, proof["filename"], proof["content_type"],
                        proof["content"], proof["size_bytes"], proof["checksum_sha256"],
                    ),
                )
            cur.execute(
                "UPDATE order_refunds SET status = 'completed', completed_at = %s, updated_at = %s WHERE order_refund_id = %s;",
                (now, now, refund_id),
            )
            _add_log_cursor(cur, actor_account_id=actor_id, action="completed_manual_refund", entity_type="order_refunds", entity_id=refund_id)
            if refund.get("return_id"):
                from .product_returns import _complete_if_ready
                _complete_if_ready(cur, refund["return_id"])
            amount = Decimal(str(refund["amount"]))
            _publish_refund_event(
                cur, refund, event_type="refund_completed", title="Refund completed",
                message=f"PHP {amount:,.2f} was refunded for Order #{refund['order_id']}. Reference: {reference}",
                dedupe_key=f"refund-{refund_id}-attempt-{attempt['attempt_number']}-completed",
                attempt_id=int(attempt["order_refund_attempt_id"]),
            )
    except UniqueViolation as exc:
        raise ValueError("That transaction reference has already been used for a completed refund.") from exc


def take_over_order_refund(refund_id: int, owner_account_id: int) -> int:
    with get_transaction_cursor() as cur:
        refund = _locked_refund(cur, refund_id)
        if not refund:
            raise ValueError("Refund case was not found.")
        from .product_returns import _actor
        _actor(cur,owner_account_id,{"owner"})
        check_refund_eligibility(cur,refund)
        if refund["status"] != "processing":
            raise ValueError("Only a refund currently being processed can be taken over.")
        if refund["processing_by_account_id"] == owner_account_id:
            raise ValueError("You are already processing this refund.")
        cur.execute(
            """
            SELECT order_refund_attempt_id
            FROM order_refund_attempts
            WHERE order_refund_id = %s AND status = 'processing'
            ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE;
            """,
            (refund_id,),
        )
        old_attempt = cur.fetchone()
        now = business_now()
        if old_attempt:
            cur.execute(
                "UPDATE order_refund_attempts SET status = 'failed', failure_reason = %s, failed_at = %s WHERE order_refund_attempt_id = %s;",
                ("Processing was taken over by an owner.", now, old_attempt["order_refund_attempt_id"]),
            )
        cur.execute(
            "UPDATE order_refunds SET status = 'failed', failed_at = %s, updated_at = %s WHERE order_refund_id = %s;",
            (now, now, refund_id),
        )
        cur.execute(
            """
            UPDATE order_refunds SET status = 'processing', processing_by_account_id = %s,
                processing_started_at = %s, failed_at = NULL, updated_at = %s
            WHERE order_refund_id = %s;
            """,
            (owner_account_id, now, now, refund_id),
        )
        attempt_id = _create_attempt(cur, refund, owner_account_id)
        _publish_refund_event(cur,refund,event_type="refund_processing",title="Refund processing reassigned",message=f"The owner took over processing of Refund #{refund_id}.",dedupe_key=f"refund-{refund_id}-takeover-{attempt_id}",attempt_id=attempt_id)
        _add_log_cursor(cur, actor_account_id=owner_account_id, action="took_over_manual_refund", entity_type="order_refunds", entity_id=refund_id)
        return attempt_id


def get_order_refund_proof(proof_id: int, role_key: str, account_id: int | None) -> dict | None:
    if account_id is None or role_key not in {"owner", "sales-supervisor", "reseller", "team-leader"}:
        return None
    if role_key == "team-leader":
        scope, params = _refund_scope(role_key,account_id)
        return fetch_one("SELECT p.* FROM order_refund_proofs p JOIN order_refund_attempts a USING(order_refund_attempt_id) JOIN order_refunds r USING(order_refund_id) WHERE p.order_refund_proof_id=%s " + scope, (proof_id,*params))
    access = "o.created_by_account_id = %s" if role_key == "reseller" else (
        "r.assigned_supervisor_account_id = %s" if role_key == "sales-supervisor" else "%s IS NOT NULL"
    )
    return fetch_one(
        f"""
        SELECT p.order_refund_proof_id, p.filename, p.content_type, p.content, p.size_bytes
        FROM order_refund_proofs p
        JOIN order_refund_attempts a ON a.order_refund_attempt_id = p.order_refund_attempt_id
        JOIN order_refunds r ON r.order_refund_id = a.order_refund_id
        JOIN orders o ON o.order_id = r.order_id
        WHERE p.order_refund_proof_id = %s AND {access};
        """,
        (proof_id, account_id),
    )


__all__ = [
    "REFUND_STATUSES", "list_order_refunds", "count_order_refunds", "refund_status_counts",
    "get_order_refund", "start_order_refund", "fail_order_refund", "complete_order_refund",
    "take_over_order_refund", "get_order_refund_proof", "list_reseller_refund_conversations",
    "send_order_refund_message", "list_order_refund_messages",
    "refund_financial_summary",
]
