from datetime import datetime
from decimal import Decimal

import pytest

from app import main  # noqa: F401 -- register template filters
from app.web import templates


def case_data(**overrides):
    case = dict(status="settlement_pending", sales_authorized=True, resolution="replace",
                reseller_name="Example reseller", lines=[dict(quantity=4, received_quantity=4), dict(quantity=2, received_quantity=1)],
                credit=Decimal("865"), replacement_total=Decimal("1931"), additional_due=Decimal("1066"), unused_credit=0,
                has_payment_proof=True, payment_verified_at=None, replacement_payment_details=None, quote_revision=1,
                verified_at=None, has_reservations=True, replacement_prepared_at=None, replacement_released_at=None,
                replacement_delivered_at=None, refund=None, created_at=datetime(2026, 10, 30, 10), updated_at=datetime(2026, 11, 2, 10),
                remarks="Packaging damaged", reconciliation_flags=[],
                experience=dict(sales=dict(title="Your action: review payment", action="verify_payment"), owner="Supervisor / Owner", replacement_progress="Preparing replacement"))
    case.update(overrides)
    return case


def render(**overrides):
    return templates.env.get_template("portals/returns/_supervisor_case_summary.html").render(case=case_data(**overrides))


def test_current_blocker_and_key_facts_are_visible_without_opening_records():
    html = render()
    assert "Waiting for reseller receipt details" in html
    assert "Example reseller" in html
    assert "5 / 6 received" in html
    assert "Awaiting verification" in html
    assert "Reserved" in html
    assert "Packaging damaged" in html
    assert "Return credit" in html and "865.00" in html
    assert "Replacement value" in html and "1,931.00" in html
    assert "Extra payment to verify" in html and "1,066.00" in html
    assert "Submitted" in html and "Updated" in html


@pytest.mark.parametrize("status,title", [("completed", "Return completed"), ("rejected", "Request declined"), ("discrepancy", "Resolve return mismatch")])
def test_historical_proof_does_not_override_current_case_status(status, title):
    html = render(status=status, experience=dict(sales=dict(title=title, action=""), owner="Supervisor", replacement_progress="Recorded"))
    assert title in html
    assert "Waiting for reseller receipt details" not in html


def test_current_receipt_restores_supervisor_action_and_verified_goods_state():
    html = render(replacement_payment_details=dict(transaction_number="REF123", revision=1), verified_at=datetime(2026, 11, 2, 9))
    assert "review payment" in html
    assert "Next action with" in html
    assert "Supervisor / Owner" in html
    assert "Awaiting verification" not in html


def test_refund_summary_does_not_show_replacement_money_fields():
    html = render(resolution="refund", additional_due=0, refund=dict(status="completed", amount=Decimal("865")))
    assert "Refund sent" in html
    assert "Replacement value" not in html
    assert "Extra payment" not in html


def test_long_customer_reason_stays_expandable_and_escaped():
    html = render(remarks="<script>unsafe</script> " * 12)
    assert "<details>" in html
    assert "<script>unsafe</script>" not in html
    assert "&lt;script&gt;" in html


def test_older_case_without_valid_stock_waits_on_inventory_before_resubmission():
    html = render(has_reservations=False, experience=dict(sales=dict(title="Waiting for Inventory", action=""), owner="Inventory", replacement_progress="Awaiting stock"))
    assert "Waiting for Inventory" in html
    assert "Waiting for reseller receipt details" not in html
