"""Read-only predicates shared by dashboard counts and their destination lists.

These do not authorize transitions. Existing action handlers remain authoritative.
All SQL fragments are constants; account identifiers remain bound parameters.
"""
from app.return_experience import (
    AUTHORIZED_SQL, FOLLOWUP_SQL, PAYMENT_DETAILS_SQL, PAYMENT_PROOF_SQL,
    QUEUE_SQL, VALID_RESERVATION_SQL, assigned_leader_queue_sql,
)
from .return_views import INVENTORY_STATUS_SQL

RETURN_FROM = """ FROM product_returns r JOIN orders o USING(order_id)
 JOIN product_return_review_reconciliation checks USING(return_id) """
OPEN_RETURNS = f"(r.status NOT IN ('completed','rejected') OR {FOLLOWUP_SQL})"

LABELS = {
    "receipts": "Return receipt checks", "prepare": "Replacement preparation",
    "release": "Replacement releases", "delivery": "Confirm replacement delivery",
    "followups": "Open reseller follow-ups", "customer_action": "Returns needing your action",
}
ALLOWED = {"inventory": {"receipts", "prepare", "release"},
           "sales-supervisor": {"delivery", "followups"}, "reseller": {"customer_action"}}


def return_scope(role: str, actor_id: int) -> tuple[str, list]:
    if role == "reseller":
        return " AND o.created_by_account_id=%s", [actor_id]
    if role == "sales":
        return " AND o.team_leader_account_id=%s", [actor_id]
    if role == "sales-supervisor":
        return """ AND (o.team_leader_account_id=%s OR r.processing_supervisor_account_id=%s
            OR EXISTS(SELECT 1 FROM order_refunds f WHERE f.return_id=r.return_id
                      AND f.assigned_supervisor_account_id=%s))""", [actor_id] * 3
    if role in {"owner", "inventory"}:
        return "", []
    raise ValueError("Your account cannot view return queues.")


BASE_PREPARATION = f"""(r.resolution='replace' AND r.status='settlement_pending'
 AND r.verified_at IS NOT NULL AND r.verified_by_account_id IS NOT NULL
 AND {AUTHORIZED_SQL} AND r.accepted_revision=r.quote_revision
 AND r.replacement_released_at IS NULL)"""

PAYMENT_CLEARED = f"""((r.replacement_total<=r.credit AND r.payment_verified_at IS NULL
 AND COALESCE(r.payment_amount,0)=0) OR (r.replacement_total>r.credit
 AND r.payment_verified_at IS NOT NULL AND r.payment_verified_by_account_id IS NOT NULL
 AND NULLIF(r.payment_reference,'') IS NOT NULL
 AND r.payment_amount=r.replacement_total-r.credit AND {PAYMENT_PROOF_SQL}))"""

PREPARE = f"""({BASE_PREPARATION}
 AND (r.replacement_workflow_version<>2 OR {PAYMENT_CLEARED})
 AND (NOT {VALID_RESERVATION_SQL}
      OR (r.replacement_workflow_version=2 AND r.replacement_prepared_at IS NULL)))"""

RELEASE = f"""({BASE_PREPARATION} AND {VALID_RESERVATION_SQL} AND {PAYMENT_CLEARED}
 AND r.quote_revision>0 AND r.replacement_total=(SELECT SUM(q.line_total)
 FROM replacement_quote_lines q WHERE q.return_id=r.return_id AND q.revision=r.quote_revision)
 AND (r.replacement_workflow_version<>2 OR r.replacement_prepared_at IS NOT NULL))"""

WAITING_STOCK = f"""(r.resolution='replace' AND r.status='settlement_pending'
 AND ({INVENTORY_STATUS_SQL})='awaiting_replacement_release'
 AND NOT {PREPARE} AND NOT {RELEASE})"""

CUSTOMER_ACTION = f"""(r.resolution='replace' AND (
 (r.status IN ('awaiting_receipt','awaiting_inventory_verification','settlement_pending')
  AND r.accepted_revision IS DISTINCT FROM r.quote_revision
  AND EXISTS(SELECT 1 FROM replacement_quote_lines q
             WHERE q.return_id=r.return_id AND q.revision=r.quote_revision))
 OR (r.status='settlement_pending' AND r.verified_at IS NOT NULL AND {AUTHORIZED_SQL}
  AND r.accepted_revision=r.quote_revision AND r.replacement_total>r.credit
  AND r.payment_verified_at IS NULL AND (r.replacement_workflow_version=2 OR {VALID_RESERVATION_SQL})
  AND NOT ({PAYMENT_PROOF_SQL} AND COALESCE({PAYMENT_DETAILS_SQL},false)))))"""


def processing_scope(actor_id: int) -> tuple[str, list]:
    return """(r.processing_supervisor_account_id=%s OR
        (r.handed_off_at IS NULL AND o.team_leader_account_id=%s))""", [actor_id, actor_id]


def dashboard_predicate(role: str, key: str, actor_id: int) -> tuple[str, list]:
    if key not in ALLOWED.get(role, set()):
        raise ValueError("This dashboard queue is not available for your role.")
    if role == "inventory":
        return {
            "receipts": f"({INVENTORY_STATUS_SQL}) IN ('awaiting_receipt','awaiting_inventory_verification')",
            "prepare": PREPARE, "release": RELEASE,
        }[key], []
    if role == "reseller":
        return CUSTOMER_ACTION, []
    scope, params = processing_scope(actor_id)
    condition = FOLLOWUP_SQL if key == "followups" else """(r.replacement_workflow_version=2
        AND r.status<>'rejected' AND r.replacement_released_at IS NOT NULL
        AND r.replacement_delivered_at IS NULL)"""
    return f"({scope} AND {condition})", params


def review_predicate(role: str, key: str, actor_id: int) -> tuple[str, list]:
    sql = assigned_leader_queue_sql() if role == "sales" else QUEUE_SQL
    predicate = f"({sql})=%s"
    params = [key]
    if role == "sales-supervisor":
        scope, scoped_params = processing_scope(actor_id)
        predicate = f"({predicate} AND {scope})"
        params += scoped_params
    return predicate, params
