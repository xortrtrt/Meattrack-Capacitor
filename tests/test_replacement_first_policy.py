"""Acceptance contract for new replacement-first returns; legacy cases stay separate."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app import repositories
from app.business_time import business_now
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns, replacement_workflow as workflow
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo, substitute, refund_proof  # noqa: F401
from test_replacement_workflow import approve_receive

pytestmark = pytest.mark.postgres


def request(d, quantity=2, *, key=None):
    lines = [(d["allocation"], quantity)]
    preview = workflow.preview(d["order"], d["reseller"], lines, None)
    case = returns.create_product_return(
        d["order"], d["reseller"], "replace", lines, "Damaged packs require replacement",
        [supporting_photo()], key or str(uuid4()), comparison_token=preview["token"],
    )
    return case


def data(d, case):
    return returns.get_product_return(case, d["supervisor"])


def convert(d, case, *, actor=None, key=None, version=None, unreceived=False):
    reviewed = data(d, case)
    return returns.convert_product_return_to_refund(
        case, actor or d["supervisor"], "Supplier cannot provide an acceptable replacement",
        "No acceptable replacement is available. Your approved return will be refunded.",
        key or str(uuid4()), version or returns.refund_conversion_version(reviewed),
        unreceived_payment=unreceived,
    )


def settle(d, case):
    refund = data(d, case)["refund"]
    repositories.start_order_refund(refund["order_refund_id"], d["supervisor"], "sales-supervisor")
    repositories.complete_order_refund(
        refund["order_refund_id"], d["supervisor"], "sales-supervisor",
        refund_method="Synthetic transfer", transaction_reference="RF" + uuid4().hex,
        transferred_at=business_now(), internal_note="Synthetic evidence; no external transfer",
        proof=refund_proof(), transfer_amount=refund["amount"],
    )


def offer(d, case, product, quantity=1):
    returns.review_product_return(
        case, d["supervisor"], True, "Original product cannot be supplied by our supplier",
        [(product, quantity)], customer_message="Please review this alternative to the unavailable product.",
    )


@pytest.mark.parametrize("active", [True, False])
def test_default_uses_only_returned_quantities_and_original_prices(lifecycle, active):  # noqa: F811
    d = lifecycle
    repositories.update_product_price(d["product"], "450")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_items SET is_active=%s WHERE item_id=%s", (active, d["product"]))
    case = request(d, 3)
    result = data(d, case)
    assert result["return_policy_version"] == 2
    assert result["replacement_workflow_version"] == 2
    assert result["credit"] == result["replacement_total"] == 300
    assert result["additional_due"] == result["unused_credit"] == 0
    assert [(r["product_id"], r["quantity"]) for r in result["quote_lines"]] == [(d["product"], 3)]
    assert result["accepted_revision"] == result["quote_revision"] == 1
    assert not result["has_reservations"] and not result["refund"]
    returns.review_product_return(case, d["supervisor"], True, "Check and reserve approved replacement stock")
    result = data(d, case)
    if active:
        assert result["has_reservations"] and not result["refund"]
    else:
        assert not result["has_reservations"] and result["refund_converted_at"]


def test_new_reseller_cannot_choose_refund_or_substitute_or_extra_packs(lifecycle):  # noqa: F811
    d = lifecycle
    with pytest.raises(ValueError, match="replacement"):
        returns.create_product_return(d["order"], d["reseller"], "refund", [(d["allocation"], 2)],
                                      "Request a cash refund directly", [supporting_photo()])
    for items in [[(substitute(250), 1)], [(d["product"], 3)]]:
        with pytest.raises(ValueError, match="same|Supervisor|default"):
            workflow.preview(d["order"], d["reseller"], [(d["allocation"], 2)], items)
    assert not fetch_all("SELECT * FROM product_returns")


def test_default_request_retry_is_idempotent_and_tampered_token_is_rejected(lifecycle):  # noqa: F811
    d = lifecycle
    key = str(uuid4())
    case = request(d, key=key)
    assert request(d, key=key) == case
    preview = workflow.preview(d["order"], d["reseller"], [(d["allocation"], 1)], None)
    with pytest.raises(ValueError, match="comparison|changed"):
        returns.create_product_return(d["order"], d["reseller"], "replace", [(d["allocation"], 2)],
                                      "Changed quantities after comparison", [supporting_photo()],
                                      comparison_token=preview["token"])
    assert len(fetch_all("SELECT * FROM product_returns")) == 1


def test_supervisor_cannot_change_submitted_replacement(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    with pytest.raises(ValueError, match="fixed after submission"):
        offer(d, case, substitute(150))
    assert data(d, case)["quote_revision"] == 1


def test_supervisor_cannot_offer_equal_value_alternative(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    with pytest.raises(ValueError, match="fixed after submission"):
        offer(d, case, substitute(200))
    current = data(d, case)
    assert current["accepted_revision"] == current["quote_revision"] == 1
    assert current["replacement_total"] == current["credit"] == 200


def test_higher_value_offer_and_replacement_payment_are_rejected(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    with pytest.raises(ValueError, match="fixed after submission"):
        offer(d, case, substitute(300))
    with pytest.raises(ValueError):
        returns.record_return_payment(case, d["reseller"], [refund_proof()], {
            "transaction_number": "PAY" + uuid4().hex, "payment_amount": "100",
            "payment_date": business_now().date().isoformat(), "payment_time": "10:00",
        })


def test_conversion_before_receipt_waits_for_complete_inventory_verification(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    returns.review_product_return(case, d["supervisor"], True, "Approve the damaged goods return")
    convert(d, case)
    result = data(d, case)
    assert result["resolution"] == "refund" and result["refund_converted_at"]
    assert not result["refund"]
    returns.inspect_product_return(case, d["inventory"], "complete", "Complete approved return received", str(uuid4()))
    assert data(d, case)["refund"]["amount"] == 200
    settle(d, case)
    assert data(d, case)["status"] == "completed"
    assert not data(d, case)["replacement_delivered_at"]


def test_conversion_refunds_only_return_credit_after_receipt(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    returns.reserve_replacement(case, d["inventory"], "Prepare before supplier issue is identified")
    key = str(uuid4())
    old = returns.refund_conversion_version(data(d, case))
    convert(d, case, key=key, version=old)
    convert(d, case, key=key, version=old)
    result = data(d, case)
    assert not result["has_reservations"] and not result["replacement_prepared_at"]
    refund = result["refund"]
    assert refund["amount"] == 200
    assert refund["original_credit_amount"] == 200 and refund["topup_amount"] == 0
    for action, args in [
        (returns.reserve_replacement, (case, d["inventory"], "Cannot prepare a refunded case")),
        (returns.release_replacement, (case, d["inventory"], "Cannot release a refunded case")),
        (returns.accept_replacement_quote, (case, d["reseller"], result["quote_revision"])),
        (workflow.deliver, (case, d["supervisor"], "Cannot deliver a refunded case")),
    ]:
        with pytest.raises(ValueError):
            action(*args)
    settle(d, case)
    assert data(d, case)["status"] == "completed"
    totals = fetch_one("SELECT * FROM order_financial_reconciliation WHERE order_id=%s", (d["order"],))
    assert totals["retained_sales"] == 800 and totals["net_collections"] == 800
    assert not totals["flags"]
    from app.repositories.report_settlements import report_order_rows
    with get_transaction_cursor() as cur:
        rows = report_order_rows(cur, business_now().date(), business_now().date(), None)
    assert rows[0]["refunded_sales"] == 200 and rows[0]["retained_sales"] == 800
    assert len(fetch_all("SELECT * FROM order_refunds WHERE return_id=%s", (case,))) == 1


def test_conversion_rejects_unauthorized_stale_and_released_cases(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    for actor in [d["sales"], d["inventory"], d["reseller"]]:
        with pytest.raises(ValueError):
            convert(d, case, actor=actor)
    version = returns.refund_conversion_version(data(d, case))
    returns.reserve_replacement(case, d["inventory"], "Prepare accepted replacement")
    with pytest.raises(ValueError, match="changed|Reload|reload"):
        convert(d, case, version=version)
    returns.release_replacement(case, d["inventory"], "Release accepted replacement")
    with pytest.raises(ValueError, match="release"):
        convert(d, case)


def test_release_racing_conversion_has_one_winner_and_balanced_stock(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    returns.reserve_replacement(case, d["inventory"], "Prepare eligible replacement")
    version = returns.refund_conversion_version(data(d, case))
    before = fetch_one("SELECT SUM(quantity_available) n FROM inventory_batches")["n"]
    barrier = Barrier(2)

    def act(conversion):
        barrier.wait(timeout=10)
        try:
            if conversion:
                convert(d, case, version=version)
            else:
                returns.release_replacement(case, d["inventory"], "Concurrent warehouse release")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(act, [True, False])) == 1
    result = data(d, case)
    after = fetch_one("SELECT SUM(quantity_available) n FROM inventory_batches")["n"]
    assert before - after == (2 if result["replacement_released_at"] else 0)
    assert bool(result["refund"]) != bool(result["replacement_released_at"])


def test_mismatch_rebuilds_default_from_corrected_quantities(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    returns.review_product_return(case, d["supervisor"], True, "Approve originally requested packs")
    returns.inspect_product_return(case, d["inventory"], "mismatch", "Only one approved pack was returned", str(uuid4()))
    line = data(d, case)["lines"][0]
    returns.review_product_return(case, d["supervisor"], True, "Corrected full approved request to one pack",
                                  revised_quantities=[(line["return_line_id"], 1)],
                                  customer_message="Only one returned pack is approved. Please review the corrected comparison.")
    result = data(d, case)
    assert result["credit"] == result["replacement_total"] == 100
    assert result["quote_lines"][0]["quantity"] == 1
    assert result["accepted_revision"] is None
    returns.accept_replacement_quote(case, d["reseller"], result["quote_revision"])
    returns.inspect_product_return(case, d["inventory"], "complete", "Corrected complete request now matches", str(uuid4()))
    assert data(d, case)["sales_authorized"]


@pytest.mark.parametrize("modern", [False, True])
def test_legacy_refund_and_cheaper_replacement_keep_original_rules(lifecycle, modern):  # noqa: F811
    from legacy_return_fixtures import create_legacy_return, legacy_preview
    from test_product_lifecycle import receive, complete_refund
    d = lifecycle
    kwargs = {}
    resolution = "replace" if modern else "refund"
    if modern:
        product = substitute(100)
        preview = legacy_preview(d["order"], d["reseller"], [(d["allocation"], 2)], [(product, 1)])
        kwargs = {"replacement_items": [(product, 1)], "comparison_token": preview["token"]}
    case = create_legacy_return(d["order"], d["reseller"], resolution, [(d["allocation"], 2)],
                                "Historical request before policy cutover", [supporting_photo()], **kwargs)
    if modern:
        approve_receive(d, case)
        returns.reserve_replacement(case, d["inventory"], "Historical cheaper replacement")
        returns.release_replacement(case, d["inventory"], "Release historical cheaper replacement")
        workflow.deliver(case, d["supervisor"], "Historical replacement delivered")
    else:
        returns.review_product_return(case, d["sales"], True, "Historical cash return approved")
        receive(d, case)
    assert data(d, case)["return_policy_version"] == 1
    assert data(d, case)["refund"]["amount"] == (100 if modern else 200)
    complete_refund(d, case)
    assert data(d, case)["status"] == "completed"


def test_expired_preparation_blocks_release_and_waits_for_valid_restock(lifecycle):  # noqa: F811
    from datetime import timedelta
    from app.business_time import business_today
    d = lifecycle
    case = request(d)
    product = d["product"]
    approve_receive(d, case)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE batch_id IN (SELECT batch_id FROM replacement_stock_reservations WHERE return_id=%s)", (business_today(), case))
    with pytest.raises(ValueError):
        returns.release_replacement(case, d["inventory"], "Cannot release expired reservation")
    assert returns.reserve_replacement(case, d["inventory"], "Reprepare requires valid restock") is False
    assert data(d, case)["payment_amount"] is None and not data(d, case)["payment_verified_at"]
    repositories.add_product_batch(product, "FRESH" + uuid4().hex[:8], 2, business_today() + timedelta(days=20))
    assert returns.reserve_replacement(case, d["inventory"], "Retry with valid restock")
    returns.release_replacement(case, d["inventory"], "Release valid restocked replacement")
    assert not data(d, case)["refund"]


def test_supervisor_approval_shortage_notifies_reseller_and_refunds_after_expired_return(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quantity_available=0 WHERE batch_id=%s", (d["batches"][1],))
    returns.review_product_return(case, d["supervisor"], True, "Approve after stock check")
    result = data(d, case)
    assert result["resolution"] == "refund" and result["refund_converted_at"]
    assert not result["refund"] and not result["has_reservations"]
    assert any(event["action"] == "replacement_stock_shortage" for event in result["events"])
    assert any("stock is insufficient" in (event["customer_message"] or "") for event in result["events"])
    returns.inspect_product_return(case, d["inventory"], "complete", "Expired returned products received", str(uuid4()))
    result = data(d, case)
    assert result["refund"]["amount"] == 200
    assert fetch_one("SELECT location FROM expired_holdings WHERE return_line_id=%s", (result["lines"][0]["return_line_id"],))["location"] == returns.EXPIRED_HOLDING_LOCATION
    settle(d, case)
    assert data(d, case)["status"] == "completed"
