from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import psycopg2
import pytest

from app import database, repositories
from app.repositories import orders, superuser
from test_inventory_postgres import _seed_fulfillment, strict_inventory_database as strict_inventory_database

pytestmark = pytest.mark.postgres


@pytest.fixture
def corrected_stock(strict_inventory_database):
    with psycopg2.connect(strict_inventory_database) as conn:
        with conn.cursor() as cur:
            cur.execute(Path("database/migrations/027_owner_superuser.sql").read_text(encoding="utf-8"))
            cur.execute("INSERT INTO accounts(account_type,name,email,password_hash) VALUES ('owner','Test Owner','owner@audit.test','hash') RETURNING account_id")
            actor = cur.fetchone()[0]
    leader, product, batches, order_ids = _seed_fulfillment(strict_inventory_database, batch_quantities=[10], order_quantities=[8])
    return {"dsn": strict_inventory_database, "actor": actor, "leader": leader, "product": product, "batch": batches[0], "order": order_ids[0]}


def adjust(stock, delta, **kwargs):
    return superuser.compensate_inventory(stock["product"], delta, stock["actor"], "Verified stock adjustment", batch_id=kwargs.get("batch", stock["batch"]))


def balances(stock):
    with psycopg2.connect(stock["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT quantity_available FROM inventory_items WHERE item_id=%s", (stock["product"],))
            sentinel = cur.fetchone()[0]
            cur.execute("SELECT quantity_available,quantity_received FROM inventory_batches WHERE batch_id=%s", (stock["batch"],))
            return sentinel, *cur.fetchone()


def test_finished_corrections_use_batches_and_preserve_historical_movements(corrected_stock):
    stock = corrected_stock
    adjust(stock, "3")
    adjust(stock, "-2")
    assert balances(stock) == (0, 11, 13)
    with psycopg2.connect(stock["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT quantity_delta,balance_before,balance_after FROM inventory_movements WHERE movement_type='owner_correction' ORDER BY movement_id")
            assert cur.fetchall() == [(3, 10, 13), (-2, 13, 11)]
            cur.execute("SELECT quantity_delta,balance_before,balance_after FROM inventory_movements WHERE movement_type='opening_balance'")
            assert cur.fetchall() == [(10, 0, 10)]
            cur.execute("SELECT actor_role,before_state,after_state FROM activity_logs WHERE action='compensated_inventory' ORDER BY activity_log_id")
            logs = cur.fetchall()
            assert len(logs) == 2
            assert logs[0][0] == "owner"
            assert logs[0][1]["sellable_stock"] == "10.000"
            assert logs[0][2]["sellable_stock"] == "13.000"


def test_reservations_and_invalid_batch_are_protected(corrected_stock):
    stock = corrected_stock
    with database.get_transaction_cursor() as cur:
        orders._reserve_order_stock_cursor(cur, stock["order"], {stock["product"]: Decimal(8)})
    with pytest.raises(ValueError, match="reserved"):
        adjust(stock, "-3")
    with pytest.raises(ValueError, match="not found"):
        adjust(stock, "1", batch=999999)
    with pytest.raises(ValueError, match="whole packs"):
        adjust(stock, "0.001")
    assert balances(stock) == (0, 10, 10)


def test_stock_and_audit_roll_back_together(corrected_stock, monkeypatch):
    stock = corrected_stock
    def failed_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(superuser, "_audit_cursor", failed_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        adjust(stock, "2")
    assert balances(stock) == (0, 10, 10)
    with database.get_db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM inventory_movements WHERE movement_type='owner_correction'")
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("competitor", ["correction", "reservation", "fulfillment"])
def test_concurrent_adjustments_do_not_oversell(corrected_stock, competitor):
    stock = corrected_stock
    barrier = Barrier(2)
    def work(kind):
        barrier.wait(timeout=10)
        try:
            if kind == "correction":
                adjust(stock, "-7")
            elif kind == "reservation":
                with database.get_transaction_cursor() as cur:
                    orders._reserve_order_stock_cursor(cur, stock["order"], {stock["product"]: Decimal(8)})
            else:
                repositories.decide_order(stock["order"], "fulfill", team_leader_account_id=stock["leader"])
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(work, ["correction", competitor]))
    assert results.count(True) == 1
    assert balances(stock)[0] == 0
    assert balances(stock)[1] >= 0
    with database.get_db_cursor() as cur:
        cur.execute("SELECT COALESCE(SUM(quantity),0) AS n FROM order_stock_reservations WHERE product_id=%s", (stock["product"],))
        assert balances(stock)[1] >= cur.fetchone()["n"]


def test_duplicate_batch_codes_are_friendly_even_concurrently(corrected_stock):
    stock = corrected_stock
    barrier = Barrier(2)
    def receive(_):
        barrier.wait(timeout=10)
        try:
            repositories.add_product_batch(stock["product"], " duplicate-code ", "1", date.today() + timedelta(days=365), actor_account_id=stock["actor"])
            return "created"
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(receive, range(2)))
    assert sorted(results) == sorted(["created", "This batch code already exists."])
    with database.get_db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM inventory_batches WHERE batch_code='DUPLICATE-CODE'")
        assert cur.fetchone()["n"] == 1


def test_raw_material_resulting_balance_is_bounded(corrected_stock):
    stock = corrected_stock
    material = repositories.add_raw_inventory_item("Beef", "meat", "kg", "999999999.999", actor_account_id=stock["actor"])
    with pytest.raises(ValueError, match="exceed"):
        repositories.add_raw_inventory_quantity(material["raw_material_id"], "0.001", actor_account_id=stock["actor"])
    superuser.compensate_inventory(material["raw_material_id"], "-1", stock["actor"], "Verified raw stock loss")
    with database.get_db_cursor() as cur:
        cur.execute("SELECT quantity_available FROM inventory_items WHERE item_id=%s", (material["raw_material_id"],))
        assert cur.fetchone()["quantity_available"] == Decimal("999999998.999")


def test_resulting_received_capacity_and_quality_are_preserved(corrected_stock):
    stock = corrected_stock
    with database.get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quantity_received=999999999 WHERE batch_id=%s", (stock["batch"],))
    with pytest.raises(ValueError, match="exceed"):
        adjust(stock, "1")
    assert balances(stock) == (0, 10, 999999999)
    with database.get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET quantity_received=10,quality_status='pending',received_date=CURRENT_DATE-2,expiry_date=CURRENT_DATE-1 WHERE batch_id=%s", (stock["batch"],))
    adjust(stock, "1")
    with database.get_db_cursor() as cur:
        cur.execute("SELECT quality_status,expiry_date<CURRENT_DATE AS expired FROM inventory_batches WHERE batch_id=%s", (stock["batch"],))
        assert dict(cur.fetchone()) == {"quality_status": "pending", "expired": True}
    assert balances(stock) == (0, 11, 11)


def test_batch_ownership_and_order_line_overflow_are_domain_errors(corrected_stock):
    stock = corrected_stock
    with database.get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO inventory_items(item_type,category,name,description,unit,base_price,pack_size,pack_size_unit,pack_content_status)
                       SELECT item_type,category,'Other Product',description,unit,base_price,pack_size,pack_size_unit,pack_content_status
                       FROM inventory_items WHERE item_id=%s RETURNING item_id""", (stock["product"],))
        other_product = cur.fetchone()["item_id"]
        cur.execute("UPDATE inventory_items SET base_price=9999999999.99 WHERE item_id=%s", (stock["product"],))
    with pytest.raises(ValueError, match="batch not found"):
        superuser.compensate_inventory(other_product, "1", stock["actor"], "Incorrect batch ownership", batch_id=stock["batch"])
    with pytest.raises(ValueError, match="line total.*exceed"):
        repositories.create_order_from_items("team-leader", [(stock["product"], "2")], account_id=stock["leader"])
    assert balances(stock) == (0, 10, 10)
