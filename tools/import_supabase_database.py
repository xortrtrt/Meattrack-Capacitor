"""Replace the local application data with the complete Supabase dataset.

The source URL is read from SUPABASE_DB_URL in the git-ignored .env file.
The current local schema and migration ledger are retained. Supabase-only
media_assets and accounts auth metadata are intentionally omitted because the
application now uses local static media and local password authentication.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extras import execute_values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.config import database_dsn  # noqa: E402


EXCLUDED_SOURCE_TABLES = {
    "media_assets", "schema_migrations", "web_sessions", "security_events",
    "account_login_otps", "account_password_otps", "account_activation_tokens",
    "notification_outbox",
}
REQUIRED_LEDGER_COLUMNS = {
    "movement_id", "item_id", "movement_type", "quantity_delta", "unit",
    "balance_before", "balance_after", "actor_name",
}
IMPORT_ORDER = (
    "departments", "accounts", "inquiries", "resellers", "inventory_items",
    "inventory_batches", "product_recipes", "orders", "order_items",
    "order_payment_proofs", "reseller_cart_items", "sales_reports",
    "sales_report_items", "sales_report_attachments", "alerts", "forecast_runs",
    "forecast_results", "user_consents", "notifications", "notification_recipients",
    "activity_logs", "inventory_movements",
)


def public_tables(connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public';
            """
        )
        return {row[0] for row in cursor.fetchall()}


def insertable_columns(connection, table: str, schema: str = "public") -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
              AND table_schema = %s
              AND is_generated = 'NEVER'
            ORDER BY ordinal_position;
            """,
            (table, schema),
        )
        return [row[0] for row in cursor.fetchall()]


def identity_columns(connection, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND is_identity = 'YES'
            ORDER BY ordinal_position;
            """,
            (table,),
        )
        return [row[0] for row in cursor.fetchall()]


def row_count(connection, table: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SELECT COUNT(*) FROM {};").format(sql.Identifier(table)))
        return int(cursor.fetchone()[0])


def importable_tables(source, target) -> list[str]:
    source_tables = public_tables(source)
    target_tables = public_tables(target)
    available = ((source_tables & target_tables) - EXCLUDED_SOURCE_TABLES) & set(IMPORT_ORDER)
    order = {name: index for index, name in enumerate(IMPORT_ORDER)}
    return sorted(available, key=lambda name: (order.get(name, len(order)), name))


def common_columns(source, target, table: str) -> list[str]:
    source_columns = set(insertable_columns(source, table))
    return [column for column in insertable_columns(target, table) if column in source_columns]


def source_rows(source, table: str, columns: list[str]) -> list[tuple]:
    query = sql.SQL("SELECT {} FROM {};").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.Identifier(table),
    )
    with source.cursor() as cursor:
        cursor.execute(query)
        return cursor.fetchall()


def validate_source_inventory(source) -> None:
    if "inventory_movements" not in public_tables(source):
        raise RuntimeError("Full-database import requires inventory_movements; catalog-only legacy sources are not accepted.")
    if not REQUIRED_LEDGER_COLUMNS.issubset(set(insertable_columns(source, "inventory_movements"))):
        raise RuntimeError("Source inventory ledger does not match the authoritative movement schema.")
    if "team_leader_account_id" not in set(insertable_columns(source, "orders")):
        raise RuntimeError("Full-database source must be migrated to immutable order team-leader ownership first.")
    required_item_columns = {"pack_size", "pack_size_unit", "pack_content_status"}
    available_item_columns = set(insertable_columns(source, "inventory_items"))
    if not required_item_columns.issubset(available_item_columns):
        raise RuntimeError(
            "The full source database predates strict pack metadata. "
            "Migrate the source first or use import_supabase_catalog.py for normalized legacy catalog data."
        )
    checks = (
        ("""
            SELECT COUNT(*) FROM inventory_items
            WHERE (item_type = 'raw_material' AND unit NOT IN ('kg', 'g', 'ml'))
               OR (item_type = 'finished_product' AND unit <> 'pack')
        """, "unsupported inventory item units"),
        ("""
            SELECT COUNT(*) FROM inventory_items
            WHERE (item_type = 'raw_material' AND (
                       pack_size IS NOT NULL OR pack_size_unit IS NOT NULL OR pack_content_status IS NOT NULL
                   ))
               OR (item_type = 'finished_product' AND (
                       (pack_content_status = 'declared' AND pack_size > 0 AND pack_size_unit IN ('g', 'kg', 'ml'))
                       OR
                       (pack_content_status = 'unknown_legacy' AND pack_size IS NULL AND pack_size_unit IS NULL)
                   ) IS NOT TRUE)
        """, "invalid structured pack metadata"),
        ("""
            SELECT COUNT(*)
            FROM product_recipes pr
            JOIN inventory_items p ON p.item_id = pr.product_item_id
            JOIN inventory_items rm ON rm.item_id = pr.material_item_id
            WHERE p.item_type <> 'finished_product'
               OR rm.item_type <> 'raw_material'
               OR pr.unit <> rm.unit
        """, "invalid recipe item types or units"),
        ("""
            SELECT COUNT(*)
            FROM inventory_batches ib
            JOIN inventory_items ii ON ii.item_id = ib.item_id
            WHERE ii.item_type <> 'finished_product'
               OR ib.unit <> 'pack'
               OR ib.quantity_received <> trunc(ib.quantity_received)
               OR ib.quantity_available <> trunc(ib.quantity_available)
        """, "invalid finished-product batches"),
        ("SELECT COUNT(*) FROM order_items WHERE quantity <> trunc(quantity)", "fractional order packs"),
        ("SELECT COUNT(*) FROM reseller_cart_items WHERE quantity <> trunc(quantity)", "fractional cart packs"),
        ("SELECT COUNT(*) FROM sales_report_items WHERE quantity_sold <> trunc(quantity_sold)", "fractional reported packs"),
        ("SELECT COUNT(*) FROM inventory_movements WHERE balance_after <> balance_before + quantity_delta", "invalid ledger equations"),
        ("SELECT COUNT(*) FROM inventory_movements WHERE balance_before < 0 OR balance_after < 0", "negative ledger balances"),
        ("""
            SELECT COUNT(*) FROM order_items oi
            JOIN inventory_items p ON p.item_id = oi.product_id
            WHERE p.item_type <> 'finished_product' OR oi.unit <> 'pack'
        """, "invalid order product types or units"),
        ("""
            SELECT COUNT(*) FROM reseller_cart_items rci
            JOIN inventory_items p ON p.item_id = rci.product_id
            WHERE p.item_type <> 'finished_product'
        """, "invalid cart product types"),
        ("""
            SELECT COUNT(*) FROM sales_report_items sri
            JOIN inventory_items p ON p.item_id = sri.product_id
            WHERE p.item_type <> 'finished_product' OR sri.unit <> 'pack'
        """, "invalid reported product types or units"),
    )
    failures: list[str] = []
    with source.cursor() as cursor:
        for query, label in checks:
            cursor.execute(query)
            if int(cursor.fetchone()[0]):
                failures.append(label)
    if failures:
        raise RuntimeError("Source inventory validation failed: " + ", ".join(failures))

    with source.cursor() as cursor:
        cursor.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (item_id) item_id, balance_after
                FROM inventory_movements
                WHERE affected_batch_id IS NULL
                ORDER BY item_id, created_at DESC, movement_id DESC
            )
            SELECT count(*)
            FROM inventory_items i LEFT JOIN latest l ON l.item_id = i.item_id
            WHERE i.item_type = 'raw_material' AND l.balance_after IS DISTINCT FROM i.quantity_available;
            """
        )
        raw_mismatches = int(cursor.fetchone()[0])
        cursor.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (affected_batch_id) affected_batch_id, balance_after
                FROM inventory_movements
                WHERE affected_batch_id IS NOT NULL
                ORDER BY affected_batch_id, created_at DESC, movement_id DESC
            )
            SELECT count(*)
            FROM inventory_batches b LEFT JOIN latest l ON l.affected_batch_id = b.batch_id
            WHERE l.balance_after IS DISTINCT FROM b.quantity_available;
            """
        )
        batch_mismatches = int(cursor.fetchone()[0])
    if raw_mismatches or batch_mismatches:
        raise RuntimeError(
            f"Source ledger reconciliation failed: {raw_mismatches} raw balance(s), {batch_mismatches} batch balance(s)."
        )


def require_pristine_target(target) -> None:
    durable = public_tables(target) - EXCLUDED_SOURCE_TABLES
    populated = [table for table in sorted(durable) if table != "schema_migrations" and row_count(target, table)]
    if populated:
        raise RuntimeError(
            "Full database replacement is limited to an empty, disposable target. Existing data found in: "
            + ", ".join(populated)
        )


def _defer_foreign_keys(cursor, schema: str) -> None:
    cursor.execute(
        """
        SELECT c.relname, p.conname
        FROM pg_constraint p
        JOIN pg_class c ON c.oid = p.conrelid
        WHERE p.contype = 'f' AND p.connamespace = %s::regnamespace;
        """,
        (schema,),
    )
    for relation, constraint in cursor.fetchall():
        cursor.execute(
            sql.SQL("ALTER TABLE {}.{} ALTER CONSTRAINT {} DEFERRABLE INITIALLY DEFERRED").format(
                sql.Identifier(schema), sql.Identifier(relation), sql.Identifier(constraint)
            )
        )


def _insert_rows(source, target_cursor, target, tables: list[str], schema: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    target_cursor.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
    target_cursor.execute("SELECT set_config('meattrack.maintenance_import', 'on', true)")
    target_cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    for table in tables:
        source_columns = set(insertable_columns(source, table))
        columns = [column for column in insertable_columns(target, table, schema) if column in source_columns]
        rows = source_rows(source, table, columns)
        counts[table] = len(rows)
        if not rows:
            continue
        statement = sql.SQL("INSERT INTO {}.{} ({}) OVERRIDING SYSTEM VALUE VALUES %s").format(
            sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
        )
        execute_values(target_cursor, statement, rows, page_size=500)
    return counts


def validate_in_staging_schema(source, target, tables: list[str]) -> None:
    schema = f"import_validation_{uuid.uuid4().hex}"
    baseline = (PROJECT_ROOT / "database" / "schema.sql").read_text(encoding="utf-8").strip()
    baseline = baseline.removeprefix("BEGIN;").strip().removesuffix("COMMIT;").strip()
    try:
        with target.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            cursor.execute(baseline)
            _defer_foreign_keys(cursor, schema)
            counts = _insert_rows(source, cursor, target, tables, schema)
            for table, expected in counts.items():
                cursor.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
                if int(cursor.fetchone()[0]) != expected:
                    raise RuntimeError(f"Staging count mismatch for {table}.")
            cursor.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
            validate_source_inventory(target)
        target.rollback()
    except Exception:
        target.rollback()
        raise


def reset_identity_sequences(target, tables: list[str]) -> None:
    with target.cursor() as cursor:
        for table in tables:
            for column in identity_columns(target, table):
                cursor.execute("SELECT pg_get_serial_sequence(%s, %s);", (table, column))
                sequence = cursor.fetchone()[0]
                if not sequence:
                    continue
                cursor.execute(
                    sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {};").format(
                        sql.Identifier(column), sql.Identifier(table)
                    )
                )
                maximum = int(cursor.fetchone()[0])
                if maximum:
                    cursor.execute("SELECT setval(%s, %s, true);", (sequence, maximum))
                else:
                    cursor.execute("SELECT setval(%s, 1, false);", (sequence,))


def replace_database(source, target, tables: list[str]) -> None:
    source_counts = {table: row_count(source, table) for table in tables}
    with target.cursor() as cursor:
        _defer_foreign_keys(cursor, "public")
        _insert_rows(source, cursor, target, tables, "public")

    reset_identity_sequences(target, tables)

    mismatches = []
    for table, expected in source_counts.items():
        actual = row_count(target, table)
        if actual != expected:
            mismatches.append(f"{table}: expected {expected}, found {actual}")
    if mismatches:
        raise RuntimeError("Post-import count verification failed: " + "; ".join(mismatches))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace all local application data with the Supabase dataset."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Compare tables and row counts only.")
    action.add_argument("--apply", action="store_true", help="Replace all local application data.")
    action.add_argument("--self-check", action="store_true", help="Validate importer safety invariants without connecting.")
    parser.add_argument("--confirm-target", help="Required with --apply; must exactly match current_database().")
    args = parser.parse_args()

    if args.self_check:
        assert "schema_migrations" in EXCLUDED_SOURCE_TABLES
        assert "web_sessions" in EXCLUDED_SOURCE_TABLES
        assert "notification_outbox" in EXCLUDED_SOURCE_TABLES
        assert "inventory_movements" not in EXCLUDED_SOURCE_TABLES
        print("Importer safety self-check passed.")
        return

    source_dsn = os.getenv("SUPABASE_DB_URL", "").strip()
    if not source_dsn:
        parser.error("Set SUPABASE_DB_URL in .env before running this importer.")

    with psycopg2.connect(source_dsn, connect_timeout=15) as source:
        with psycopg2.connect(database_dsn()) as target:
            with target.cursor() as cursor:
                cursor.execute("SELECT current_database();")
                target_name = cursor.fetchone()[0]
            if args.apply and args.confirm_target != target_name:
                parser.error(f"--apply requires --confirm-target {target_name!r}")
            validate_source_inventory(source)
            tables = importable_tables(source, target)
            print(f"Importable application tables: {len(tables)}")
            for table in tables:
                print(f"{table}: source={row_count(source, table)}, local={row_count(target, table)}")
            if args.check:
                return
            require_pristine_target(target)
            validate_in_staging_schema(source, target, tables)
            replace_database(source, target, tables)
            validate_source_inventory(target)
    print("Full Supabase database import completed and verified.")


if __name__ == "__main__":
    main()
