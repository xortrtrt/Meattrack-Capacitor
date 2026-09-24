from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
import pytest

from app import database, repositories


def _dsn_with_search_path(dsn: str, schema: str) -> str:
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@pytest.fixture
def strict_inventory_database():
    schema = f"test_inventory_{uuid.uuid4().hex}"
    try:
        admin = psycopg2.connect(database.DSN)
    except psycopg2.OperationalError:
        pytest.skip("Local PostgreSQL is unavailable")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    scoped_dsn = _dsn_with_search_path(database.DSN, schema)
    scoped = psycopg2.connect(scoped_dsn)
    baseline = Path("database/schema.sql").read_text(encoding="utf-8").strip()
    baseline = baseline.removeprefix("BEGIN;").strip().removesuffix("COMMIT;").strip()
    with scoped.cursor() as cursor:
        cursor.execute(baseline)
    scoped.commit()
    scoped.close()

    previous_pool = database.pool
    previous_ready = repositories.SYSTEM_TABLES_READY
    database.pool = ThreadedConnectionPool(1, 8, scoped_dsn)
    repositories.SYSTEM_TABLES_READY = False
    try:
        yield scoped_dsn
    finally:
        if database.pool is not None:
            database.pool.closeall()
        database.pool = previous_pool
        repositories.SYSTEM_TABLES_READY = previous_ready
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def _seed_fulfillment(scoped_dsn: str, *, batch_quantities: list[int], order_quantities: list[int]):
    with psycopg2.connect(scoped_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO accounts (account_type, name, email, password_hash, team_leader_role)
                VALUES ('team_leader', 'Inventory Test Leader', 'leader@test.local', 'hash', 'sales')
                RETURNING account_id;
            """)
            leader_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO resellers (
                    business_name, contact_person, email, contact_number,
                    reseller_status, team_leader_account_id
                )
                VALUES ('Test Reseller', 'Test Person', 'reseller@test.local', '09170000000', 'active', %s)
                RETURNING reseller_id;
            """, (leader_id,))
            reseller_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO accounts (account_type, reseller_id, name, email, password_hash)
                VALUES ('reseller', %s, 'Test Reseller', 'account@test.local', 'hash')
                RETURNING account_id;
            """, (reseller_id,))
            reseller_account_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO inventory_items (
                    item_type, category, name, description, unit, base_price,
                    pack_size, pack_size_unit, pack_content_status
                )
                VALUES ('finished_product', 'Pork', 'Test Product',
                        'Test Product - 500 g per pack.', 'pack', 100,
                        500, 'g', 'declared')
                RETURNING item_id;
            """)
            product_id = cursor.fetchone()[0]
            batch_ids = []
            for index, quantity in enumerate(batch_quantities, start=1):
                cursor.execute("""
                    INSERT INTO inventory_batches (
                        item_id, batch_code, source_type, quantity_received,
                        quantity_available, unit, received_date, expiry_date, quality_status
                    )
                    VALUES (%s, %s, 'direct_received', %s, %s, 'pack', CURRENT_DATE,
                            CURRENT_DATE + (%s * INTERVAL '1 day'), 'approved')
                    RETURNING batch_id;
                """, (product_id, f"TEST-{index}", quantity, quantity, index))
                batch_id = cursor.fetchone()[0]
                batch_ids.append(batch_id)
                cursor.execute("""
                    INSERT INTO inventory_movements (
                        item_id, affected_batch_id, movement_type, quantity_delta,
                        unit, balance_before, balance_after, actor_name, note
                    )
                    VALUES (%s, %s, 'opening_balance', %s, 'pack', 0, %s, 'MEATTRACK', 'Test opening');
                """, (product_id, batch_id, quantity, quantity))
            order_ids = []
            for quantity in order_quantities:
                cursor.execute("""
                    INSERT INTO orders (
                        order_type, reseller_id, created_by_account_id,
                        approved_by_account_id, approved_at, status, total_amount
                    )
                    VALUES ('reseller', %s, %s, %s, now(), 'approved', %s)
                    RETURNING order_id;
                """, (reseller_id, reseller_account_id, leader_id, quantity * 100))
                order_id = cursor.fetchone()[0]
                order_ids.append(order_id)
                cursor.execute("""
                    INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
                    VALUES (%s, %s, %s, 'pack', 100);
                """, (order_id, product_id, quantity))
    return leader_id, product_id, batch_ids, order_ids


@pytest.mark.postgres
def test_latest_baseline_accepts_all_numbered_migrations(strict_inventory_database):
    migration_sql = Path("database/migrations/002_strict_inventory.sql").read_text(encoding="utf-8")
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(migration_sql)
            cursor.execute("SELECT to_regclass('inventory_movements');")
            assert cursor.fetchone()[0] == "inventory_movements"


@pytest.mark.postgres
def test_concurrent_fulfillment_cannot_oversell(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[4, 4],
    )

    def fulfill(order_id):
        try:
            return repositories.decide_order(order_id, "fulfill", team_leader_account_id=leader_id)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(fulfill, order_ids))

    assert results.count(True) == 1
    assert sum("Not enough Test Product" in str(result) for result in results) == 1
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT quantity_available FROM inventory_batches;")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT status, COUNT(*) FROM orders GROUP BY status ORDER BY status;")
            assert dict(cursor.fetchall()) == {"approved": 1, "fulfilled": 1}
            cursor.execute("SELECT COUNT(*), SUM(quantity_delta) FROM inventory_movements WHERE movement_type = 'sale_fulfillment';")
            assert cursor.fetchone() == (1, -4)


@pytest.mark.postgres
def test_fulfillment_failure_rolls_back_every_batch_and_order(strict_inventory_database):
    leader_id, _, batch_ids, order_ids = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[2, 3],
        order_quantities=[5],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE FUNCTION fail_second_test_sale() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.movement_type = 'sale_fulfillment' AND NEW.affected_batch_id = %s THEN
                        RAISE EXCEPTION 'injected ledger failure';
                    END IF;
                    RETURN NEW;
                END $$;
            """, (batch_ids[1],))
            cursor.execute("""
                CREATE TRIGGER trg_fail_second_test_sale
                BEFORE INSERT ON inventory_movements
                FOR EACH ROW EXECUTE FUNCTION fail_second_test_sale();
            """)

    with pytest.raises(psycopg2.Error, match="injected ledger failure"):
        repositories.decide_order(order_ids[0], "fulfill", team_leader_account_id=leader_id)

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT quantity_available FROM inventory_batches ORDER BY batch_id;")
            assert [row[0] for row in cursor.fetchall()] == [2, 3]
            cursor.execute("SELECT status FROM orders WHERE order_id = %s;", (order_ids[0],))
            assert cursor.fetchone()[0] == "approved"
            cursor.execute("SELECT COUNT(*) FROM inventory_movements WHERE movement_type = 'sale_fulfillment';")
            assert cursor.fetchone()[0] == 0


@pytest.mark.postgres
def test_pending_order_cannot_be_fulfilled(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[2],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE orders SET status = 'pending' WHERE order_id = %s;", (order_ids[0],))

    with pytest.raises(ValueError, match="approved before"):
        repositories.decide_order(order_ids[0], "fulfill", team_leader_account_id=leader_id)

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT quantity_available FROM inventory_batches;")
            assert cursor.fetchone()[0] == 5


@pytest.mark.postgres
def test_database_rejects_invalid_units_fractional_packs_and_ledger_mutation(strict_inventory_database):
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.Error):
                cursor.execute("""
                    INSERT INTO inventory_items (item_type, name, unit, quantity_available)
                    VALUES ('raw_material', 'Invalid Unit', 'l', 1);
                """)
        connection.rollback()

    _, _, batch_ids, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[3],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.Error, match="immutable"):
                cursor.execute("UPDATE inventory_movements SET note = 'changed';")
        connection.rollback()
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.Error):
                cursor.execute(
                    "UPDATE inventory_batches SET quantity_available = 1.5 WHERE batch_id = %s;",
                    (batch_ids[0],),
                )


@pytest.mark.postgres
def test_production_atomically_records_consumption_and_output(strict_inventory_database):
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO accounts (account_type, name, email, password_hash, team_leader_role)
                VALUES ('team_leader', 'Production Leader', 'production@test.local', 'hash', 'inventory')
                RETURNING account_id;
            """)
            actor_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO inventory_items (item_type, category, name, unit, quantity_available)
                VALUES ('raw_material', 'meat', 'Pork', 'kg', 10)
                RETURNING item_id;
            """)
            material_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO inventory_items (
                    item_type, category, name, description, unit, base_price,
                    pack_size, pack_size_unit, pack_content_status
                )
                VALUES ('finished_product', 'Pork', 'Produced Product',
                        'Produced Product - 500 g per pack.', 'pack', 100,
                        500, 'g', 'declared')
                RETURNING item_id;
            """)
            product_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO product_recipes (product_item_id, material_item_id, quantity_required, unit)
                VALUES (%s, %s, 0.500, 'kg');
            """, (product_id, material_id))
            cursor.execute("""
                INSERT INTO inventory_movements (
                    item_id, movement_type, quantity_delta, unit,
                    balance_before, balance_after, actor_name, note
                ) VALUES (%s, 'opening_balance', 10, 'kg', 0, 10, 'MEATTRACK', 'Test opening');
            """, (material_id,))

    batch = repositories.produce_product(
        product_id,
        "PROD-TEST-1",
        "4",
        repositories.date.today() + repositories.timedelta(days=30),
        actor_account_id=actor_id,
    )

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT quantity_available FROM inventory_items WHERE item_id = %s;", (material_id,))
            assert cursor.fetchone()[0] == 8
            cursor.execute("SELECT quantity_available FROM inventory_batches WHERE batch_id = %s;", (batch["product_batch_id"],))
            assert cursor.fetchone()[0] == 4
            cursor.execute("""
                SELECT movement_type, quantity_delta, production_batch_id
                FROM inventory_movements
                WHERE production_batch_id = %s
                ORDER BY movement_type;
            """, (batch["product_batch_id"],))
            assert cursor.fetchall() == [
                ("production_consumption", -2, batch["product_batch_id"]),
                ("production_output", 4, batch["product_batch_id"]),
            ]


@pytest.mark.postgres
def test_reseller_catalog_hides_inactive_products(strict_inventory_database):
    _, product_id, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[],
    )

    assert [row["product_id"] for row in repositories.list_products(active_only=True)] == [product_id]
    assert repositories.count_products(active_only=True) == 1

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE inventory_items SET is_active = false WHERE item_id = %s;", (product_id,))

    assert repositories.list_products(active_only=True) == []
    assert repositories.count_products(active_only=True) == 0
    assert [row["product_id"] for row in repositories.list_products()] == [product_id]


@pytest.mark.postgres
def test_reseller_cart_and_checkout_reject_quantities_above_current_stock(strict_inventory_database):
    _, product_id, batch_ids, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]

    first = repositories.add_reseller_cart_item(reseller_account_id, product_id, "3")
    assert first["quantity"] == 3
    second = repositories.add_reseller_cart_item(reseller_account_id, product_id, "2")
    assert second["quantity"] == 5

    with pytest.raises(ValueError, match="Only 5 packs.*not reserved"):
        repositories.add_reseller_cart_item(reseller_account_id, product_id, "1")
    with pytest.raises(ValueError, match="Only 5 packs.*not reserved"):
        repositories.update_reseller_cart_item(reseller_account_id, product_id, "6")

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_batches SET quantity_available = 4 WHERE batch_id = %s;",
                (batch_ids[0],),
            )

    with pytest.raises(ValueError, match="Only 4 packs.*not reserved"):
        repositories.create_order_from_items(
            "reseller",
            [(product_id, "5")],
            account_id=reseller_account_id,
        )

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM orders;")
            assert cursor.fetchone()[0] == 0
