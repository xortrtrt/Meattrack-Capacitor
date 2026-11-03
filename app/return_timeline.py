"""Public return milestones, derived from recorded facts rather than stage prose."""
from decimal import Decimal

from app.return_experience import payment_submission_ready


def workflow_steps(case: dict, inventory_only: bool = False) -> list[dict]:
    status = case.get("status", "submitted")
    replacement = case.get("resolution") == "replace"
    modern = case.get("replacement_workflow_version") == 2
    processor = "Supervisor / Owner"
    released = bool(case.get("replacement_released_at"))
    verified = bool(case.get("verified_at"))
    authorized = bool(case.get("sales_authorized"))
    due = Decimal(str(case.get("additional_due") or 0))
    refund_due = Decimal(str(case.get("unused_credit") or 0)) if replacement else Decimal(str(case.get("refund_due", case.get("credit")) or 0))
    steps: list[dict] = []

    def event_time(*actions):
        return next((event.get("created_at") for event in reversed(case.get("events", []))
                     if event.get("action") in actions), None)

    def add(key, label, owner, done, at=None, note="", requires=None):
        steps.append(dict(key=key, label=label, owner=owner, done=bool(done), at=at,
                          note=note, requires=[step["key"] for step in steps] if requires is None else requires))

    add("requested", "Request submitted", "Reseller", True, case.get("created_at"))
    if case.get("reconciliation_flags"):
        add("record_review", "Review return records", "Inventory / Supervisor" if inventory_only else processor, False)
    approved = case.get("approved_by_account_id") or ("approved_by_account_id" not in case and status not in {"submitted", "rejected"})
    add("review", "Review request", processor if modern else "Sales", approved,
        event_time("sales_approved", "approved"))
    if status == "discrepancy":
        add("mismatch", "Resolve receipt mismatch", processor, False)
    revision = case.get("quote_revision", 0)
    if replacement and (not modern or revision > 1):
        add("accept", "Accept replacement offer", "Reseller",
            revision and case.get("accepted_revision") == revision, event_time("quote_accepted"))
    add("receipt", "Receive and check returned goods", "Inventory", verified,
        case.get("verified_at"))
    if replacement:
        if not modern:
            add("authorize", "Clear replacement", processor, authorized, case.get("settlement_authorized_at"))
            add("reserve", "Reserve replacement stock", "Inventory", case.get("has_reservations") or released,
                event_time("replacement_reserved"))
        elif verified and not authorized:
            add("authorize", "Clear replacement", processor, False)
        if inventory_only:
            add("clearance", "Clear next stock action", processor,
                authorized and (due <= 0 or case.get("payment_verified_at"))
                if not case.get("inventory_can_release") else True)
        elif due > 0:
            submitted = payment_submission_ready(case) or case.get("payment_verified_at")
            add("payment_sent", "Submit additional-payment receipt", "Reseller", submitted,
                event_time("payment_uploaded") if submitted else None,
                "Resubmit confirmed receipt details" if case.get("has_payment_proof") and not submitted else "")
            add("payment_checked", "Verify funds received", processor, case.get("payment_verified_at"),
                case.get("payment_verified_at"))
        if modern:
            add("prepare", "Prepare replacement stock", "Inventory",
                (case.get("replacement_prepared_at") and case.get("has_reservations")) or released,
                case.get("replacement_prepared_at"))
        add("release", "Release replacement", "Inventory", released, case.get("replacement_released_at"))
        if modern and not inventory_only:
            add("delivery", "Confirm delivery", processor, case.get("replacement_delivered_at"),
                case.get("replacement_delivered_at"))
    elif not inventory_only:
        add("authorize", "Clear refund", processor, authorized, case.get("settlement_authorized_at"))
    if not inventory_only and refund_due > 0:
        refund = case.get("refund") or {}
        add("refund", "Send difference refund" if replacement else "Send refund", processor,
            refund.get("status") == "completed", refund.get("completed_at"),
            requires=["release"] if replacement else None)
    if not inventory_only and case.get("has_open_followup"):
        add("followup", "Resolve reported issue", processor, False,
            requires=["release"] if replacement and released else ["requested"])
    inventory_done = case.get("inventory_status") == "completed" or (released if replacement else verified)
    add("complete", "Inventory complete" if inventory_only else "Return complete", "Inventory" if inventory_only else processor,
        inventory_done if inventory_only else status == "completed", case.get("completed_at"))
    completed = {step["key"] for step in steps if step["done"]}
    for step in steps:
        active = (not step["done"] and set(step.pop("requires")).issubset(completed)
                  and (status not in {"rejected", "completed"} or step["key"] in {"record_review", "followup"}))
        step["state"] = "done" if step.pop("done") else "current" if active else "pending"
        if status == "rejected" and step["state"] != "done":
            step["state"] = "declined" if step["key"] == "review" else "stopped"
        if step["state"] != "done":
            step["at"] = None
        if step["state"] != "current":
            step["note"] = ""
    return steps


def overview_stages(case: dict) -> list[dict]:
    """Group actual milestones; interleaved inventory/settlement may both be active."""
    steps = case.get("workflow_steps") or workflow_steps(case)
    groups = [
        ("submitted", "Submitted", {"requested"}),
        ("review", "Supervisor review" if case.get("replacement_workflow_version") == 2 else "Sales review", {"review", "record_review", "mismatch"}),
        ("inventory", "Inventory processing", {"accept", "receipt", "reserve", "prepare", "release", "delivery"}),
        ("settlement", "Settlement", {"authorize", "payment_sent", "payment_checked", "refund"}),
        ("completed", "Completed", {"complete", "followup"}),
    ]
    result = []
    for key, label, members in groups:
        items = [step for step in steps if step["key"] in members]
        states = {step["state"] for step in items}
        state = ("not_required" if not items else "done" if states == {"done"}
                 else "declined" if "declined" in states else "stopped" if "stopped" in states
                 else "current" if "current" in states or ("done" in states and key != "completed") else "pending")
        at = next((step["at"] for step in reversed(items) if step.get("at")), None) if state == "done" else None
        result.append(dict(key=key, label=label, state=state, at=at))
    return result
