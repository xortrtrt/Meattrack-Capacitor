"""Shared stock must be protected across order payment and replacement preparation."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4

import pytest

from app import repositories
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database, _payment_submission  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_replacement_workflow import approve_receive
from test_replacement_first_policy import request, data, convert

pytestmark = pytest.mark.postgres


def scarce_stock(d):
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quantity_available=3 WHERE batch_id=%s", (d["batches"][1],))


def unpaid_order(d):
    return repositories.create_order_from_items("reseller", [(d["product"], Decimal(2))], "Synthetic competitor", d["reseller"])


@pytest.mark.parametrize("first", ["order", "replacement"])
def test_first_committed_reservation_wins_and_loser_can_retry_after_restock(lifecycle, first):  # noqa: F811
    d = lifecycle
    scarce_stock(d)
    case = request(d)
    approve_receive(d, case)
    order = unpaid_order(d)
    proofs, details = _payment_submission("COMPETE" + uuid4().hex)
    details["payment_amount"] = Decimal(200)
    ready = Event()

    def pay_order():
        if first != "order":
            assert ready.wait(15)
        try:
            return repositories.submit_order_payment(d["reseller"], order["order_id"], proofs, details)["status"] == "pending"
        except ValueError:
            return False
        finally:
            if first == "order":
                ready.set()

    def prepare():
        if first != "replacement":
            assert ready.wait(15)
        try:
            return returns.reserve_replacement(case, d["inventory"], "Competing replacement preparation")
        finally:
            if first == "replacement":
                ready.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        order_job, replacement_job = pool.submit(pay_order), pool.submit(prepare)
        outcomes = {"order": order_job.result(20), "replacement": replacement_job.result(20)}
    assert outcomes[first] and sum(bool(v) for v in outcomes.values()) == 1
    assert repositories.product_by_id(d["product"])["available"] == 1
    if first == "replacement":
        # Payment evidence cannot be discarded when stock is lost after payment.
        # Keep the existing rejected-paid-order refund review instead of charging again.
        assert fetch_one("SELECT status FROM orders WHERE order_id=%s", (order["order_id"],))["status"] == "rejected"
        assert fetch_one("SELECT count(*) n FROM order_payment_proofs WHERE order_id=%s", (order["order_id"],))["n"] == 1
        refund = fetch_one("SELECT * FROM order_refunds WHERE order_id=%s", (order["order_id"],))
        assert refund["payment_review_required"] and refund["amount"] == 200
        assert fetch_one("SELECT count(*) n FROM order_stock_reservations WHERE order_id=%s", (order["order_id"],))["n"] == 0
        convert(d, case)
        assert repositories.product_by_id(d["product"])["available"] == 3
        assert repositories.submit_order_payment(d["reseller"], order["order_id"], proofs, details)["status"] == "rejected"
        assert fetch_one("SELECT count(*) n FROM order_refunds WHERE order_id=%s", (order["order_id"],))["n"] == 1
    else:
        from datetime import timedelta
        from app.business_time import business_today
        repositories.add_product_batch(d["product"], "RESTOCK" + uuid4().hex[:8], 2, business_today() + timedelta(days=30))
        assert returns.reserve_replacement(case, d["inventory"], "Retry after valid restock")
    assert not fetch_all("SELECT * FROM inventory_batches WHERE quantity_available<0")


def test_two_replacements_racing_last_stock_have_only_one_winner(lifecycle):  # noqa: F811
    d = lifecycle
    scarce_stock(d)
    cases = [request(d), request(d)]
    for case in cases:
        approve_receive(d, case)
    barrier = Barrier(2)

    def prepare(case):
        barrier.wait(10)
        return returns.reserve_replacement(case, d["inventory"], "Competing for last valid packs")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(prepare, cases)) == 1
    assert sum(bool(data(d, case)["has_reservations"]) for case in cases) == 1
    assert repositories.product_by_id(d["product"])["available"] == 1
