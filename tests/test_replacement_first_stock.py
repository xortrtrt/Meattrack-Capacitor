"""Approval-time replacement stock reserves shared inventory."""
from decimal import Decimal
from uuid import uuid4

import pytest

from app import repositories
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database, _payment_submission  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_replacement_first_policy import request, data

pytestmark = pytest.mark.postgres


def scarce_stock(d):
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quantity_available=3 WHERE batch_id=%s", (d["batches"][1],))


def unpaid_order(d):
    return repositories.create_order_from_items("reseller", [(d["product"], Decimal(2))], "Synthetic competitor", d["reseller"])


@pytest.mark.parametrize("first", ["order", "replacement"])
def test_approval_stock_reservation_and_paid_order_shortage_refund_remain_separate(lifecycle, first):  # noqa: F811
    d = lifecycle
    scarce_stock(d)
    case = request(d)
    order = unpaid_order(d)
    proofs, details = _payment_submission("COMPETE" + uuid4().hex)
    details["payment_amount"] = Decimal(200)
    if first == "replacement":
        returns.review_product_return(case, d["supervisor"], True, "Reserve stock at approval")
        assert data(d, case)["has_reservations"]
        submitted = repositories.submit_order_payment(d["reseller"], order["order_id"], proofs, details)
        assert submitted["status"] == "rejected"
        refund = fetch_one("SELECT * FROM order_refunds WHERE order_id=%s", (order["order_id"],))
        assert refund["payment_review_required"] and refund["amount"] == 200
        assert fetch_one("SELECT count(*) n FROM order_payment_proofs WHERE order_id=%s", (order["order_id"],))["n"] == 1
        assert fetch_one("SELECT count(*) n FROM order_stock_reservations WHERE order_id=%s", (order["order_id"],))["n"] == 0
        assert fetch_one("SELECT count(*) n FROM order_refunds WHERE order_id=%s", (order["order_id"],))["n"] == 1
    else:
        submitted = repositories.submit_order_payment(d["reseller"], order["order_id"], proofs, details)
        assert submitted["status"] == "pending"
        returns.review_product_return(case, d["supervisor"], True, "Stock was reserved by the paid order")
        result = data(d, case)
        assert result["resolution"] == "refund" and result["refund_converted_at"]
        assert not result["has_reservations"]
    assert not fetch_all("SELECT * FROM inventory_batches WHERE quantity_available<0")


def test_two_replacements_racing_last_stock_have_only_one_winner(lifecycle):  # noqa: F811
    d = lifecycle
    scarce_stock(d)
    cases = [request(d), request(d)]
    for case in cases:
        returns.review_product_return(case, d["supervisor"], True, "Approve against current shared stock")
    assert sum(bool(data(d, case)["has_reservations"]) for case in cases) == 1
    assert sum(data(d, case)["resolution"] == "refund" for case in cases) == 1
    assert repositories.product_by_id(d["product"])["available"] == 1
