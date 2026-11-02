"""Customer details reuse public case projections and existing customer actions."""
from datetime import datetime

from test_sales_case_cards import render


def test_public_page_has_five_sections_and_no_staff_controls():
    html = render(role="reseller")
    for label in ("Overview", "Replacement &amp; Delivery", "Payment &amp; Refunds", "Attachments", "Activity History"):
        assert label in html
    assert html.count('data-overview-stage=') == 5
    assert "data-return-details" in html and "Return history" in html
    for forbidden in ("Internal review notes", "verify-payment", "Holding #", "Approve return request", "Review Decision"):
        assert forbidden not in html
    assert 'data-return-dialog=' not in html


def test_changed_offer_has_one_revision_bound_acceptance_form():
    html = render(role="reseller", status="awaiting_receipt", quote_revision=2, accepted_revision=1)
    assert html.count('id="return-offer-accept"') == 1
    assert 'name="revision" value="2"' in html
    assert 'data-return-intent="accept"' in html
    assert 'form="return-offer-accept"' in html


def test_customer_payment_retains_recognition_and_confirmation_gates():
    html = render(role="reseller", status="settlement_pending", sales_authorized=True,
                  verified_at=datetime(2026, 11, 2, 10))
    assert 'data-receipt-checkout' in html and 'data-receipt-native-submit' in html
    assert '/portal/reseller/returns/3/receipt-scan' in html
    assert 'name="receipt_scan_token"' in html and 'data-receipt-submit' in html


def test_current_receipt_is_shown_as_awaiting_verification_with_optional_resubmission():
    html = render(role="reseller", status="settlement_pending", sales_authorized=True,
                  verified_at=datetime(2026, 11, 2, 10), has_payment_proof=True,
                  replacement_payment_details=dict(transaction_number="EXAMPLE123", payment_amount="100", revision=1))
    assert "Receipt submitted" in html and "Replace submitted receipt" in html
    assert "View payment" in html


def test_receipt_acknowledgment_remains_optional_and_has_no_staff_delivery_form():
    html = render(role="reseller", replacement_released_at=datetime(2026, 11, 2, 10))
    assert 'action="/portal/reseller/returns/actions/acknowledge-replacement"' in html
    assert 'data-return-intent="acknowledge"' in html
    assert "Report an issue" in html
    assert 'actions/delivered' not in html


def test_private_event_notes_are_filtered_before_rendering():
    html = render(role="reseller", events=[dict(action="payment_verified", created_at=datetime(2026, 11, 2, 10),
                                               remarks="PRIVATE STAFF NOTES", customer_message="")])
    assert "PRIVATE STAFF NOTES" not in html
    assert "Your additional payment was verified." in html
