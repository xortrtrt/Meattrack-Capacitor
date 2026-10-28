"""Collections, cancellation refunds, and return settlements share one balance."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg2
import pytest

from app import repositories
from app.business_time import business_now, business_today
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, receive, complete_refund, refund_proof, substitute  # noqa: F401

pytestmark = pytest.mark.postgres


def paid_pending_order(d, amount=600):
    reference = "PAID" + uuid4().hex
    proof = refund_proof()
    with get_transaction_cursor() as cur:
        cur.execute(
            """INSERT INTO orders(order_type,reseller_id,created_by_account_id,team_leader_account_id,
            status,total_amount,payment_transaction_number,payment_transaction_key,payment_paid_at,
            payment_amount,payment_ocr_confidence,payment_extraction_method,payment_confirmed_at,payment_ocr_engine)
            SELECT 'reseller',reseller_id,created_by_account_id,team_leader_account_id,'pending',%s,%s,%s,
            now(),%s,0.9,'ocr_confirmed',now(),'test' FROM orders WHERE order_id=%s RETURNING order_id;""",
            (amount, reference, reference.upper(), amount, d["order"]),
        )
        order = cur.fetchone()["order_id"]
        cur.execute(
            """INSERT INTO order_payment_proofs(order_id,uploaded_by_account_id,filename,content_type,
            content,size_bytes,checksum_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s);""",
            (order, d["reseller"], proof["filename"], proof["content_type"], proof["content"],
             proof["size_bytes"], proof["checksum_sha256"]),
        )
    return order


def settle(d, refund):
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    repositories.complete_order_refund(
        refund, d["supervisor"], "sales-supervisor", refund_method="Simulated bank transfer",
        transaction_reference="REF" + uuid4().hex, transferred_at=business_now(),
        internal_note="Simulated transfer matched to the original collection", proof=refund_proof(),
    )


def verified(d, resolution="refund", replacements=None):
    case = create_case(d, resolution)
    returns.review_product_return(case, d["sales"], True, "Approved expired product settlement", replacements)
    receive(d, case)
    return case


def replacement(d, price):
    case = verified(d, "replace", [(substitute(price), 1)])
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved verified replacement stock")
    return case


def test_all_collection_and_refund_types_reconcile_without_counting_replacement_as_sale(lifecycle):  # noqa: F811
    d = lifecycle
    cancelled = paid_pending_order(d)
    repositories.decide_order(cancelled, "reject", d["sales"], rejection_reason="Paid order cancelled before dispatch",
                              payment_received=True)
    first = verified(d)
    complete_refund(d, first)
    cheaper = replacement(d, 150)
    returns.release_replacement(cheaper, d["inventory"], "Released the accepted cheaper replacement")
    complete_refund(d, cheaper)
    dearer = replacement(d, 250)
    returns.record_return_payment(dearer, d["reseller"], [refund_proof()])
    returns.verify_return_payment(dearer, d["sales"], "TOPUP" + uuid4().hex, 50, "Confirmed the additional collection")
    returns.release_replacement(dearer, d["inventory"], "Released the paid replacement")
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO orders(order_type,created_by_account_id,status,total_amount) VALUES('walk_in',%s,'fulfilled',700);",
                    (d["sales"],))
    before = returns.return_settlement_totals(d["sales"])
    assert before["pending_refunds"] == 600 and before["completed_refunds"] == 250
    assert before["net_collections"] == 2100
    refund = fetch_one("SELECT order_refund_id FROM order_refunds WHERE order_id=%s", (cancelled,))["order_refund_id"]
    settle(d, refund)
    for actor in (d["sales"], d["owner"]):
        totals = returns.return_settlement_totals(actor)
        assert totals["original_sales"] == 1700 and totals["retained_sales"] == 1450
        assert totals["original_collections"] == 2300 and totals["additional_collections"] == 50
        assert totals["cancellation_refunds"] == 600 and totals["return_refunds"] == 200
        assert totals["replacement_refunds"] == 50 and totals["completed_refunds"] == 850
        assert totals["net_collections"] == 1500 and totals["pending_refunds"] == 0
        assert totals["reconciliation_count"] == 0
    assert repositories.team_sales_report_totals(business_today(), business_today(), d["sales"])["total_sales"] == 1450
    assert returns.return_settlement_totals(d["reseller"])["net_collections"] == 800
    assert returns.return_settlement_totals(d["supervisor"])["original_collections"] == 0


def test_completed_refund_does_not_hide_a_second_outstanding_obligation(lifecycle):  # noqa: F811
    d = lifecycle
    complete_refund(d, verified(d))
    verified(d)
    listed = repositories.list_orders(order_type="reseller", reseller_account_id=d["reseller"], status="refunded")
    assert len(listed) == 1 and listed[0]["refunded_amount"] == 200
    assert listed[0]["refund_status"] == "pending"
    assert repositories.count_orders(order_type="reseller", reseller_account_id=d["reseller"], status="refunded") == 1


def test_missing_refund_is_flagged_and_cannot_silently_complete(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d)
    with get_transaction_cursor() as cur:
        cur.execute("DELETE FROM order_refunds WHERE return_id=%s", (case,))
    assert returns.return_settlement_totals(d["sales"])["reconciliation_count"] == 1
    with pytest.raises(ValueError, match="missing its refund"):
        with get_transaction_cursor() as cur:
            returns._complete_if_ready(cur, case)
    assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "settlement_pending"


@pytest.mark.parametrize("field,value,message", [
    ("replacement_total", 201, "quote lines"),
    ("payment_amount", 49, "exact additional payment"),
])
def test_corrupted_quote_or_collection_cannot_release_stock(lifecycle, field, value, message):  # noqa: F811
    d = lifecycle
    case = replacement(d, 250)
    returns.record_return_payment(case, d["reseller"], [refund_proof()])
    returns.verify_return_payment(case, d["sales"], "TOPUP" + uuid4().hex, 50, "Confirmed the exact additional collection")
    with get_transaction_cursor() as cur:
        # Field names come only from this parametrization.
        cur.execute(f"UPDATE product_returns SET {field}=%s WHERE return_id=%s", (value, case))
    with pytest.raises(ValueError, match=message):
        returns.release_replacement(case, d["inventory"], "Must not release inconsistent settlement")
    assert fetch_one("SELECT count(*) n FROM replacement_releases WHERE return_id=%s", (case,))["n"] == 0
    assert returns.return_settlement_totals(d["sales"])["reconciliation_count"] == 1


def test_replacement_difference_refund_requires_matching_physical_release(lifecycle):  # noqa: F811
    d = lifecycle
    case = replacement(d, 150)
    returns.release_replacement(case, d["inventory"], "Released the accepted replacement product")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE replacement_releases SET quantity=quantity+1 WHERE return_id=%s", (case,))
    refund = fetch_one("SELECT order_refund_id FROM order_refunds WHERE return_id=%s", (case,))["order_refund_id"]
    with pytest.raises(ValueError, match="releases do not match"):
        repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    assert returns.return_settlement_totals(d["sales"])["reconciliation_count"] == 1


def test_verified_extra_collection_requires_its_payment_proof_at_release(lifecycle):  # noqa: F811
    d = lifecycle
    case = replacement(d, 250)
    returns.record_return_payment(case, d["reseller"], [refund_proof()])
    returns.verify_return_payment(case, d["sales"], "TOPUP" + uuid4().hex, 50, "Verified additional collection")
    with get_transaction_cursor() as cur:
        cur.execute("DELETE FROM return_attachments WHERE return_id=%s AND purpose='payment'", (case,))
    with pytest.raises(ValueError, match="missing its current payment proof"):
        returns.release_replacement(case, d["inventory"], "Cannot release without original collection evidence")
    assert returns.return_settlement_totals(d["sales"])["reconciliation_count"] == 1


def test_completed_legacy_return_can_record_verifier_without_reopening_or_repaying(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d)
    complete_refund(d, case)
    before = fetch_one("SELECT * FROM order_refunds WHERE return_id=%s", (case,))
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE product_returns SET verified_by_account_id=NULL WHERE return_id=%s", (case,))
    assert returns.return_settlement_totals(d["sales"])["reconciliation_count"] == 1
    assert "named verifier" in returns.get_product_return(case, d["inventory"])["reconciliation_flags"][0]
    returns.verify_product_return(case, d["inventory"], "Actually rechecked the recovered legacy receipts", str(uuid4()))
    assert fetch_one("SELECT * FROM order_refunds WHERE return_id=%s", (case,)) == before
    assert fetch_one("SELECT status FROM product_returns WHERE return_id=%s", (case,))["status"] == "completed"
    assert returns.return_settlement_totals(d["sales"])["reconciliation_count"] == 0


def test_refund_source_is_immutable_and_migration_replay_preserves_balances(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d)
    before = fetch_one("SELECT * FROM order_financial_reconciliation WHERE order_id=%s", (d["order"],))
    migration = Path("database/migrations/035_align_order_financial_settlements.sql").read_text(encoding="utf-8")
    with get_transaction_cursor() as cur:
        cur.execute(migration)
        cur.execute(migration)
    assert fetch_one("SELECT * FROM order_financial_reconciliation WHERE order_id=%s", (d["order"],)) == before
    with pytest.raises(psycopg2.Error, match="Locked refund"):
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE order_refunds SET return_id=NULL WHERE return_id=%s", (case,))


def test_fresh_baseline_and_full_migration_chain_agree(strict_inventory_database):  # noqa: F811
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('order_financial_reconciliation'::regclass) definition")
        before = cur.fetchone()["definition"]
        for migration in sorted(Path("database/migrations").glob("*.sql")):
            cur.execute(migration.read_text(encoding="utf-8"))
        cur.execute("SELECT pg_get_viewdef('order_financial_reconciliation'::regclass) definition")
        assert cur.fetchone()["definition"] == before


def test_original_collection_cannot_reuse_replacement_reference_or_proof(lifecycle):  # noqa: F811
    d = lifecycle
    case = replacement(d, 250)
    proof = refund_proof()
    returns.record_return_payment(case, d["reseller"], [proof])
    reference = "TOPUP" + uuid4().hex.upper()
    returns.verify_return_payment(case, d["sales"], reference, 50, "Verified replacement collection reference")
    order = paid_pending_order(d)
    with pytest.raises(psycopg2.errors.UniqueViolation, match="another collection ledger"):
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE orders SET payment_transaction_number=%s,payment_transaction_key=%s WHERE order_id=%s",
                        (reference, reference, order))
    with pytest.raises(psycopg2.errors.UniqueViolation, match="replacement collection"):
        with get_transaction_cursor() as cur:
            cur.execute("""INSERT INTO order_payment_proofs(order_id,uploaded_by_account_id,filename,content_type,
            content,size_bytes,checksum_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (order, d["reseller"], proof["filename"], proof["content_type"], proof["content"],
                         proof["size_bytes"], proof["checksum_sha256"]))


def test_concurrent_replacement_collections_cannot_reuse_one_payment_proof(lifecycle):  # noqa: F811
    d = lifecycle
    cases = [replacement(d, 250), replacement(d, 275)]
    proof = refund_proof()

    def upload(case):
        try:
            returns.record_return_payment(case, d["reseller"], [proof])
            return True
        except (ValueError, psycopg2.errors.UniqueViolation):
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(upload, cases)) == 1


def test_concurrent_original_and_replacement_collections_cannot_share_reference(lifecycle):  # noqa: F811
    d = lifecycle
    case = replacement(d, 250)
    returns.record_return_payment(case, d["reseller"], [refund_proof()])
    order = paid_pending_order(d)
    reference = "CONCURRENT" + uuid4().hex.upper()
    barrier = Barrier(2)

    def collect(original):
        barrier.wait(timeout=10)
        try:
            if original:
                with get_transaction_cursor() as cur:
                    cur.execute("UPDATE orders SET payment_transaction_number=%s,payment_transaction_key=%s WHERE order_id=%s",
                                (reference, reference, order))
            else:
                returns.verify_return_payment(case, d["sales"], reference, 50, "Simulated concurrent collection")
            return True
        except (ValueError, psycopg2.errors.UniqueViolation):
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(collect, [True, False])) == 1
    assert fetch_one("""SELECT (SELECT count(*) FROM orders WHERE payment_transaction_key=%s)
                     +(SELECT count(*) FROM product_returns WHERE payment_reference=%s) n""",
                     (reference, reference))["n"] == 1
