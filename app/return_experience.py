"""Role-specific next actions, separate from inventory and settlement state."""
from decimal import Decimal

QUEUES = (
    ("all", "All"), ("needs_review", "Needs review"), ("mismatch", "Return mismatch"),
    ("payment", "Payment verification"), ("waiting", "Waiting on others"), ("completed", "Completed"),
)

PAYMENT_PROOF_SQL = """EXISTS(SELECT 1 FROM return_attachments proof
 WHERE proof.return_id=r.return_id AND proof.purpose='payment' AND proof.revision=r.quote_revision)"""
FOLLOWUP_SQL = "EXISTS(SELECT 1 FROM return_customer_followups followup WHERE followup.return_id=r.return_id AND followup.status='open')"
AUTHORIZED_SQL = "(r.settlement_authorized_by_account_id IS NOT NULL AND r.settlement_authorized_at IS NOT NULL AND r.settlement_authorized_quote_revision IS NOT DISTINCT FROM r.quote_revision)"
VALID_RESERVATION_SQL = """(EXISTS(SELECT 1 FROM replacement_quote_lines q WHERE q.return_id=r.return_id AND q.revision=r.quote_revision)
 AND NOT EXISTS(SELECT 1 FROM replacement_quote_lines q
 LEFT JOIN inventory_batches b ON b.item_id=q.product_id
 LEFT JOIN replacement_stock_reservations s ON s.batch_id=b.batch_id AND s.return_id=q.return_id
 WHERE q.return_id=r.return_id AND q.revision=r.quote_revision GROUP BY q.product_id,q.quantity
 HAVING COALESCE(SUM(s.quantity) FILTER(WHERE b.quality_status='approved'
 AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date AND b.quantity_available>=s.quantity),0)<>q.quantity))"""
QUEUE_SQL = f"""CASE
 WHEN r.status='discrepancy' THEN 'mismatch'
 WHEN {FOLLOWUP_SQL} OR r.status='submitted'
   OR (r.status='settlement_pending' AND r.verified_at IS NOT NULL AND NOT {AUTHORIZED_SQL})
   OR cardinality(checks.flags)>0 THEN 'needs_review'
 WHEN r.status IN ('completed','rejected') THEN 'completed'
 WHEN r.resolution='replace' AND r.status='settlement_pending' AND {AUTHORIZED_SQL}
   AND r.accepted_revision=r.quote_revision AND r.replacement_total>r.credit
   AND r.payment_verified_at IS NULL AND {VALID_RESERVATION_SQL} AND {PAYMENT_PROOF_SQL} THEN 'payment'
 ELSE 'waiting' END"""


def normalize_queue(value: str | None) -> str:
    return value if value in dict(QUEUES) else "needs_review"


def experience(case: dict) -> dict:
    """Only public facts enter this payload; never include raw internal remarks."""
    status = case.get("status", "submitted")
    replace = case.get("resolution") == "replace"
    released = bool(case.get("replacement_released_at"))
    credit = Decimal(str(case.get("credit") or 0))
    total = Decimal(str(case.get("replacement_total") or 0))
    due = max(Decimal(0), total-credit) if replace else Decimal(0)
    refund_due = max(Decimal(0), credit-total) if replace else credit
    refund_status = (case.get("refund") or {}).get("status") or case.get("refund_status")
    refund_complete = refund_status == "completed"
    money = (f"₱{refund_due:,.2f} refund {'completed' if refund_complete else 'pending'}" if refund_due
             else f"₱{due:,.2f} additional payment verified" if case.get("payment_verified_at")
             else f"₱{due:,.2f} payment awaiting verification" if due and case.get("has_payment_proof")
             else f"₱{due:,.2f} additional payment" if due else "No additional payment")
    product = ("Replacement released" if released else "Preparing replacement" if replace and case.get("verified_at")
               else "Awaiting returned goods" if case.get("approved_by_account_id") else "Under review")
    if not replace and case.get("verified_at"):
        product = "Returned goods verified"
    if status == "rejected":
        product, money = "Request declined", "No settlement"
    elif status == "submitted":
        money = "Outcome awaiting Sales review"
    elif refund_due and not refund_status and not released:
        money = f"₱{refund_due:,.2f} refund difference" if replace else f"₱{refund_due:,.2f} approved refund · awaiting goods verification"
    sales_action, reseller_action, owner = "", "", "Sales"
    sales_title, reseller_title = "Waiting for Sales review", "Waiting for Sales review"
    description = "Sales is reviewing the request and supporting photos."
    if status == "discrepancy":
        sales_action, sales_title = "review", "Your action: resolve the return mismatch"
        reseller_title, description = "Sales is reviewing a return mismatch", "Wait for Sales to explain the problem and next steps."
    elif case.get("has_open_followup"):
        sales_action, sales_title = "resolve_issue", "Your action: resolve the reseller’s reported issue"
        reseller_title, description = "Your issue is with Sales", "Your reported problem remains open. Sales will help with the next step."
    elif status in {"completed", "rejected"}:
        owner = "No action required"
        sales_title = reseller_title = "Return completed" if status == "completed" else "Request declined"
        description = "Review the outcome below. You can contact Sales if you need help."
    elif status == "submitted":
        sales_action, sales_title = "review", "Your action: review this return request"
    elif case.get("verified_at") and not case.get("sales_authorized"):
        sales_action, sales_title = "proceed", "Your action: confirm the approved resolution"
        reseller_title, description = "Waiting for Sales confirmation", "Inventory has checked the returned goods. Sales must clear the next step."
    elif replace and case.get("accepted_revision") != case.get("quote_revision"):
        owner, reseller_action = "Reseller", "accept"
        sales_title, reseller_title = "Waiting for reseller acceptance", "Your action: review the replacement offer"
        description = "Review the products, quantities, and price difference before accepting."
    elif not case.get("verified_at"):
        owner = "Reseller / Inventory"
        sales_title, reseller_title = "Waiting for returned goods and Inventory’s check", "Return the approved products for checking"
        description = "Send back the approved packs. Inventory will confirm the complete matching receipt."
    elif released or not replace:
        owner = "Supervisor / Owner"
        sales_title = reseller_title = "Waiting for the refund transfer"
        description = "The authorized processor must complete the refund. Sales can follow its progress."
    elif not case.get("has_reservations"):
        owner = "Inventory"
        sales_title = reseller_title = "Waiting for Inventory to reserve valid replacement stock"
        description = "Inventory is preparing eligible stock. Additional payment is requested after valid stock is reserved."
    elif due and not case.get("payment_verified_at") and case.get("has_payment_proof"):
        sales_action, sales_title = "verify_payment", "Your action: review the reseller’s payment proof"
        reseller_title, description = "Payment proof submitted—waiting for Sales verification", "Sales will verify the submitted proof before replacement release."
    elif due and not case.get("payment_verified_at"):
        owner, reseller_action = "Reseller", "payment"
        sales_title, reseller_title = "Waiting for the reseller’s additional payment", "Your action: send additional-payment proof"
        description = f"Valid replacement stock is reserved. Pay ₱{due:,.2f} and upload your proof."
    else:
        owner = "Inventory"
        sales_title = reseller_title = "Waiting for Inventory to release the replacement"
        description = "The replacement is cleared and reserved for release."
    if case.get("reconciliation_flags") and not sales_action:
        sales_title, sales_action, owner = "Your action: review this case’s recorded details", "reconcile", "Sales"
    queue = ("mismatch" if status == "discrepancy" else "needs_review" if sales_action in {"review", "resolve_issue", "proceed", "reconcile"}
             else "completed" if status in {"completed", "rejected"} else "payment" if sales_action == "verify_payment" else "waiting")
    sales_description = {
        "review": ("Review Inventory’s mismatch and provide corrected instructions for the reseller." if status == "discrepancy"
                   else "Check the requested products and supporting photos, then record your decision."),
        "resolve_issue": "Review the reseller’s message and provide a customer-facing resolution.",
        "proceed": "The returned goods are verified. Confirm the approved resolution.",
        "verify_payment": "Compare the current offer and payment proof, then verify the amount and transaction reference.",
        "reconcile": "Review the recorded details and the highlighted inconsistencies before continuing.",
    }.get(sales_action, description)
    if not sales_action and owner == "Reseller":
        sales_description = "The reseller must accept the current offer." if reseller_action == "accept" else "Valid stock is reserved. Waiting for the reseller’s additional-payment proof."
    elif not sales_action and owner == "Reseller / Inventory":
        sales_description = "Waiting for the reseller to return the approved goods and Inventory to check the complete receipt."
    return {"queue": queue, "owner": owner, "replacement_progress": product, "financial_progress": money,
            "progress_label": f"{product} · {money}", "sales": {"title": sales_title, "action": sales_action, "description": sales_description},
            "reseller": {"title": reseller_title, "action": reseller_action, "description": description}, "description": description}
