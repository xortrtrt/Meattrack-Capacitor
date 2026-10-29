"""Inventory reads the custody record; Sales reads its financial settlement."""

from __future__ import annotations


INVENTORY_STATUS_SQL = """CASE
 WHEN r.status='rejected' THEN 'rejected'
 WHEN r.approved_by_account_id IS NULL THEN 'submitted'
 WHEN r.status='discrepancy' THEN 'discrepancy'
 WHEN EXISTS(SELECT 1 FROM product_return_lines l WHERE l.return_id=r.return_id AND l.received_quantity<l.quantity)
  THEN 'awaiting_receipt'
 WHEN r.verified_at IS NULL OR r.verified_by_account_id IS NULL THEN 'awaiting_inventory_verification'
 WHEN r.resolution='replace' AND r.replacement_released_at IS NULL THEN 'awaiting_replacement_release'
 ELSE 'completed' END"""

SALES_STAGE_SQL = """CASE WHEN r.status='settlement_pending' AND (
 r.settlement_authorized_at IS NULL OR r.settlement_authorized_quote_revision IS DISTINCT FROM r.quote_revision)
 THEN 'ready_for_confirmation' ELSE r.status END"""

INVENTORY_EVENT_MESSAGES = {
    "submitted": "A return was requested against the original delivery allocations.",
    "sales_approved": "Sales approved the returned products and quantities.",
    "rejected": "Sales did not approve this return request.",
    "inventory_received": "Returned goods were received into holding.",
    "inventory_receipt_complete": "All approved products and proof matched. Complete receipt confirmed into Discarded inventory; the approved resolution may proceed.",
    "inventory_verified": "Inventory verified the received goods and custody records.",
    "inventory_reverified": "Inventory rechecked the custody records for this completed return.",
    "receipt_discrepancy": "Returned goods require a quantity, batch, or condition review.",
    "quote_accepted": "The reseller accepted the replacement products and quantities.",
    "replacement_reserved": "Valid replacement stock was reserved.",
    "replacement_released": "Replacement stock was released against this return.",
    "payment_verified": "Sales clearance was updated for replacement release.",
    "settlement_authorized": "Sales authorized the next step for this verified return.",
}

RESELLER_EVENT_MESSAGES = {
    "replacement_received": "You acknowledged receiving your replacement.",
    "customer_issue_reported": "Your replacement issue was sent to Sales for follow-up.",
    "customer_issue_resolved": "Sales provided a resolution for your reported issue.",
    "rejected": "Sales declined this return request.",
    "submitted": "Your return request was submitted for Sales review.",
    "sales_approved": "Sales approved the return. Send back the approved products and quantities.",
    "inventory_received": "A delivery of your returned items was received into non-sellable holding.",
    "inventory_receipt_complete": "All approved returned products matched and were received. Your approved refund or replacement can proceed.",
    "inventory_verified": "All approved returned items were received and verified. Awaiting Sales confirmation.",
    "inventory_reverified": "Inventory rechecked the goods records for your completed return. Your recorded outcome remains complete.",
    "receipt_discrepancy": "The received items need further review. Sales will contact you about the next step.",
    "quote_accepted": "You accepted the current replacement offer.",
    "settlement_authorized": "Sales confirmed proceeding with your approved resolution.",
    "replacement_reserved": "Your replacement products are being prepared.",
    "replacement_released": "Your replacement products were released. Their batch details are available below.",
    "payment_uploaded": "Your additional-payment proof was submitted for verification.",
    "payment_verified": "Your additional payment was verified.",
    "refund_processing": "Your refund transfer is being processed.",
    "refund_failed": "The refund transfer needs another attempt. Your refund remains pending.",
    "refund_completed": "Your refund transfer was completed.",
    "completed": "Your return and its approved resolution are complete.",
}

INVENTORY_FLAGS = {
    "No traceable approved return lines.",
    "Returned quantities and receipt/holding records do not reconcile.",
    "Claims exceed the fulfilled batch allocation.",
    "Received goods are missing matching receipt records.",
    "Holding balance or receipt linkage requires review.",
    "Inventory verification is missing complete approved receipts.",
    "Final Inventory verification requires a named verifier.",
}


def _pick(row: dict, keys: str) -> dict:
    return {key: row[key] for key in keys.split() if key in row}


def inventory_return_view(case: dict) -> dict:
    """An allowlist prevents new financial fields from leaking into Inventory."""
    result = _pick(case, """return_id order_id resolution status inventory_status requested_by_account_id
        approved_by_account_id verified_at verified_by_account_id verified_by_name verification_remarks
        replacement_released_at quote_revision accepted_revision has_reservations reseller_name
        team_leader_account_id created_at updated_at list_total list_page requested_quantity received_quantity
        inventory_can_reserve inventory_can_release inventory_can_confirm_receipt sales_authorized receipt_confirmed receipt_review_version""")
    result["status"] = case["inventory_status"]
    for key, fields in {
        "lines": "return_line_id allocation_id batch_id product_id name batch_code expiry_date quantity received_quantity",
        "quote_lines": "quote_line_id product_id name quantity revision",
        "receipts": "receipt_id return_id location remarks received_at received_by_name received_by_account_id",
        "receipt_lines": "receipt_line_id receipt_id return_line_id holding_id movement_id quantity location quantity_remaining source_batch_code name expiry_date created_at disposed_quantity",
        "custody_movements": "movement_id holding_id action quantity actor_name created_at remarks disposal_method",
        "reserved_batches": "batch_id batch_code product_id name expiry_date quantity eligible",
        "released_batches": "release_id batch_id batch_code product_id name expiry_date quantity released_by_name released_at movement_id",
    }.items():
        if key in case:
            result[key] = [_pick(row, fields) for row in case[key]]
    result["events"] = [
        {**_pick(event, "event_id action actor_name created_at"), "remarks": INVENTORY_EVENT_MESSAGES[event["action"]]}
        for event in case.get("events", [])
        if event["action"] in INVENTORY_EVENT_MESSAGES and event["action"] != "payment_verified"
    ]
    result["attachments"] = [
        _pick(row, "attachment_id purpose filename") for row in case.get("attachments", []) if row["purpose"] == "evidence"
    ]
    result["reconciliation_flags"] = [flag for flag in case.get("reconciliation_flags", []) if flag in INVENTORY_FLAGS]
    return result


def reseller_return_view(case: dict) -> dict:
    result = _pick(case, """return_id order_id resolution status remarks credit replacement_total quote_revision
        accepted_revision verified_at replacement_released_at payment_verified_at payment_amount
        created_at completed_at updated_at has_reservations list_total list_page requested_quantity received_quantity
        additional_due unused_credit sales_authorized experience replacement_received_at has_open_followup
        has_payment_proof offer_snapshot offer_changes previous_offer""")
    result["followups"] = [_pick(row, "followup_id message status resolution_message created_at resolved_at messages") for row in case.get("followups", [])]
    status = case["status"]
    if status == "completed":
        label = "Completed"
    elif status == "rejected":
        label = "Request declined"
    elif status == "submitted":
        label = "Under Sales review"
    elif status == "discrepancy":
        label = "Items under review"
    elif not case.get("verified_at"):
        label = "Checking received items" if status == "awaiting_inventory_verification" else "Awaiting returned items"
    elif not case.get("sales_authorized"):
        label = "Awaiting Sales confirmation"
    elif case["resolution"] == "refund":
        label = "Refund in progress" if (case.get("refund") or {}).get("status") == "processing" else "Refund pending transfer"
    elif case.get("replacement_released_at"):
        label = "Replacement released—refund pending"
    elif case["accepted_revision"] != case["quote_revision"]:
        label = "Review replacement offer"
    elif not case.get("has_reservations"):
        label = "Preparing valid replacement stock"
    elif case.get("additional_due", 0) > 0 and case.get("has_payment_proof") and not case.get("payment_verified_at"):
        label = "Payment proof submitted"
    elif case.get("additional_due", 0) > 0 and case.get("has_reservations") and not case.get("payment_verified_at"):
        label = "Additional payment required"
    else:
        label = "Preparing replacement"
    result["customer_status_label"] = label
    result["needs_review"] = bool(case.get("reconciliation_flags"))
    for key, fields in {
        "lines": "return_line_id allocation_id name batch_code expiry_date quantity received_quantity unit_price",
        "quote_lines": "product_id name quantity unit_price line_total revision",
        "attachments": "attachment_id purpose revision filename",
        "released_batches": "batch_code name expiry_date quantity released_at",
    }.items():
        if key in case:
            result[key] = [_pick(row, fields) for row in case[key]]
    refund = case.get("refund")
    result["refund"] = _pick(refund, "order_refund_id amount status completed_at") if refund else None
    result["events"] = [
        {**_pick(event, "action created_at"), "remarks": event.get("customer_message")
         or RESELLER_EVENT_MESSAGES.get(event["action"], "Sales updated your return.")}
        for event in case.get("events", []) if event.get("customer_message") or event["action"] in RESELLER_EVENT_MESSAGES
    ]
    return result


def inventory_context_view(value):
    """Strip catalog prices and sales metrics from Inventory template context."""
    financial_keys = {
        "base_price", "unit_price", "line_total", "total_amount", "fulfilled_sales", "total_sales",
        "credit", "replacement_total", "payment_amount", "payment_reference", "refund", "refund_status",
        "order_refund_id", "order_total", "additional_due", "unused_credit", "amount",
    }
    if isinstance(value, dict):
        return {key: inventory_context_view(item) for key, item in value.items() if key not in financial_keys}
    if isinstance(value, list):
        return [inventory_context_view(item) for item in value]
    return value
