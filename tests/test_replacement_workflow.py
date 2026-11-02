"""Versioned replacement pricing, payment-before-preparation, custody and delivery."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app import repositories
from app.database import fetch_one, get_transaction_cursor
from app.business_time import business_now, business_today
from app.repositories import (
    product_returns as returns,
    replacement_workflow as workflow,
    replacement_messages as messages,
)
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo, refund_proof, substitute  # noqa: F401

pytestmark = pytest.mark.postgres


def request(d, items=None):
    items = items or [(d["product"], 2)]
    lines = [(d["allocation"], 2)]
    comparison = workflow.preview(d["order"], d["reseller"], lines, items)
    case = returns.create_product_return(
        d["order"],
        d["reseller"],
        "replace",
        lines,
        "Damaged packs need replacement",
        [supporting_photo()],
        str(uuid4()),
        replacement_items=items,
        comparison_token=comparison["token"],
    )
    return case, comparison


def approve_receive(d, case):
    returns.review_product_return(case, d["supervisor"], True, "Supervisor approves requested replacement")
    returns.inspect_product_return(
        case, d["inventory"], "complete", "All approved quantities and proof match", str(uuid4())
    )


def pay(d, case, amount):
    reference = "TOPUP" + uuid4().hex[:14]
    details = dict(
        transaction_number=reference,
        payment_amount=str(amount),
        payment_date=business_today().isoformat(),
        payment_time="10:00",
    )
    returns.record_return_payment(case, d["reseller"], [refund_proof()], details)
    returns.verify_return_payment(
        case, d["supervisor"], reference, amount, "Funds confirmed in the payment provider", funds_received=True
    )


def test_same_product_price_components_and_immediate_supervisor_conversation(lifecycle):  # noqa: F811
    d = lifecycle
    repositories.update_product_price(d["product"], "250")
    # The pricing builder must preserve each historic price, not average before matching.
    with get_transaction_cursor() as cur:
        multi = workflow.price_selection(
            cur,
            [
                dict(allocation_id=8, product_id=d["product"], quantity=1, unit_price="150.00"),
                dict(allocation_id=3, product_id=d["product"], quantity=2, unit_price="100.00"),
            ],
            [(d["product"], 4)],
        )
    assert [(c.get("allocation_id"), c["quantity"]) for c in multi["items"][0]["pricing_components"]] == [
        (3, 2),
        (8, 1),
        (None, 1),
    ]
    assert Decimal(multi["total"]) == 600 and Decimal(multi["credit"]) == 350
    case, comparison = request(d, [(d["product"], 3)])
    assert Decimal(comparison["credit"]) == 200
    assert Decimal(comparison["total"]) == 450
    assert [
        (p["basis"], p["quantity"], Decimal(p["unit_price"])) for p in comparison["items"][0]["pricing_components"]
    ] == [("original", 2, Decimal(100)), ("catalog", 1, Decimal(250))]
    result = returns.get_product_return(case, d["supervisor"])
    assert result["replacement_workflow_version"] == 2
    assert result["status"] == "submitted" and result["approved_by_account_id"] is None
    assert result["processing_supervisor_account_id"] == d["supervisor"]
    assert result["accepted_revision"] == 1
    assert messages.messages(case, d["reseller"])["messages"]
    with pytest.raises(ValueError):
        messages.messages(case, d["sales"])
    with pytest.raises(ValueError):
        returns.review_product_return(case, d["sales"], True, "Regular leader cannot approve")
    key = str(uuid4())
    for _ in range(2):
        messages.send(case, d["reseller"], "Please review the damaged packs", key)
    assert len(messages.messages(case, d["supervisor"])["messages"]) == 2
    with pytest.raises(ValueError):
        messages.send(case, d["reseller"], "Different content", key)
    last = messages.messages(case, d["supervisor"])["messages"][-1]["message_id"]
    messages.mark_read(case, d["supervisor"], last)
    assert messages.threads(d["supervisor"])[0]["unread_count"] == 0


def test_message_badges_follow_bounded_cursors_and_ignore_own_replies(lifecycle):  # noqa: F811
    d = lifecycle
    case, _ = request(d)
    other, _ = request(d)
    initial = messages.messages(case, d["supervisor"])
    assert initial["unread_count"] == 1
    messages.send(case, d["supervisor"], "Supervisor reply", str(uuid4()))
    first = messages.send(case, d["reseller"], "First incoming reply", str(uuid4()))
    last = messages.send(case, d["reseller"], "Second incoming reply", str(uuid4()))
    unrelated = messages.send(other, d["reseller"], "Another case", str(uuid4()))
    assert messages.messages(case, d["supervisor"])["unread_count"] == 3
    # A real message from another case cannot clear this case's badge.
    assert messages.mark_read(case, d["supervisor"], unrelated["message_id"]) == 3
    assert messages.mark_read(case, d["supervisor"], first["message_id"]) == 1
    assert messages.mark_read(case, d["supervisor"], initial["messages"][0]["message_id"]) == 1
    assert messages.messages(case, d["supervisor"], after_id=first["message_id"])["unread_count"] == 1
    assert messages.mark_read(case, d["supervisor"], last["message_id"]) == 0
    assert messages.messages(other, d["supervisor"])["unread_count"] == 2


def test_receipt_then_payment_then_fefo_preparation_release_and_delivery(lifecycle):  # noqa: F811
    d = lifecycle
    product = substitute(300)
    case, _ = request(d, [(product, 1)])
    with pytest.raises(ValueError):
        returns.record_return_payment(case, d["reseller"], [refund_proof()], {})
    approve_receive(d, case)
    assert fetch_one("SELECT SUM(quantity_received) n FROM expired_holdings WHERE source='return'")["n"] == 2
    with pytest.raises(ValueError, match="clearance"):
        returns.reserve_replacement(case, d["inventory"], "Preparation before payment is blocked")
    pay(d, case, "100.00")
    assert not returns.get_product_return(case, d["inventory"])["has_reservations"]
    assert returns.reserve_replacement(case, d["inventory"], "Prepared valid FEFO replacement packs")
    ready = returns.get_product_return(case, d["supervisor"])
    assert ready["replacement_prepared_at"]
    assert ready["status"] == "settlement_pending"
    returns.release_replacement(case, d["inventory"], "Warehouse handover for delivery")
    assert returns.get_product_return(case, d["supervisor"])["status"] == "settlement_pending"
    workflow.deliver(case, d["supervisor"], "Delivered to the reseller contact")
    assert returns.get_product_return(case, d["reseller"])["status"] == "completed"
    assert fetch_one("SELECT COUNT(*) n FROM orders")["n"] == 1
    summary = returns.return_settlement_totals(d["owner"])
    assert summary["original_sales"] == 1000 and summary["additional_collections"] == 100


def test_same_product_without_price_change_still_saves_comparison(lifecycle):  # noqa: F811
    d = lifecycle
    repositories.update_product_price(d["product"], "999")
    case, comparison = request(d)
    assert Decimal(comparison["total"]) == Decimal(comparison["credit"]) == 200
    approve_receive(d, case)
    returns.reserve_replacement(case, d["inventory"], "Prepare same-product FEFO stock")
    assert any("Supervisor approved" in e["remarks"] for e in returns.get_product_return(case, d["reseller"])["events"])
    returns.release_replacement(case, d["inventory"], "Release same-product replacement")
    workflow.deliver(case, d["supervisor"], "Reseller received the replacement")
    assert returns.get_product_return(case, d["reseller"])["status"] == "completed"
    assert fetch_one("SELECT COUNT(*) n FROM return_offer_snapshots WHERE return_id=%s", (case,))["n"] == 1


def test_cheaper_selection_refund_and_delivery_both_required(lifecycle):  # noqa: F811
    d = lifecycle
    product = substitute(100)
    case, _ = request(d, [(product, 1)])
    approve_receive(d, case)
    returns.reserve_replacement(case, d["inventory"], "Prepare lower-priced replacement")
    returns.release_replacement(case, d["inventory"], "Release replacement and record difference")
    workflow.deliver(case, d["supervisor"], "Replacement delivered with refund pending")
    record = returns.get_product_return(case, d["supervisor"])
    assert record["status"] == "settlement_pending" and record["refund"]["amount"] == 100
    refund = record["refund"]["order_refund_id"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    repositories.complete_order_refund(
        refund,
        d["supervisor"],
        "sales-supervisor",
        transfer_amount=100,
        refund_method="Simulated transfer",
        transaction_reference="RF" + uuid4().hex[:16],
        transferred_at=business_now(),
        internal_note="Simulated test, no money transferred",
        proof=refund_proof(),
    )
    assert returns.get_product_return(case, d["supervisor"])["status"] == "completed"


def test_stale_comparison_and_paid_stock_shortage_preserve_payment(lifecycle):  # noqa: F811
    d = lifecycle
    product = substitute(300)
    lines = [(d["allocation"], 2)]
    comparison = workflow.preview(d["order"], d["reseller"], lines, [(product, 1)])
    repositories.update_product_price(product, "350")
    with pytest.raises(ValueError, match="changed"):
        returns.create_product_return(
            d["order"],
            d["reseller"],
            "replace",
            lines,
            "Review stale product comparison",
            [supporting_photo()],
            replacement_items=[(product, 1)],
            comparison_token=comparison["token"],
        )
    case, _ = request(d, [(product, 1)])
    approve_receive(d, case)
    pay(d, case, "150.00")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE item_id=%s", (business_today(), product))
    assert returns.reserve_replacement(case, d["inventory"], "Try preparation after stock expiry") is False
    result = returns.get_product_return(case, d["supervisor"])
    assert result["payment_amount"] == 150 and result["payment_verified_at"]
    assert not result["replacement_prepared_at"] and not result["has_reservations"]
    with pytest.raises(ValueError):
        returns.release_replacement(case, d["inventory"], "Expired stock cannot be released")
    with pytest.raises(ValueError, match="cannot be revised"):
        returns.review_product_return(case, d["supervisor"], True, "Cannot change a paid quote", [(d["product"], 2)])


def test_existing_replacement_creation_keeps_version_one(lifecycle):  # noqa: F811
    from test_product_lifecycle import create_case

    d = lifecycle
    case = create_case(d, "replace")
    record = returns.get_product_return(case, d["sales"])
    assert record["replacement_workflow_version"] == 1
    assert not record["handed_off_at"]


def test_ready_stock_expiry_blocks_release_and_repreparation_preserves_payment(lifecycle):  # noqa: F811
    from datetime import timedelta

    d = lifecycle
    product = substitute(300)
    case, _ = request(d, [(product, 1)])
    approve_receive(d, case)
    pay(d, case, 100)
    returns.reserve_replacement(case, d["inventory"], "Prepared eligible stock before expiry")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE item_id=%s", (business_today(), product))
    with pytest.raises(ValueError):
        returns.release_replacement(case, d["inventory"], "Cannot dispatch expired reserved stock")
    repositories.add_product_batch(product, "RESTOCK-" + uuid4().hex[:10], 3, business_today() + timedelta(days=5))
    returns.reserve_replacement(case, d["inventory"], "Prepare valid restocked FEFO goods")
    result = returns.get_product_return(case, d["supervisor"])
    assert result["payment_amount"] == 100 and result["payment_verified_at"]
    assert all(row["expiry_date"] > business_today() for row in result["reserved_batches"])
    returns.release_replacement(case, d["inventory"], "Dispatch valid replacement after restock")
    assert fetch_one("SELECT COUNT(*) n FROM replacement_releases WHERE return_id=%s", (case,))["n"] == 1


def test_missing_supervisor_routes_conversation_to_owner_and_rejects_other_participants(lifecycle):  # noqa: F811
    from app.conversation_notifications import unread_summary

    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE accounts SET is_active=false WHERE account_id=%s", (d["supervisor"],))
    case, _ = request(d)
    assert returns.get_product_return(case, d["owner"])["processing_supervisor_account_id"] is None
    messages.send(case, d["reseller"], "Owner please review this replacement", str(uuid4()))
    assert messages.threads(d["owner"])[0]["return_id"] == case
    assert unread_summary(d["owner"], "owner")["unread_conversations"] == 1
    with pytest.raises(ValueError):
        messages.messages(case, d["inventory"])
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role)
            VALUES('team_leader','Unrelated Supervisor','unrelated@replacement.test','hash','sales-supervisor') RETURNING account_id""")
        unrelated = cur.fetchone()["account_id"]
    with pytest.raises(ValueError):
        messages.messages(case, unrelated)


def test_current_receipt_and_explicit_funds_confirmation_are_required(lifecycle):  # noqa: F811
    d = lifecycle
    product = substitute(300)
    case, _ = request(d, [(product, 1)])
    approve_receive(d, case)
    details = dict(
        transaction_number="PAYMENTCHECK123",
        payment_amount="100.00",
        payment_date=business_today().isoformat(),
        payment_time="10:00",
    )
    with pytest.raises(ValueError, match="amount"):
        returns.record_return_payment(case, d["reseller"], [refund_proof()], {**details, "payment_amount": "99.99"})
    returns.record_return_payment(case, d["reseller"], [refund_proof()], details)
    with pytest.raises(ValueError, match="provider"):
        returns.verify_return_payment(
            case, d["supervisor"], "PAYMENTCHECK123", 100, "Requires explicit receipt of funds"
        )
    returns.verify_return_payment(
        case, d["supervisor"], "OTHERREFERENCE", 999, "Verify stored reseller details only", funds_received=True
    )
    verified = returns.get_product_return(case, d["supervisor"])
    assert verified["payment_verified_at"]
    assert verified["payment_reference"] == "PAYMENTCHECK123"
    assert verified["payment_amount"] == 100


def test_migration_preserves_legacy_cases_and_conversation_history_is_immutable(lifecycle):  # noqa: F811
    from pathlib import Path
    import psycopg2
    from test_product_lifecycle import create_case

    d = lifecycle
    legacy = create_case(d, "replace")
    before = fetch_one("SELECT * FROM product_returns WHERE return_id=%s", (legacy,))
    migration = Path("database/migrations/044_replacement_workflow.sql").read_text(encoding="utf-8")
    with get_transaction_cursor() as cur:
        cur.execute(migration)
        cur.execute(migration)
    assert dict(fetch_one("SELECT * FROM product_returns WHERE return_id=%s", (legacy,))) == dict(before)
    # Claim another original pack; the legacy case already claims two.
    lines = [(d["allocation"], 1)]
    comparison = workflow.preview(d["order"], d["reseller"], lines, [(d["product"], 1)])
    case = returns.create_product_return(
        d["order"],
        d["reseller"],
        "replace",
        lines,
        "Another traceable damaged pack",
        [supporting_photo()],
        replacement_items=[(d["product"], 1)],
        comparison_token=comparison["token"],
    )
    with pytest.raises(psycopg2.errors.RaiseException):
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE return_conversation_messages SET content='Changed history' WHERE return_id=%s", (case,))


def test_mismatch_revision_recalculates_original_credit_and_requires_fresh_acceptance(lifecycle):  # noqa: F811
    d = lifecycle
    case, _ = request(d)
    returns.review_product_return(case, d["supervisor"], True, "Approve the original requested replacement")
    returns.inspect_product_return(case, d["inventory"], "mismatch", "Only one approved pack arrived", str(uuid4()))
    line = returns.get_product_return(case, d["supervisor"])["lines"][0]
    returns.review_product_return(
        case,
        d["supervisor"],
        True,
        "Review corrected request after mismatch",
        replacements=[(d["product"], 1)],
        revised_quantities=[(line["return_line_id"], 1)],
        customer_message="We agreed to replace the one returned pack after reviewing the mismatch.",
    )
    revised = returns.get_product_return(case, d["reseller"])
    assert revised["credit"] == 100 and revised["replacement_total"] == 100
    assert revised["quote_revision"] == 2 and revised["accepted_revision"] is None
    returns.inspect_product_return(
        case, d["inventory"], "complete", "The revised approved request matches in full", str(uuid4())
    )
    with pytest.raises(ValueError):
        returns.reserve_replacement(case, d["inventory"], "Cannot prepare before revised quote acceptance")
    returns.accept_replacement_quote(case, d["reseller"], 2)
    assert returns.reserve_replacement(case, d["inventory"], "Prepare the newly accepted corrected request")


def test_new_payment_is_in_supervisor_queue_before_stock_reservation(lifecycle):  # noqa: F811
    d = lifecycle
    case, _ = request(d, [(substitute(300), 1)])
    approve_receive(d, case)
    returns.record_return_payment(case, d["reseller"], [refund_proof()], dict(
        transaction_number="NEWQUEUE123", payment_amount="100.00", payment_date=business_today().isoformat(), payment_time="10:30"))
    assert not returns.get_product_return(case, d["supervisor"])["has_reservations"]
    assert returns.return_queue_counts(d["supervisor"])["payment"] == 1
    queued = returns.list_product_returns(d["supervisor"], queue="payment")
    row = next(row for row in queued if row["return_id"] == case)
    assert row["experience"]["sales"]["action"] == "verify_payment"
    assert row["experience"]["reseller"]["action"] == ""


def test_payment_confirmation_rejects_receipt_changed_since_staff_review(lifecycle):  # noqa: F811
    d = lifecycle
    case, _ = request(d, [(substitute(300), 1)])
    approve_receive(d, case)
    details = dict(transaction_number="FIRSTRECEIPT123", payment_amount="100.00",
                   payment_date=business_today().isoformat(), payment_time="10:30")
    returns.record_return_payment(case, d["reseller"], [refund_proof()], details)
    reviewed = returns.get_product_return(case, d["supervisor"])["payment_review_version"]
    returns.record_return_payment(case, d["reseller"], [refund_proof()], {**details, "transaction_number": "SECONDRECEIPT123"})
    with pytest.raises(ValueError, match="submission changed"):
        returns.verify_return_payment(case, d["supervisor"], "", "", "Funds reviewed on earlier receipt", funds_received=True, review_version=reviewed)
    current = returns.get_product_return(case, d["supervisor"])
    assert not current["payment_verified_at"]
    assert current["payment_review_version"] != reviewed
    returns.verify_return_payment(case, d["supervisor"], "", "", "Current provider funds checked", funds_received=True, review_version=current["payment_review_version"])
    assert returns.get_product_return(case, d["supervisor"])["payment_reference"] == "SECONDRECEIPT123"
