"""Supervisor cards retain the review, settlement, and delivery actions."""
from datetime import datetime

from test_sales_case_cards import render


def test_submitted_request_has_one_timeline_and_one_visible_comparison():
    html = render(role="sales-supervisor")
    assert "data-return-details" in html and "rd-overview-grid" in html
    assert html.count('class="return-workflow"') == 1
    assert html.count('data-overview-stage=') == 5
    assert 'form="return-review-approve"' in html
    assert "Review decision" in html and "Approve return request" in html
    assert "Reject request" in html and 'name="customer_message" required' in html
    assert "Approval and Inventory handoff" not in html
    assert "Price breakdown &amp; delivery" not in html
    assert "/portal/sales-supervisor/orders?q=6" in html
    assert "/portal/team-leader/messages" not in html


def test_legacy_payment_stays_blocked_until_inventory_has_valid_stock():
    html = render(role="sales-supervisor", replacement_workflow_version=1,
                  status="settlement_pending", sales_authorized=True, has_payment_proof=True,
                  replacement_payment_details=None, has_reservations=False)
    assert "Awaiting Inventory" in html
    assert 'name="funds_received"' not in html
    assert 'name="transaction_number"' not in html and 'name="payment_amount"' not in html


def test_current_payment_review_keeps_reseller_values_read_only_and_binds_version():
    html = render(role="sales-supervisor", status="settlement_pending",
                  verified_at=datetime(2026, 11, 2, 10), sales_authorized=True,
                  has_payment_proof=True, payment_review_version="reviewed-proof-version",
                  experience=dict(sales=dict(action="verify_payment")),
                  replacement_payment_details=dict(transaction_number="EXAMPLE123", payment_amount="100",
                                                   revision=1, payment_date="2026-11-02", payment_time="10:00"))
    assert "EXAMPLE123" in html and "PHP 100.00" in html
    assert 'name="payment_review_version" value="reviewed-proof-version"' in html
    assert 'name="funds_received"' in html and "Confirm funds received" in html
    assert 'data-return-panel="settlement"' in html
    assert 'name="transaction_number"' not in html and 'name="payment_amount"' not in html


def test_released_request_exposes_delivery_instead_of_another_approval():
    html = render(role="sales-supervisor", status="settlement_pending", approved_by_account_id=4,
                  replacement_released_at=datetime(2026, 11, 2, 10))
    assert "Confirm replacement delivery" in html and 'action="/portal/sales-supervisor/returns/actions/delivered"' in html
    assert 'action="/portal/sales-supervisor/returns/actions/approve"' not in html
    assert "Open replacement conversation" in html


def test_historical_authorization_action_keeps_its_operation_key():
    html = render(role="sales-supervisor", authorize=True, status="settlement_pending",
                  verified_at=datetime(2026, 11, 2, 10), resolution="refund")
    assert 'data-return-intent="proceed"' in html
    assert 'action="/portal/sales-supervisor/returns/actions/proceed"' in html
    assert 'name="operation_key" value="test-operation"' in html
    assert "Proceed with refund" in html


def test_processed_request_has_a_recorded_decision_without_approval_buttons():
    html = render(role="sales-supervisor", status="completed", approved_by_account_id=4)
    assert "Recorded decision" in html and "Return completed" in html
    assert 'form="return-review-approve"' not in html and 'form="return-review-reject"' not in html


def test_empty_evidence_and_replacement_states_remain_clear():
    html = render(role="sales-supervisor", attachments=[], quote_lines=[])
    assert "No supporting photos attached." in html
    assert "No replacement products recorded yet." in html


def test_action_errors_render_an_accessible_alert_without_losing_the_page():
    html = render(role="sales-supervisor", error="Reload and review the updated request.")
    assert 'role="alert"' in html and "Reload and review the updated request." in html
    assert "data-return-details" in html
