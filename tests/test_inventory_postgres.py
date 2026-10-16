from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
import pytest
from fastapi.testclient import TestClient

from app import database, main, repositories
from app.portals import reseller as reseller_portal
from app.portals import shared as shared_portal


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
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL integration tests are required but PostgreSQL is unavailable")
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
    database.pool = ThreadedConnectionPool(1, 8, scoped_dsn)
    try:
        yield scoped_dsn
    finally:
        if database.pool is not None:
            database.pool.closeall()
        database.pool = previous_pool
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def _seed_fulfillment(
    scoped_dsn: str, *, batch_quantities: list[int], order_quantities: list[int], order_status: str = "approved"
):
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
            for order_index, quantity in enumerate(order_quantities, start=1):
                transaction_key = f"SEEDREF{order_index:08d}"
                cursor.execute("""
                    INSERT INTO orders (
                        order_type, reseller_id, created_by_account_id,
                        team_leader_account_id, status, total_amount,
                        payment_transaction_number, payment_transaction_key,
                        payment_paid_at, payment_amount, payment_ocr_confidence,
                        payment_extraction_method, payment_confirmed_at, payment_ocr_engine
                    )
                    VALUES ('reseller', %s, %s, %s, 'pending', %s,
                            %s, %s, now(), 500.00, 0.9, 'ocr_confirmed', now(), 'tesseract-test')
                    RETURNING order_id;
                """, (
                    reseller_id, reseller_account_id, leader_id, quantity * 100,
                    transaction_key, transaction_key,
                ))
                order_id = cursor.fetchone()[0]
                order_ids.append(order_id)
                cursor.execute("""
                    INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
                    VALUES (%s, %s, %s, 'pack', 100);
                """, (order_id, product_id, quantity))
                proof_content = f"seed-proof-{order_index}".encode()
                cursor.execute("""
                    INSERT INTO order_payment_proofs (
                        order_id, uploaded_by_account_id, filename, content_type,
                        content, size_bytes, checksum_sha256
                    ) VALUES (%s, %s, %s, 'image/png', %s, %s, %s);
                """, (
                    order_id,
                    reseller_account_id,
                    f"seed-proof-{order_index}.png",
                    proof_content,
                    len(proof_content),
                    hashlib.sha256(proof_content).hexdigest(),
                ))
                if order_status in {"approved", "fulfilled"}:
                    cursor.execute(
                        """
                        UPDATE orders
                        SET status = 'approved', approved_by_account_id = %s, approved_at = now()
                        WHERE order_id = %s;
                        """,
                        (leader_id, order_id),
                    )
                if order_status == "fulfilled":
                    cursor.execute(
                        "UPDATE orders SET status = 'fulfilled', fulfilled_at = now() WHERE order_id = %s;",
                        (order_id,),
                    )
                if order_status == "rejected":
                    cursor.execute("UPDATE orders SET status = 'rejected' WHERE order_id = %s;", (order_id,))
    return leader_id, product_id, batch_ids, order_ids


def _payment_submission(label: str, *, transaction_number: str | None = None):
    content = f"receipt-image-{label}".encode()
    checksum = hashlib.sha256(content).hexdigest()
    clean_reference = transaction_number or f"TXN{hashlib.sha256(label.encode()).hexdigest()[:16].upper()}"
    return (
        [{
            "filename": f"receipt-{label}.png",
            "content_type": "image/png",
            "content": content,
            "size_bytes": len(content),
            "checksum_sha256": checksum,
        }],
        {
            "transaction_number": clean_reference,
            "transaction_key": "".join(character for character in clean_reference.upper() if character.isalnum()),
            "paid_at": datetime.now(timezone.utc),
            "payment_amount": Decimal("500.00"),
            "ocr_confidence": 0.91,
            "extraction_method": "ocr_confirmed",
            "confirmed_at": datetime.now(timezone.utc),
            "ocr_engine": "tesseract-test",
        },
    )


@pytest.mark.postgres
def test_latest_baseline_accepts_all_numbered_migrations(strict_inventory_database):
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            for migration in sorted(Path("database/migrations").glob("*.sql")):
                cursor.execute(migration.read_text(encoding="utf-8"))
            cursor.execute("SELECT to_regclass('inventory_movements');")
            assert cursor.fetchone()[0] == "inventory_movements"


@pytest.mark.postgres
def test_database_rejects_new_reseller_order_without_confirmed_payment(strict_inventory_database):
    leader_id, _, _, _ = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[5], order_quantities=[]
    )
    connection = psycopg2.connect(strict_inventory_database)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account_id, reseller_id FROM accounts WHERE email = 'account@test.local';")
            account_id, reseller_id = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO orders (
                    order_type, reseller_id, created_by_account_id,
                    team_leader_account_id, status, total_amount
                ) VALUES ('reseller', %s, %s, %s, 'pending', 100);
                """,
                (reseller_id, account_id, leader_id),
            )
        with pytest.raises(psycopg2.Error, match="confirmed payment details and proof"):
            connection.commit()
        connection.rollback()
    finally:
        connection.close()


@pytest.mark.postgres
def test_legacy_unpaid_pending_reseller_order_can_be_rejected(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[5], order_quantities=[1], order_status="pending"
    )
    order_id = order_ids[0]
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE orders DISABLE TRIGGER trg_require_reseller_order_payment;")
            cursor.execute(
                """
                UPDATE orders
                SET payment_transaction_number = NULL,
                    payment_transaction_key = NULL,
                    payment_paid_at = NULL,
                    payment_amount = NULL,
                    payment_ocr_confidence = NULL,
                    payment_extraction_method = NULL,
                    payment_confirmed_at = NULL,
                    payment_ocr_engine = NULL
                WHERE order_id = %s;
                """,
                (order_id,),
            )
            cursor.execute("DELETE FROM order_payment_proofs WHERE order_id = %s;", (order_id,))
            cursor.execute("ALTER TABLE orders ENABLE TRIGGER trg_require_reseller_order_payment;")

    assert repositories.decide_order(
        order_id,
        "reject",
        team_leader_account_id=leader_id,
        rejection_reason="The submitted receipt could not be verified.",
    )

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM orders WHERE order_id = %s;", (order_id,))
            assert cursor.fetchone()[0] == "rejected"


@pytest.mark.postgres
def test_approved_reseller_order_cannot_be_rejected(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[5], order_quantities=[1], order_status="approved"
    )
    order_id = order_ids[0]

    assert not repositories.decide_order(
        order_id,
        "reject",
        team_leader_account_id=leader_id,
        rejection_reason="The submitted receipt could not be verified.",
    )

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM orders WHERE order_id = %s;", (order_id,))
            assert cursor.fetchone()[0] == "approved"


@pytest.mark.postgres
def test_order_decisions_create_reseller_system_messages(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[10],
        order_quantities=[1, 1, 1],
        order_status="pending",
    )

    assert repositories.decide_order(order_ids[0], "approve", team_leader_account_id=leader_id)
    assert repositories.decide_order(
        order_ids[1],
        "reject",
        team_leader_account_id=leader_id,
        rejection_reason="The payment reference does not match the uploaded receipt.",
    )
    assert repositories.decide_order(order_ids[2], "approve", team_leader_account_id=leader_id)
    assert repositories.decide_order(order_ids[2], "fulfill", team_leader_account_id=leader_id)

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT osm.order_system_message_id, osm.event_type, osm.title, osm.message,
                       osm.rejection_reason, osm.order_id
                FROM order_system_messages osm
                JOIN accounts a ON a.account_id = osm.reseller_account_id
                WHERE a.email = 'account@test.local'
                ORDER BY osm.order_system_message_id;
                """
            )
            messages = cursor.fetchall()
            assert [message[1] for message in messages] == [
                "approved",
                "rejected",
                "approved",
                "fulfilled",
            ]
            assert [message[2] for message in messages] == [
                "Order approved",
                "Order rejected",
                "Order approved",
                "Order fulfilled",
            ]
            assert messages[1][3] == f"Your reseller order #{order_ids[1]} was rejected."
            assert messages[1][4] == "The payment reference does not match the uploaded receipt."
            assert [message[5] for message in messages] == [
                order_ids[0],
                order_ids[1],
                order_ids[2],
                order_ids[2],
            ]
            cursor.execute(
                """
                SELECT n.source_id, n.target_url, n.title
                FROM notifications n
                JOIN notification_recipients nr ON nr.notification_id = n.notification_id
                JOIN accounts a ON a.account_id = nr.account_id
                WHERE a.email = 'account@test.local'
                  AND n.category = 'order'
                  AND n.source_type = 'order_system_messages'
                ORDER BY n.notification_id;
                """
            )
            notification_rows = cursor.fetchall()
            assert [row[0] for row in notification_rows] == [message[0] for message in messages]
            assert all(
                row[1] == f"/portal/reseller/inbox?system_message={row[0]}#system-message-{row[0]}"
                for row in notification_rows
            )
            assert [row[2] for row in notification_rows] == [message[2] for message in messages]
            cursor.execute("SELECT rejection_reason FROM orders WHERE order_id = %s;", (order_ids[1],))
            assert cursor.fetchone()[0] == "The payment reference does not match the uploaded receipt."


@pytest.mark.postgres
def test_pending_order_rejection_requires_explanation(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[1],
        order_status="pending",
    )

    with pytest.raises(ValueError, match="at least 5 characters"):
        repositories.decide_order(order_ids[0], "reject", team_leader_account_id=leader_id)

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status, rejection_reason FROM orders WHERE order_id = %s;", (order_ids[0],))
            assert cursor.fetchone() == ("pending", None)
            cursor.execute("SELECT COUNT(*) FROM order_system_messages WHERE order_id = %s;", (order_ids[0],))
            assert cursor.fetchone()[0] == 0


@pytest.mark.postgres
def test_hardening_constraints_reject_invalid_transitions_and_owner_changes(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[10], order_quantities=[1]
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE orders SET status = 'fulfilled' WHERE order_id = %s;", (order_ids[0],))
            with pytest.raises(psycopg2.Error, match="terminal"):
                cursor.execute("UPDATE orders SET status = 'approved' WHERE order_id = %s;", (order_ids[0],))
            connection.rollback()

            with pytest.raises(psycopg2.Error, match="ownership is immutable"):
                cursor.execute("UPDATE orders SET team_leader_account_id = NULL WHERE order_id = %s;", (order_ids[0],))
            connection.rollback()

            cursor.execute(
                """
                INSERT INTO inquiries (name, contact_number, email, business_name, status)
                VALUES ('Person', '0917', 'person@test.local', 'Store', 'rejected') RETURNING inquiry_id;
                """
            )
            inquiry_id = cursor.fetchone()[0]
            with pytest.raises(psycopg2.Error, match="terminal"):
                cursor.execute("UPDATE inquiries SET status = 'approved' WHERE inquiry_id = %s;", (inquiry_id,))


@pytest.mark.postgres
def test_payment_proof_limit_and_pending_only_are_database_enforced(strict_inventory_database):
    leader_id, _, _, order_ids = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[10], order_quantities=[1]
    )
    order_id = order_ids[0]
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.Error, match="awaiting payment or review"):
                cursor.execute(
                    """
                    INSERT INTO order_payment_proofs
                        (order_id, filename, content_type, content, size_bytes, checksum_sha256)
                    VALUES (%s, 'proof.png', 'image/png', '\\x00', 1, %s);
                    """,
                    (order_id, "0" * 64),
                )


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
        order_status="pending",
    )

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

    with pytest.raises(ValueError, match="Only 5 packs.*reserved by orders awaiting payment"):
        repositories.add_reseller_cart_item(reseller_account_id, product_id, "1")
    with pytest.raises(ValueError, match="Only 5 packs.*reserved by orders awaiting payment"):
        repositories.update_reseller_cart_item(reseller_account_id, product_id, "6")

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory_batches SET quantity_available = 4 WHERE batch_id = %s;",
                (batch_ids[0],),
            )

    with pytest.raises(ValueError, match="Only 4 packs.*reserved by orders awaiting payment"):
        repositories.create_order_from_items(
            "reseller",
            [(product_id, "5")],
            account_id=reseller_account_id,
        )

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM orders;")
            assert cursor.fetchone()[0] == 0


@pytest.mark.postgres
def test_reseller_cart_checkout_accepts_sales_supervisor_assignment(strict_inventory_database):
    leader_id, product_id, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE accounts SET team_leader_role = 'sales-supervisor' WHERE account_id = %s;",
                (leader_id,),
            )
            cursor.execute("SELECT account_id, reseller_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id, reseller_id = cursor.fetchone()

    assert leader_id in {row["account_id"] for row in repositories.list_team_leader_accounts()}
    assignment = repositories.set_reseller_team_leader(reseller_id, leader_id)
    assert assignment["team_leader_account_id"] == leader_id

    repositories.add_reseller_cart_item(reseller_account_id, product_id, "2")
    order = repositories.checkout_reseller_cart(reseller_account_id, "Supervisor-owned order")

    assert order["status"] == "to_pay"
    assert order["total_amount"] == 200.0
    assert order["payment_due_at"] is not None
    assert repositories.reseller_cart_count(reseller_account_id) == 0

    proofs, payment = _payment_submission("supervisor")
    submitted = repositories.submit_order_payment(
        reseller_account_id, order["order_id"], proofs, payment
    )
    assert submitted["status"] == "pending"

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT team_leader_account_id, payment_transaction_number,
                       payment_paid_at, payment_amount, payment_extraction_method,
                       (SELECT count(*) FROM order_payment_proofs WHERE order_id = orders.order_id)
                FROM orders WHERE order_id = %s;
                """,
                (order["order_id"],),
            )
            stored = cursor.fetchone()
            assert stored[0] == leader_id
            assert stored[1] == payment["transaction_number"]
            assert stored[2] is not None
            assert stored[3] == payment["payment_amount"]
            assert stored[4] == "ocr_confirmed"
            assert stored[5] == 1


@pytest.mark.postgres
def test_reseller_cannot_reuse_receipt_or_transaction_but_other_reseller_can(strict_inventory_database):
    leader_id, product_id, _, _ = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[10], order_quantities=[]
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            first_account_id = cursor.fetchone()[0]

    proofs, payment = _payment_submission("reuse-check", transaction_number="SHARED-REF-123456")
    repositories.add_reseller_cart_item(first_account_id, product_id, "1")
    first_order = repositories.checkout_reseller_cart(first_account_id)
    repositories.submit_order_payment(first_account_id, first_order["order_id"], proofs, payment)

    repositories.add_reseller_cart_item(first_account_id, product_id, "1")
    second_order = repositories.checkout_reseller_cart(first_account_id)
    _, different_payment = _payment_submission("different-reference")
    with pytest.raises(ValueError, match="screenshot has already been used"):
        repositories.submit_order_payment(
            first_account_id, second_order["order_id"], proofs, different_payment
        )
    assert repositories.reseller_cart_count(first_account_id) == 0

    different_proof, _ = _payment_submission("different-screenshot")
    with pytest.raises(ValueError, match="transaction number has already been used"):
        repositories.submit_order_payment(
            first_account_id, second_order["order_id"], different_proof, payment
        )
    assert repositories.reseller_cart_count(first_account_id) == 0

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO resellers (
                    business_name, contact_person, email, contact_number,
                    reseller_status, team_leader_account_id
                ) VALUES ('Other Reseller', 'Other Person', 'other@test.local',
                          '09171111111', 'active', %s)
                RETURNING reseller_id;
                """,
                (leader_id,),
            )
            other_reseller_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO accounts (account_type, reseller_id, name, email, password_hash)
                VALUES ('reseller', %s, 'Other Reseller', 'other-account@test.local', 'hash')
                RETURNING account_id;
                """,
                (other_reseller_id,),
            )
            other_account_id = cursor.fetchone()[0]

    repositories.add_reseller_cart_item(other_account_id, product_id, "1")
    other_proof, _ = _payment_submission("other-reseller")
    other_order = repositories.checkout_reseller_cart(other_account_id)
    other_payment = repositories.submit_order_payment(
        other_account_id, other_order["order_id"], other_proof, payment
    )
    assert other_payment["status"] == "pending"


@pytest.mark.postgres
def test_concurrent_checkout_allows_only_one_active_payment_window(strict_inventory_database):
    _, first_product_id, _, _ = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[10], order_quantities=[]
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO inventory_items (
                    item_type, category, name, description, unit, base_price,
                    pack_size, pack_size_unit, pack_content_status
                ) VALUES ('finished_product', 'Pork', 'Concurrent Product',
                          'Second concurrency product.', 'pack', 110, 500, 'g', 'declared')
                RETURNING item_id;
                """
            )
            second_product_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO inventory_batches (
                    item_id, batch_code, source_type, quantity_received,
                    quantity_available, unit, received_date, expiry_date, quality_status
                ) VALUES (%s, 'CONCURRENT-TEST', 'direct_received', 10, 10, 'pack',
                          CURRENT_DATE, CURRENT_DATE + INTERVAL '7 days', 'approved');
                """,
                (second_product_id,),
            )

    repositories.add_reseller_cart_item(reseller_account_id, first_product_id, "1")
    repositories.add_reseller_cart_item(reseller_account_id, second_product_id, "1")
    def checkout(product_id):
        try:
            return repositories.checkout_reseller_cart(
                reseller_account_id,
                product_ids=[product_id],
            )
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(checkout, first_product_id)
        second = executor.submit(checkout, second_product_id)
        results = [first.result(), second.result()]

    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum("still awaiting payment" in str(result) for result in results) == 1
    assert repositories.reseller_cart_count(reseller_account_id) == 1
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM orders;")
            assert cursor.fetchone()[0] == 1


@pytest.mark.postgres
def test_unpaid_order_expires_after_fifteen_minutes_and_releases_stock(strict_inventory_database):
    _, product_id, batch_ids, _ = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[5], order_quantities=[]
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]

    repositories.add_reseller_cart_item(reseller_account_id, product_id, "2")
    order = repositories.checkout_reseller_cart(reseller_account_id)
    assert order["status"] == "to_pay"
    remaining_seconds = (order["payment_due_at"] - datetime.now(timezone.utc)).total_seconds()
    assert 14 * 60 <= remaining_seconds <= 15 * 60

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT quantity FROM order_stock_reservations WHERE order_id = %s AND product_id = %s;",
                (order["order_id"], product_id),
            )
            assert cursor.fetchone()[0] == 2
            cursor.execute(
                """
                UPDATE orders
                SET order_date = now() - INTERVAL '30 minutes',
                    payment_due_at = now() - INTERVAL '1 minute'
                WHERE order_id = %s;
                """,
                (order["order_id"],),
            )

    assert repositories.expire_unpaid_orders(account_id=reseller_account_id) == 1
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, expired_at FROM orders WHERE order_id = %s;",
                (order["order_id"],),
            )
            status, expired_at = cursor.fetchone()
            assert status == "expired"
            assert expired_at is not None
            cursor.execute("SELECT count(*) FROM order_stock_reservations WHERE order_id = %s;", (order["order_id"],))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT quantity_available FROM inventory_batches WHERE batch_id = %s;", (batch_ids[0],))
            assert cursor.fetchone()[0] == 5

    repositories.add_reseller_cart_item(reseller_account_id, product_id, "5")
    replacement = repositories.checkout_reseller_cart(reseller_account_id)
    assert replacement["status"] == "to_pay"


@pytest.mark.postgres
def test_reseller_checkout_keeps_unselected_items_in_cart(strict_inventory_database):
    _, first_product_id, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[10],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO inventory_items (
                    item_type, category, name, description, unit, base_price,
                    pack_size, pack_size_unit, pack_content_status
                )
                VALUES ('finished_product', 'Beef', 'Second Test Product',
                        'Second product.', 'pack', 125, 500, 'g', 'declared')
                RETURNING item_id;
                """
            )
            second_product_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO inventory_batches (
                    item_id, batch_code, source_type, quantity_received,
                    quantity_available, unit, received_date, expiry_date, quality_status
                )
                VALUES (%s, 'SECOND-TEST', 'direct_received', 10, 10, 'pack',
                        CURRENT_DATE, CURRENT_DATE + INTERVAL '7 days', 'approved');
                """,
                (second_product_id,),
            )

    repositories.add_reseller_cart_item(reseller_account_id, first_product_id, "2")
    repositories.add_reseller_cart_item(reseller_account_id, second_product_id, "3")

    order = repositories.checkout_reseller_cart(
        reseller_account_id,
        "Selected items only",
        [first_product_id],
    )

    remaining_items = repositories.list_reseller_cart_items(reseller_account_id)
    assert [item["product_id"] for item in remaining_items] == [second_product_id]
    assert repositories.reseller_cart_count(reseller_account_id) == 3
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT product_id, quantity FROM order_items WHERE order_id = %s;", (order["order_id"],))
            assert cursor.fetchall() == [(first_product_id, 2)]


@pytest.mark.postgres
def test_reseller_cart_checkout_http_flow_redirects_to_history(strict_inventory_database, monkeypatch):
    leader_id, product_id, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE accounts SET team_leader_role = 'sales-supervisor' WHERE account_id = %s;",
                (leader_id,),
            )
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]

    repositories.add_reseller_cart_item(reseller_account_id, product_id, "2")
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: reseller_account_id)
    response = TestClient(main.app).post(
        "/portal/reseller/checkout",
        data={
            "notes": "HTTP checkout",
            "product_id": str(product_id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/portal/reseller/checkout?placed=")
    assert repositories.reseller_cart_count(reseller_account_id) == 0


@pytest.mark.postgres
def test_reseller_checkout_preserves_cart_when_assigned_leader_is_ineligible(strict_inventory_database):
    leader_id, product_id, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE accounts SET team_leader_role = 'inventory' WHERE account_id = %s;",
                (leader_id,),
            )
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]

    repositories.add_reseller_cart_item(reseller_account_id, product_id, "2")

    with pytest.raises(ValueError, match="assigned sales team leader is unavailable"):
        proofs, payment = _payment_submission("ineligible-leader")
        repositories.checkout_reseller_cart(
            reseller_account_id, payment_proofs=proofs, payment_details=payment
        )

    assert repositories.reseller_cart_count(reseller_account_id) == 2
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM orders;")
            assert cursor.fetchone()[0] == 0


@pytest.mark.postgres
def test_reseller_checkout_http_flow_returns_assignment_error_and_preserves_cart(strict_inventory_database, monkeypatch):
    leader_id, product_id, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[5],
        order_quantities=[],
    )
    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE accounts SET team_leader_role = 'inventory' WHERE account_id = %s;",
                (leader_id,),
            )
            cursor.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local';")
            reseller_account_id = cursor.fetchone()[0]

    repositories.add_reseller_cart_item(reseller_account_id, product_id, "2")
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: reseller_account_id)
    proofs, payment = _payment_submission("http-ineligible")

    async def parse_uploads(_uploads):
        return proofs

    monkeypatch.setattr(shared_portal, "parse_payment_proof_attachments", parse_uploads)
    monkeypatch.setattr(reseller_portal.receipt_ocr, "confirmed_payment_details", lambda *args: payment)

    response = TestClient(main.app).post(
        "/portal/reseller/checkout",
        data={
            "product_id": str(product_id),
            "receipt_scan_token": "signed",
            "transaction_number": payment["transaction_number"],
            "payment_date": "2026-10-15",
            "payment_time": "10:30",
            "payment_details_confirmed": "yes",
        },
        files={"payment_proofs": ("receipt.png", b"image", "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/reseller/checkout?restore=1"
    assert repositories.reseller_cart_count(reseller_account_id) == 2


@pytest.mark.postgres
def test_assigned_sales_leader_cannot_be_changed_to_inventory(strict_inventory_database):
    leader_id, _, _, _ = _seed_fulfillment(
        strict_inventory_database,
        batch_quantities=[],
        order_quantities=[],
    )

    with pytest.raises(ValueError, match="Reassign this leader's resellers"):
        repositories.update_team_leader_account_role(leader_id, "inventory")

    with psycopg2.connect(strict_inventory_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT team_leader_role FROM accounts WHERE account_id = %s;", (leader_id,))
            assert cursor.fetchone()[0] == "sales"
