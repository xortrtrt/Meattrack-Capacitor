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
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extras import execute_values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.config import database_dsn  # noqa: E402


EXCLUDED_SOURCE_TABLES = {"media_assets", "schema_migrations"}


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


def insertable_columns(connection, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND is_generated = 'NEVER'
            ORDER BY ordinal_position;
            """,
            (table,),
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
    return sorted((source_tables & target_tables) - EXCLUDED_SOURCE_TABLES)


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
    table_list = sql.SQL(", ").join(map(sql.Identifier, tables))
    with target.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = 'replica';")
        cursor.execute(
            sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE;").format(table_list)
        )
        for table in tables:
            columns = common_columns(source, target, table)
            rows = source_rows(source, table, columns)
            if not rows:
                continue
            statement = sql.SQL(
                "INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES %s"
            ).format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, columns)),
            )
            execute_values(cursor, statement, rows, page_size=500)
        cursor.execute("SET LOCAL session_replication_role = 'origin';")

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
    args = parser.parse_args()

    source_dsn = os.getenv("SUPABASE_DB_URL", "").strip()
    if not source_dsn:
        parser.error("Set SUPABASE_DB_URL in .env before running this importer.")

    with psycopg2.connect(source_dsn, connect_timeout=15) as source:
        with psycopg2.connect(database_dsn()) as target:
            validate_source_inventory(source)
            tables = importable_tables(source, target)
            print(f"Importable application tables: {len(tables)}")
            for table in tables:
                print(f"{table}: source={row_count(source, table)}, local={row_count(target, table)}")
            if args.check:
                return
            replace_database(source, target, tables)
    print("Full Supabase database import completed and verified.")


if __name__ == "__main__":
    main()
