"""Inventory Logs report historical product totals; Product Details retains batches."""

from decimal import Decimal

import pytest

from app import repositories
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns
from test_inventory_postgres import _seed_fulfillment, strict_inventory_database  # noqa: F401

pytestmark = pytest.mark.postgres


def test_stock_log_totals_span_batches_before_search_sort_and_pagination(strict_inventory_database):  # noqa: F811
    sales, product, batches, orders = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[20] * 5, order_quantities=[6, 6]
    )
    for order in orders:
        repositories.decide_order(order, "fulfill", team_leader_account_id=sales)

    # A second item's decimal movements must never contribute to this product.
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO inventory_items
            (item_type,category,name,unit,quantity_available)
            VALUES ('raw_material','Raw','Test ingredient','kg',3.75) RETURNING item_id""")
        material = cur.fetchone()["item_id"]
        cur.execute("""INSERT INTO inventory_movements
            (item_id,movement_type,quantity_delta,unit,balance_before,balance_after,actor_name)
            VALUES (%s,'opening_balance',2.5,'kg',0,2.5,'Test'),
                   (%s,'raw_receipt',1.25,'kg',2.5,3.75,'Test')""", (material, material))

    opening = repositories.list_inventory_movements(q="Test Product", category="opening", sort="oldest")
    assert [row["balance_after"] for row in opening] == [20, 40, 60, 80, 100]
    assert [row["balance_before"] for row in opening] == [0, 20, 40, 60, 80]
    sales_rows = repositories.list_inventory_movements(q="sale_fulfillment", category="sales", sort="oldest")
    assert [row["balance_after"] for row in sales_rows] == [94, 88]
    for sort, expected in (("newest", [88, 94]), ("oldest", [94, 88])):
        for page, balance in enumerate(expected, 1):
            row = repositories.list_inventory_movements(
                q="Test Product", category="sales", sort=sort, page=page, page_size=1
            )[0]
            assert row["balance_after"] == balance
            assert row["balance_before"] == balance + 6
    assert repositories.count_inventory_movements(q="Test Product", category="sales") == 2
    assert repositories.product_by_id(product)["available"] == 88
    raw = repositories.list_inventory_movements(q="Test ingredient", category="receipts")[0]
    assert raw["balance_before"] == Decimal("2.5") and raw["balance_after"] == Decimal("3.75")

    # The audit record and Product Details must still show the batch's 8 packs.
    assert fetch_one("""SELECT balance_after FROM inventory_movements
        WHERE item_id=%s ORDER BY created_at DESC,movement_id DESC LIMIT 1""", (product,))["balance_after"] == 8
    details = product_returns.product_lifecycle_details(product)
    assert details["movements"][0]["balance_after"] == 8
    assert next(batch for batch in details["batches"] if batch["batch_id"] == batches[0])["quantity_available"] == 8
