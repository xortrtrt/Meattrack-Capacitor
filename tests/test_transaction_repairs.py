from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from uuid import uuid4

import psycopg2
import pytest

from app import repositories
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, receive, complete_refund  # noqa: F401

pytestmark = pytest.mark.postgres
MIGRATION = Path("database/migrations/032_fix_order_message_uniqueness.sql")


def migrate():
    with get_transaction_cursor() as cur:
        cur.execute(MIGRATION.read_text())


def legacy_constraint():
    with get_transaction_cursor() as cur:
        # Let PostgreSQL generate/truncate the name, as migration 023 did.
        cur.execute("ALTER TABLE order_system_messages ADD UNIQUE(reseller_account_id,order_id,event_type)")


def verified_refund(d):
    case = create_case(d)
    returns.review_product_return(case, d["sales"], True, "Approved expired product return")
    receive(d, case)
    return case, returns.get_product_return(case, d["supervisor"])["refund"]["order_refund_id"]


def test_legacy_upgrade_preserves_history_and_allows_refund_retry(lifecycle):  # noqa: F811
    d = lifecycle
    legacy_constraint()
    case, refund = verified_refund(d)
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    repositories.fail_order_refund(refund, d["supervisor"], "sales-supervisor", "Bank transfer failed during test")
    before = fetch_all("SELECT * FROM order_system_messages ORDER BY order_system_message_id")
    migrate()
    migrate()
    assert fetch_all("SELECT * FROM order_system_messages ORDER BY order_system_message_id") == before
    complete_refund(d, case)
    attempts = fetch_all("SELECT status,attempt_number FROM order_refund_attempts ORDER BY attempt_number")
    assert [(a["status"], a["attempt_number"]) for a in attempts] == [("failed", 1), ("completed", 2)]
    assert returns.get_product_return(case, d["reseller"])["status"] == "completed"
    processing = fetch_all("SELECT dedupe_key FROM order_system_messages WHERE event_type='refund_processing'")
    assert len(processing) == 2 and len({m["dedupe_key"] for m in processing}) == 2
    with pytest.raises(ValueError):
        complete_refund(d, case)


def test_upgraded_database_supports_two_partial_refunds_and_correct_totals(lifecycle):  # noqa: F811
    d = lifecycle
    legacy_constraint()
    migrate()
    for _ in range(2):
        case, _ = verified_refund(d)
        complete_refund(d, case)
    totals = returns.return_settlement_totals(d["sales"])
    assert totals["original_sales"] == 1000 and totals["net_collections"] == 600
    assert fetch_one("SELECT count(*) n FROM order_refunds WHERE status='completed'")["n"] == 2
    assert fetch_one("SELECT count(*) n FROM order_system_messages WHERE event_type='refund_completed'")["n"] == 2
    assert fetch_one("SELECT count(*) n FROM orders")["n"] == 1


def test_fresh_schema_migration_keeps_dedupe_and_unrelated_constraints(lifecycle):  # noqa: F811
    with get_transaction_cursor() as cur:
        cur.execute("ALTER TABLE order_system_messages ADD CONSTRAINT preserve_message_title UNIQUE(order_id,title)")
    migrate()
    assert fetch_one("SELECT 1 present FROM pg_constraint WHERE conrelid='order_system_messages'::regclass AND conname='preserve_message_title'")
    with get_transaction_cursor() as cur:
        cur.execute("ALTER TABLE order_system_messages DROP CONSTRAINT preserve_message_title")
    _, refund = verified_refund(lifecycle)
    repositories.start_order_refund(refund, lifecycle["supervisor"], "sales-supervisor")
    repositories.fail_order_refund(refund, lifecycle["supervisor"], "sales-supervisor", "Test failed transfer before retry")
    repositories.start_order_refund(refund, lifecycle["supervisor"], "sales-supervisor")
    assert fetch_one("SELECT count(*) n FROM order_refund_attempts")["n"] == 2
    with pytest.raises(psycopg2.errors.UniqueViolation):
        with get_transaction_cursor() as cur:
            cur.execute("INSERT INTO order_system_messages(reseller_account_id,order_id,event_type,title,message,dedupe_key) SELECT reseller_account_id,order_id,event_type,title,message,dedupe_key FROM order_system_messages LIMIT 1")


def blocked_pair(d, lock_sql, unlock_sql, lock_params, submissions):
    locker = psycopg2.connect(d["dsn"])
    locker.autocommit = True
    with locker.cursor() as cur:
        cur.execute(lock_sql, lock_params)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(action) for action in submissions]
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    row = fetch_one("SELECT count(*) n FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()")
                    if row["n"] >= 2:
                        break
                    time.sleep(.02)
                assert row["n"] >= 2, "Both operations must reach the lock before releasing it"
            finally:
                with locker.cursor() as cur:
                    cur.execute(unlock_sql, lock_params if "%s" in unlock_sql else None)
            return [future.result(timeout=10) for future in futures]
    finally:
        locker.close()


@pytest.mark.parametrize("quantity,same_key", [(10, True), (5, True), (6, False)])
def test_concurrent_pullout_replays_succeed_and_competing_requests_validate(lifecycle, quantity, same_key):  # noqa: F811
    d = lifecycle
    first = str(uuid4())
    keys = [first, first if same_key else str(uuid4())]

    def submit(key):
        try:
            returns.confirm_expired_pullout(d["batches"][0], d["inventory"], quantity, "Expired freezer", "Confirmed expired stock pullout", key)
            return "success"
        except ValueError as exc:
            return str(exc)

    outcomes = blocked_pair(
        d, "SELECT pg_advisory_lock(hashtextextended(%s,0))", "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
        (f"inventory-product:{d['product']}",), [lambda k=k: submit(k) for k in keys],
    )
    assert outcomes.count("success") == (2 if same_key else 1)
    if not same_key:
        assert any("cannot exceed" in outcome for outcome in outcomes)
    assert fetch_one("SELECT quantity_available n FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))["n"] == 10 - quantity
    assert fetch_one("SELECT sum(quantity_remaining) n FROM expired_holdings")["n"] == quantity
    assert fetch_one("SELECT count(*) n FROM expired_stock_movements WHERE action='pullout'")["n"] == 1


def test_concurrent_disposal_replay_deducts_holding_once(lifecycle):  # noqa: F811
    d = lifecycle
    returns.confirm_expired_pullout(d["batches"][0], d["inventory"], 10, "Expired freezer", "Confirmed expired warehouse goods", str(uuid4()))
    holding = fetch_one("SELECT holding_id FROM expired_holdings")["holding_id"]
    key = str(uuid4())
    # The row lock, not an application delay, makes the race deterministic.
    outcomes = blocked_pair(
        d, "BEGIN; SELECT 1 FROM expired_holdings WHERE holding_id=%s FOR UPDATE", "COMMIT", (holding,),
        [lambda: returns.dispose_expired_stock(holding, d["inventory"], 10, "Recorded disposal", "Disposed all expired packs", key)] * 2,
    )
    assert outcomes == [None, None]
    assert fetch_one("SELECT quantity_remaining n FROM expired_holdings WHERE holding_id=%s", (holding,))["n"] == 0
    assert fetch_one("SELECT count(*) n FROM expired_stock_movements WHERE action='disposal'")["n"] == 1
