"""Sales tracking cards preserve current facts and role boundaries."""
from datetime import datetime
from decimal import Decimal

import pytest
from starlette.requests import Request

from app import main  # noqa: F401
from app.web import templates


def render(*, role="team-leader", authorize=False, error=None, message=None, **changes):
    now = datetime(2026, 11, 2, 10)
    case = dict(return_id=3, order_id=6, resolution="replace", replacement_workflow_version=2,
                status="submitted", reseller_name="Example reseller", reseller_account_id=8,
                created_at=now, remarks="Damaged packaging", handed_off_at=now,
                approved_by_account_id=None, verified_at=None, has_reservations=False,
                replacement_prepared_at=None, replacement_released_at=None, replacement_delivered_at=None,
                replacement_progress="Supervisor review", has_open_followup=False, followups=[],
                quote_revision=1, accepted_revision=1, credit=Decimal("200"), replacement_total=Decimal("300"),
                additional_due=Decimal("100"), unused_credit=Decimal("0"), payment_verified_at=None,
                sales_authorized=False, receipt_confirmed=False, refund=None,
                reconciliation_flags=[], receipts=[], receipt_lines=[], reserved_batches=[], released_batches=[],
                lines=[dict(name="Returned product", quantity=2, received_quantity=0,
                            batch_code="BATCH-1", expiry_date=now.date(), unit_price=Decimal("100"))],
                quote_lines=[dict(name="Requested product", quantity=1, line_total=Decimal("300"), pricing_components=[])],
                attachments=[dict(purpose="evidence", attachment_id=1, filename="supporting-photo.png")],
                events=[dict(action="submitted", created_at=now, remarks="Request sent to Supervisor.")],
                experience=dict(sales=dict(title="Tracking with Supervisor/Owner", action=""),
                                reseller=dict(title="Waiting for review")))
    case.update(changes)
    if role == "reseller":
        from app.return_timeline import workflow_steps
        from app.repositories.return_views import reseller_return_view
        case["workflow_steps"] = workflow_steps(case)
        case = reseller_return_view(case)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "session": {}})
    template = "portals/reseller/_return_details_page.html" if role=="reseller" else "portals/returns/_supervisor_case_page.html" if role=="sales-supervisor" else "portals/team-leader/_return_case_dialog.html" if role=="owner" else "portals/team-leader/_sales_case_dialog.html"
    return templates.env.get_template(template).render(
        case=case, request=request, role_key=role, return_can_review=role in {"sales-supervisor", "owner"},
        return_can_authorize=authorize, replacement_products=[],
        return_is_reseller=role=="reseller", action_base=f"/portal/{role}/returns/actions/",
        operation_key=lambda: "test-operation", message=message, error=error)


def test_tracking_facts_are_visible_without_disclosures_or_staff_actions():
    html = render()
    assert "<details" not in html and "<form" not in html
    for text in ("Review request", "Example reseller", "0 / 2 received", "Return timeline",
                 "Products to return", "Replacement selection", "Price comparison", "Supporting photos",
                 "Activity timeline", "Request sent to Supervisor.", "PHP 100.00"):
        assert text in html
    assert html.count('aria-current="step"') == 1
    assert "Tracking only" in html


@pytest.mark.parametrize("version", [1, 2])
def test_stock_and_payment_step_order_respects_workflow_version(version):
    html = render(replacement_workflow_version=version)
    payment = html.index("Submit additional-payment receipt")
    if version == 1:
        assert html.index("Reserve replacement stock") < payment
        assert "Confirm delivery" not in html
    else:
        assert payment < html.index("Prepare replacement stock")
        assert "Confirm delivery" in html


def test_refund_card_has_no_replacement_or_payment_steps():
    html = render(resolution="refund", replacement_workflow_version=1,
                  additional_due=0, quote_lines=[], replacement_total=0)
    assert "Refund summary" in html and "Send refund" in html
    assert "Replacement selection" not in html and "Submit additional-payment receipt" not in html


@pytest.mark.parametrize("status", ["rejected", "completed"])
def test_terminal_case_does_not_assign_a_current_step(status):
    assert 'aria-current="step"' not in render(status=status)


def test_verified_payment_advances_to_stock_preparation():
    html = render(approved_by_account_id=4, verified_at=datetime(2026, 11, 2, 11),
                  payment_verified_at=datetime(2026, 11, 2, 12), sales_authorized=True)
    current = html.split('aria-current="step"', 1)[1].split("</li>", 1)[0]
    assert "Prepare replacement stock" in current


def test_delivery_and_difference_refund_can_both_be_outstanding_after_release():
    html = render(approved_by_account_id=4, verified_at=datetime(2026, 11, 2, 11),
                  sales_authorized=True, additional_due=0, unused_credit=100,
                  replacement_released_at=datetime(2026, 11, 2, 12))
    current = html.split('aria-current="step"')[1:]
    assert len(current) == 2
    assert "Confirm delivery" in current[0].split("</li>", 1)[0]
    assert "Send difference refund" in current[1].split("</li>", 1)[0]


def test_followup_and_customer_text_remain_visible_and_escaped():
    html = render(has_open_followup=True, remarks="<script>private</script>")
    assert "Resolve reported issue" in html
    assert "<script>private</script>" not in html and "&lt;script&gt;" in html


@pytest.mark.parametrize("policy", [1, 2])
def test_owner_summary_respects_payment_policy_and_uses_owner_conversation(policy):
    html = render(role="owner", return_policy_version=policy,
                  replacement_total=Decimal("200") if policy == 2 else Decimal("300"),
                  additional_due=0 if policy == 2 else Decimal("100"))
    summary = html.split('class="supervisor-case-money"', 1)[1].split("</dl>", 1)[0]
    if policy == 2:
        assert "No payment or price adjustment" in summary
        assert "Extra payment due" not in summary and "Difference refund" not in summary
    else:
        assert "Extra payment due" in summary and "Difference refund" in summary
    assert '/portal/owner/messages?replacement=3' in html
    assert '/portal/team-leader/messages' not in html


@pytest.mark.parametrize("resolution", ["replace", "refund"])
@pytest.mark.parametrize("verified", [False, True])
def test_owner_converted_summary_shows_refund_and_custody_gate(resolution, verified):
    html = render(role="owner", resolution=resolution, return_policy_version=2,
                  status="awaiting_receipt" if not verified else "settlement_pending",
                  refund_converted_at=datetime(2026, 11, 4),
                  verified_at=datetime(2026, 11, 4) if verified else None)
    summary = html.split('class="supervisor-case-facts"', 1)[1].split("<footer>", 1)[0]
    assert "Refund status" in summary and "Refund amount" in summary
    assert ("Awaiting settlement" if verified else "Awaiting return verification") in summary
    assert "Replacement stock" not in summary and "Extra payment due" not in summary


def test_owner_list_treats_conversion_marker_as_refund():
    request = Request({"type": "http", "method": "GET", "scheme": "http",
                       "server": ("test", 80), "path": "/portal/owner/returns",
                       "query_string": b"", "headers": [], "session": {}})
    html = templates.env.get_template("portals/team-leader/returns.html").render(
        request=request, role_key="owner", return_case=None, return_queue=None,
        return_cases=[dict(return_id=3, order_id=6, reseller_name="Example reseller",
            status="awaiting_receipt", resolution="replace", credit=Decimal("200"),
            refund_converted_at=datetime(2026, 11, 4))],
        return_cases_page=dict(pagination=dict(total_pages=1)), legacy_lines=[])
    assert "Refund</span>" in html and 'data-lucide="wallet"' in html
    assert "Replace</span>" not in html
