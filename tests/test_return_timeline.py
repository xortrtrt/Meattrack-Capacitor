"""Timeline state follows public evidence and existing workflow prerequisites."""
from datetime import datetime
import json

import pytest

from app.return_timeline import overview_stages, workflow_steps


def case(**changes):
    row = dict(status="settlement_pending", resolution="replace", replacement_workflow_version=2,
               created_at=datetime(2026, 11, 2, 9), approved_by_account_id=4,
               verified_at=datetime(2026, 11, 2, 10), sales_authorized=True,
               quote_revision=1, accepted_revision=1, additional_due=100, unused_credit=0,
               has_reservations=False, payment_verified_at=None, has_payment_proof=False,
               replacement_payment_details=None, events=[])
    row.update(changes)
    return row


def indexed(row, inventory=False):
    return {step["key"]: step for step in workflow_steps(row, inventory)}


def test_submission_and_fund_verification_are_separate_steps():
    before = indexed(case())
    assert before["payment_sent"]["state"] == "current"
    assert before["payment_checked"]["state"] == "pending"
    after = indexed(case(has_payment_proof=True, replacement_payment_details=dict(
        transaction_number="EXAMPLE123", payment_amount="100", revision=1)))
    assert after["payment_sent"]["state"] == "done"
    assert after["payment_checked"]["state"] == "current"
    assert after["prepare"]["state"] == "pending"


def test_image_without_current_confirmed_details_does_not_complete_submission():
    for details in (None, dict(transaction_number="EXAMPLE", payment_amount="100", revision=0)):
        step = indexed(case(has_payment_proof=True, replacement_payment_details=details))["payment_sent"]
        assert step["state"] == "current"
        assert step["note"] == "Resubmit confirmed receipt details"


@pytest.mark.parametrize("version", [1, 2])
def test_stock_and_payment_order_matches_existing_versions(version):
    steps = list(indexed(case(replacement_workflow_version=version)))
    if version == 1:
        assert steps.index("reserve") < steps.index("payment_sent")
    else:
        assert steps.index("payment_checked") < steps.index("prepare")


@pytest.mark.parametrize("version", [1, 2])
def test_inventory_exposes_clearance_without_financial_steps_or_values(version):
    steps = indexed(case(replacement_workflow_version=version), True)
    assert steps["clearance"]["state"] == ("pending" if version == 1 else "current")
    serialized = json.dumps(steps, default=str).lower()
    for forbidden in ("payment", "refund", "100", "transaction", "amount"):
        assert forbidden not in serialized
    assert "delivery" not in steps and "payment_checked" not in steps


def test_inventory_release_clearance_stays_pending_until_funds_are_verified():
    pending = indexed(case(replacement_workflow_version=1, has_reservations=True), True)
    assert pending["clearance"]["state"] == "current"
    assert pending["release"]["state"] == "pending"
    ready = indexed(case(replacement_workflow_version=1, has_reservations=True,
                         payment_verified_at=datetime(2026, 11, 2, 11)), True)
    assert ready["release"]["state"] == "current"


def test_inventory_receipt_completion_does_not_claim_refund_completion():
    steps = indexed(case(resolution="refund", credit=200, inventory_status="completed"), True)
    assert steps["complete"]["label"] == "Inventory complete"
    assert steps["complete"]["state"] == "done"
    assert "refund" not in steps


def test_release_opens_delivery_and_difference_refund_together():
    steps = indexed(case(additional_due=0, unused_credit=50,
                         replacement_released_at=datetime(2026, 11, 2, 11)))
    assert steps["delivery"]["state"] == steps["refund"]["state"] == "current"
    assert steps["complete"]["state"] == "pending"


@pytest.mark.parametrize("status", ["completed", "rejected"])
def test_terminal_cases_do_not_assign_future_actions(status):
    steps = indexed(case(status=status))
    assert not any(step["state"] == "current" for step in steps.values())
    if status == "rejected":
        assert steps["payment_sent"]["state"] == "stopped"


def test_mismatch_blocks_later_steps_and_names_the_processor():
    steps = indexed(case(status="discrepancy", verified_at=None))
    assert steps["mismatch"]["state"] == "current"
    assert steps["mismatch"]["owner"] == "Supervisor / Owner"
    assert steps["receipt"]["state"] == "pending"


def test_open_followup_remains_actionable_after_case_completion():
    steps = indexed(case(status="completed", has_open_followup=True,
                         replacement_released_at=datetime(2026, 11, 2, 11)))
    assert steps["followup"]["state"] == "current"
    assert steps["complete"]["state"] == "done"


def test_dates_are_shown_only_for_recorded_completed_milestones():
    now = datetime(2026, 11, 2, 9)
    steps = indexed(case(status="submitted", approved_by_account_id=None, verified_at=None,
                         events=[dict(action="quote_published", created_at=now)]))
    assert steps["requested"]["at"] == now
    assert steps["review"]["at"] is None
    assert steps["review"]["state"] == "current"


def test_overview_keeps_five_groups_without_advancing_pending_work():
    stages = overview_stages(case(status="submitted", approved_by_account_id=None, verified_at=None))
    assert [stage["key"] for stage in stages] == ["submitted", "review", "inventory", "settlement", "completed"]
    assert [stage["state"] for stage in stages] == ["done", "current", "pending", "pending", "pending"]


def test_payment_can_be_current_while_inventory_processing_is_unfinished():
    stages = {stage["key"]: stage for stage in overview_stages(case())}
    assert stages["inventory"]["state"] == stages["settlement"]["state"] == "current"
    assert stages["completed"]["state"] == "pending"


def test_no_financial_obligation_is_labeled_not_required():
    stages = {stage["key"]: stage for stage in overview_stages(case(additional_due=0, unused_credit=0))}
    assert stages["settlement"]["state"] == "not_required"
