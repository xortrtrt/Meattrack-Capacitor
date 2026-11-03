"""Upgrade compatibility and transaction-only reset must prove retained state."""
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

from app.database import fetch_all, fetch_one, get_transaction_cursor
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_replacement_workflow import approve_receive
from test_replacement_first_policy import request, data, convert
from tools import reset_operational_data as maintenance

pytestmark = pytest.mark.postgres


def retained_state():
    return {
        name: fetch_all(query) for name, query in {
            "accounts": "SELECT * FROM accounts ORDER BY account_id",
            "resellers": "SELECT * FROM resellers ORDER BY reseller_id",
            "catalog": "SELECT * FROM inventory_items ORDER BY item_id",
            "batches": "SELECT * FROM inventory_batches ORDER BY batch_id",
            "settings": "SELECT * FROM business_settings ORDER BY setting_key",
            "recipes": "SELECT * FROM product_recipes ORDER BY recipe_id",
            "triggers": """SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY 1,2""",
        }.items()
    }


def test_new_migration_replays_without_rewriting_existing_cases(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    before = data(d, case)
    messages = fetch_all("SELECT * FROM return_conversation_messages ORDER BY message_id")
    migrations = [Path("database/migrations", name).read_text(encoding="utf-8") for name in (
        '048_replacement_first_returns.sql', '049_scope_replacement_policy_trigger.sql')]
    with get_transaction_cursor() as cur:
        for _ in range(2):
            for migration in migrations:
                cur.execute(migration)
    after = data(d, case)
    for key in ("return_policy_version", "replacement_workflow_version", "credit", "replacement_total", "quote_revision", "status"):
        assert after[key] == before[key]
    assert fetch_all("SELECT * FROM return_conversation_messages ORDER BY message_id") == messages


@pytest.mark.parametrize("apply", [False, True])
def test_transaction_reset_preserves_batches_custody_catalog_and_accounts(lifecycle, apply):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    convert(d, case)
    before = retained_state()
    holdings = fetch_all("SELECT product_id,batch_id,expiry_date,quantity_remaining,location FROM expired_holdings ORDER BY holding_id")
    old_order = d["order"]
    with psycopg2.connect(d["dsn"], cursor_factory=RealDictCursor) as conn:
        result = maintenance.reset_transactions(conn, apply=apply)
    assert result["committed"] is apply
    assert retained_state() == before
    assert fetch_all("SELECT product_id,batch_id,expiry_date,quantity_remaining,location FROM expired_holdings ORDER BY holding_id") == holdings
    if not apply:
        assert fetch_one("SELECT count(*) n FROM orders")["n"] == 1
        assert fetch_one("SELECT count(*) n FROM product_returns")["n"] == 1
    else:
        for table in ("orders", "order_items", "product_returns", "product_return_lines", "fulfillment_allocations",
                      "order_refunds", "order_refund_attempts", "order_refund_proofs", "order_stock_reservations",
                      "replacement_stock_reservations", "return_conversation_messages", "return_offer_snapshots"):
            assert fetch_one(f"SELECT count(*) n FROM {table}")["n"] == 0
        sequence = fetch_one("SELECT nextval(pg_get_serial_sequence('orders','order_id')) n")["n"]
        assert sequence > old_order
        assert fetch_one("SELECT count(*) n FROM expired_holdings WHERE return_line_id IS NOT NULL")["n"] == 0


def test_reset_rolls_back_when_new_dependency_is_not_reviewed(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    before = retained_state()
    with get_transaction_cursor() as cur:
        cur.execute("CREATE TABLE unreviewed_order_links(id bigint PRIMARY KEY,order_id bigint REFERENCES orders)")
        cur.execute("INSERT INTO unreviewed_order_links VALUES(1,%s)", (d["order"],))
    with psycopg2.connect(d["dsn"], cursor_factory=RealDictCursor) as conn:
        with pytest.raises((ValueError, RuntimeError), match="review|dependency|schema"):
            maintenance.reset_transactions(conn, apply=True)
    assert retained_state() == before
    assert data(d, case)["status"] == "submitted"


def test_reset_refuses_remote_target_before_mutation(monkeypatch):
    class Remote:
        def get_dsn_parameters(self):
            return {"host": "database.example.invalid", "dbname": "production"}

        def cursor(self, *args, **kwargs):
            pytest.fail("Remote target was accessed before loopback validation")

    with pytest.raises(ValueError, match="local|loopback"):
        maintenance.reset_transactions(Remote(), apply=True)


def test_transaction_reset_cli_requires_backup_before_connecting(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["reset_operational_data.py", "--transactions-only", "--apply"])

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Reset connected before requiring a verified backup")

    monkeypatch.setattr(maintenance.psycopg2, "connect", unexpected_connection)
    with pytest.raises(SystemExit):
        maintenance.main()
    assert "backup" in capsys.readouterr().err.lower()


def test_private_backup_requires_verified_restore_and_matching_archive(tmp_path):
    from tools.verified_database_backup import validate_backup
    archive = tmp_path / "local.dump"
    archive.write_bytes(b"PGDMPsynthetic-unverified-archive")
    with pytest.raises(ValueError, match="restore|manifest|verified"):
        validate_backup(archive)
