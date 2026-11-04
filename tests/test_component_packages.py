from datetime import timedelta
from decimal import Decimal

import pytest
from app import repositories
from app.business_time import business_today
from app.database import get_transaction_cursor, fetch_one
from app.repositories.inventory.packages import REFERENCE_PRODUCTS, REFERENCE_PACKAGES, configure_reference_packages
from test_inventory_postgres import strict_inventory_database, _payment_submission  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture
def bundle_catalog(lifecycle):  # noqa: F811
    d = lifecycle
    products = {}
    with get_transaction_cursor() as cur:
        for index, name in enumerate(list(REFERENCE_PRODUCTS) + list(REFERENCE_PACKAGES)):
            price = [1399, 4997, 27997][index - 16] if index >= 16 else 10 + index
            cur.execute(
                """INSERT INTO inventory_items(item_type,name,category,unit,base_price,pack_size,pack_size_unit,pack_content_status)
                VALUES('finished_product',%s,'Pork','pack',%s,500,'g','declared') RETURNING item_id""",
                (name, price),
            )
            pid = cur.fetchone()["item_id"]
            products[name] = pid
            cur.execute(
                """INSERT INTO inventory_batches(item_id,batch_code,quantity_received,quantity_available,unit,expiry_date)
                VALUES(%s,%s,50,50,'pack',%s)""",
                (pid, f"PKG-TEST-{index}", business_today() + timedelta(days=90)),
            )
    configure_reference_packages(d["inventory"], archive_legacy=True)
    return d, products


def test_conversion_inclusions_availability_and_no_standalone_balance(bundle_catalog):
    d, p = bundle_catalog
    for name, total in [("Trial Package", 16), ("Resellers Package", 70), ("Area Distributor's Package", 400)]:
        item = repositories.product_by_id(p[name])
        assert item["category"] == "Package" and item["total_packs"] == total
        assert (
            fetch_one("SELECT SUM(quantity_available) q FROM inventory_batches WHERE item_id=%s", (p[name],))["q"] == 0
        )
    assert repositories.product_by_id(p["Trial Package"])["available"] == 50
    assert repositories.product_by_id(p["Resellers Package"])["available"] == 8
    assert repositories.product_by_id(p["Area Distributor's Package"])["available"] == 2
    with pytest.raises(ValueError):
        repositories.add_product_batch(
            p["Trial Package"],
            "BAD-PACKAGE",
            "1",
            business_today() + timedelta(days=60),
            actor_account_id=d["inventory"],
        )
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE item_id=%s", (business_today(), p["Bacon"]))
    assert repositories.product_by_id(p["Trial Package"])["available"] == 0

    # Historical package purchases retain their recorded credit, but cannot reserve standalone stock.
    from app.repositories.replacement_workflow import price_selection
    from app.repositories.inventory.allocation import _plan_fefo_allocations
    originals=[{'allocation_id':1,'product_id':p['Trial Package'],'quantity':1,'unit_price':Decimal('1399')}]
    with get_transaction_cursor() as cur:
        quote=price_selection(cur,originals,[(p['Trial Package'],1)],allow_inactive_originals=True)
        assert Decimal(quote['credit']) == 1399
    with get_transaction_cursor() as cur, pytest.raises(ValueError,match='Not enough'):
        _plan_fefo_allocations(cur,{p['Trial Package']:Decimal(1)})


def test_package_checkout_exact_total_payment_reservation_and_fefo(bundle_catalog):
    d, p = bundle_catalog
    repositories.add_reseller_cart_item(d["reseller"], p["Trial Package"], "2")
    repositories.add_reseller_cart_item(d["reseller"], p["Bacon"], "3")
    order = repositories.checkout_reseller_cart(d["reseller"])
    oid = order["order_id"]
    expected = Decimal(1399 * 2 + 14 * 3)
    assert order["total_amount"] == expected
    assert fetch_one("SELECT SUM(line_total) t FROM order_items WHERE order_id=%s", (oid,))["t"] == expected
    assert not fetch_one(
        "SELECT 1 FROM order_items oi JOIN inventory_items i ON i.item_id=oi.product_id WHERE order_id=%s AND category='Package'",
        (oid,),
    )
    assert fetch_one("SELECT COUNT(*) n FROM reseller_cart_items WHERE account_id=%s", (d["reseller"],))["n"] == 0
    assert not fetch_one("SELECT 1 FROM order_stock_reservations WHERE order_id=%s", (oid,))
    proof, payment = _payment_submission("component-package")
    payment["payment_amount"] = expected
    repositories.submit_order_payment(d["reseller"], oid, proof, payment)
    held = fetch_one(
        "SELECT quantity FROM order_stock_reservations WHERE order_id=%s AND product_id=%s", (oid, p["Bacon"])
    )
    assert held["quantity"] == 5
    assert repositories.product_by_id(p["Trial Package"])["available"] == 45
    repositories.decide_order(oid, "approve", team_leader_account_id=d["sales"])
    repositories.decide_order(oid, "fulfill", team_leader_account_id=d["sales"])
    assert (
        fetch_one("SELECT SUM(quantity_available) q FROM inventory_batches WHERE item_id=%s", (p["Bacon"],))["q"] == 45
    )
    assert (
        fetch_one(
            "SELECT COUNT(*) n FROM fulfillment_allocations a JOIN order_items oi USING(order_item_id) WHERE oi.order_id=%s",
            (oid,),
        )["n"]
        >= 16
    )
    assert fetch_one("SELECT quantity FROM order_packages WHERE order_id=%s", (oid,))["quantity"] == 2
    assert not fetch_one("SELECT 1 FROM order_stock_reservations WHERE order_id=%s", (oid,))

    from app.repositories.product_returns import returnable_allocations
    returnable = returnable_allocations(oid, d['reseller'])
    assert sum(Decimal(str(row['quantity'])) * Decimal(str(row['unit_price'])) for row in returnable) == expected
    import psycopg2
    from tools.transaction_reset import reset_transactions
    with psycopg2.connect(d['dsn']) as connection:
        result = reset_transactions(connection, apply=False)
        assert result['cleared']['order_packages'] == 1 and not result['committed']
    assert fetch_one('SELECT quantity FROM order_packages WHERE order_id=%s',(oid,))['quantity'] == 2


def test_combined_package_cart_shortage_is_atomic(bundle_catalog):
    d, p = bundle_catalog
    repositories.add_reseller_cart_item(d["reseller"], p["Trial Package"], "50")
    repositories.add_reseller_cart_item(d["reseller"], p["Bacon"], "1")
    with pytest.raises(ValueError):
        repositories.checkout_reseller_cart(d["reseller"])
    assert fetch_one("SELECT COUNT(*) n FROM reseller_cart_items WHERE account_id=%s", (d["reseller"],))["n"] == 2
    assert not fetch_one("SELECT 1 FROM order_packages")
