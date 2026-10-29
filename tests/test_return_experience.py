from copy import deepcopy

import pytest

from app.return_experience import experience, normalize_queue


BASE = dict(status="settlement_pending", resolution="replace", credit=200, replacement_total=200,
            quote_revision=1, accepted_revision=1, approved_by_account_id=2, verified_at="checked",
            sales_authorized=True, has_reservations=True)


@pytest.mark.parametrize("changes,queue,sales_action,reseller_action,owner", [
    ({"status": "submitted"}, "needs_review", "review", "", "Sales"),
    ({"status": "discrepancy"}, "mismatch", "review", "", "Sales"),
    ({"sales_authorized": False}, "needs_review", "proceed", "", "Sales"),
    ({"accepted_revision": None}, "waiting", "", "accept", "Reseller"),
    ({"verified_at": None}, "waiting", "", "", "Reseller / Inventory"),
    ({"has_reservations": False}, "waiting", "", "", "Inventory"),
    ({"has_reservations": False, "replacement_total": 250, "has_payment_proof": True}, "waiting", "", "", "Inventory"),
    ({"replacement_total": 250}, "waiting", "", "payment", "Reseller"),
    ({"replacement_total": 250, "has_payment_proof": True}, "payment", "verify_payment", "", "Sales"),
    ({"replacement_total": 250, "payment_verified_at": "paid"}, "waiting", "", "", "Inventory"),
    ({"replacement_released_at": "released", "replacement_total": 100}, "waiting", "", "", "Supervisor / Owner"),
    ({"status": "completed", "replacement_released_at": "released"}, "completed", "", "", "No action required"),
    ({"status": "rejected"}, "completed", "", "", "No action required"),
    ({"status": "completed", "has_open_followup": True}, "needs_review", "resolve_issue", "", "Sales"),
    ({"reconciliation_flags": ["Needs checking"]}, "needs_review", "reconcile", "", "Sales"),
])
def test_shared_next_step_resolver(changes, queue, sales_action, reseller_action, owner):
    case = {**deepcopy(BASE), **changes}
    before = deepcopy(case)
    result = experience(case)
    assert result["queue"] == queue
    assert result["sales"]["action"] == sales_action
    assert result["reseller"]["action"] == reseller_action
    assert result["owner"] == owner
    assert case == before


def test_financial_and_product_progress_are_independent():
    value = experience({**BASE, "replacement_released_at": "released", "replacement_total": 100})
    assert value["progress_label"] == "Replacement released · ₱100.00 refund pending"
    value = experience({**BASE, "replacement_released_at": "released", "replacement_total": 100, "refund": {"status": "completed"}})
    assert "₱100.00 refund completed" in value["progress_label"]
    assert normalize_queue("untrusted") == "needs_review" and normalize_queue("all") == "all"
    value = experience({**BASE, "replacement_total": 250, "has_payment_proof": True})
    assert value["financial_progress"] == "₱50.00 payment awaiting verification"
