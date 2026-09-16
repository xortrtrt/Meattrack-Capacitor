"""Import the catalog from the former Supabase database into local PostgreSQL.

The source URL is read from SUPABASE_DB_URL in the local, git-ignored .env file.
Use --check to inspect the source and --apply to replace a disposable local
catalog. The importer refuses to run if local business records reference a
catalog item.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.config import database_dsn  # noqa: E402


REFERENCE_TABLES = (
    "orders",
    "order_items",
    "alerts",
    "forecast_results",
    "reseller_cart_items",
    "sales_report_items",
)


def source_catalog(source_dsn: str) -> tuple[list[dict], list[dict], list[dict]]:
    with psycopg2.connect(source_dsn, connect_timeout=15) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT item_id, item_type, category, name, description, unit,
                       base_price, quantity_available, is_active, created_at
                FROM inventory_items
                ORDER BY item_id;
                """
            )
            items = cursor.fetchall()
            cursor.execute(
                """
                SELECT product.item_type AS product_type, product.name AS product_name,
                       material.item_type AS material_type, material.name AS material_name,
                       recipe.quantity_required, recipe.unit
                FROM product_recipes recipe
                JOIN inventory_items product ON product.item_id = recipe.product_item_id
                JOIN inventory_items material ON material.item_id = recipe.material_item_id
                ORDER BY recipe.recipe_id;
                """
            )
            recipes = cursor.fetchall()
            cursor.execute(
                """
                SELECT item.item_type, item.name, batch.batch_code, batch.source_type,
                       batch.quantity_received, batch.quantity_available, batch.unit,
                       batch.received_date, batch.expiry_date, batch.quality_status,
                       batch.created_at
                FROM inventory_batches batch
                JOIN inventory_items item ON item.item_id = batch.item_id
                ORDER BY batch.batch_id;
                """
            )
            batches = cursor.fetchall()
    return items, recipes, batches


def assert_local_catalog_is_replaceable(cursor: RealDictCursor) -> None:
    references: list[str] = []
    for table in REFERENCE_TABLES:
        cursor.execute(f"SELECT COUNT(*) AS total FROM {table};")
        count = int(cursor.fetchone()["total"])
        if count:
            references.append(f"{table} ({count})")
    if references:
        raise RuntimeError(
            "Local catalog is referenced by business data and cannot be replaced: "
            + ", ".join(references)
        )


def replace_local_catalog(items: list[dict], recipes: list[dict], batches: list[dict]) -> None:
    with psycopg2.connect(database_dsn()) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            assert_local_catalog_is_replaceable(cursor)
            cursor.execute("DELETE FROM product_recipes;")
            cursor.execute("DELETE FROM inventory_batches;")
            cursor.execute("DELETE FROM inventory_items;")

            item_ids: dict[tuple[str, str], int] = {}
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO inventory_items (
                        item_type, category, name, description, unit, base_price,
                        quantity_available, is_active, created_at
                    ) VALUES (%(item_type)s, %(category)s, %(name)s, %(description)s,
                              %(unit)s, %(base_price)s, %(quantity_available)s,
                              %(is_active)s, %(created_at)s)
                    RETURNING item_id;
                    """,
                    item,
                )
                item_ids[(item["item_type"], item["name"])] = int(cursor.fetchone()["item_id"])

            for recipe in recipes:
                cursor.execute(
                    """
                    INSERT INTO product_recipes (
                        product_item_id, material_item_id, quantity_required, unit
                    ) VALUES (%s, %s, %s, %s);
                    """,
                    (
                        item_ids[(recipe["product_type"], recipe["product_name"])],
                        item_ids[(recipe["material_type"], recipe["material_name"])],
                        recipe["quantity_required"],
                        recipe["unit"],
                    ),
                )

            for batch in batches:
                cursor.execute(
                    """
                    INSERT INTO inventory_batches (
                        item_id, batch_code, source_type, quantity_received,
                        quantity_available, unit, received_date, expiry_date,
                        quality_status, created_at
                    ) VALUES (%(item_id)s, %(batch_code)s, %(source_type)s,
                              %(quantity_received)s, %(quantity_available)s,
                              %(unit)s, %(received_date)s, %(expiry_date)s,
                              %(quality_status)s, %(created_at)s);
                    """,
                    {**batch, "item_id": item_ids[(batch["item_type"], batch["name"])]},
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the Supabase catalog into local PostgreSQL.")
    parser.add_argument("--check", action="store_true", help="Read and summarize the source catalog only.")
    parser.add_argument("--apply", action="store_true", help="Replace the local demo catalog with the source catalog.")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("Specify exactly one of --check or --apply.")

    source_dsn = os.getenv("SUPABASE_DB_URL", "").strip()
    if not source_dsn:
        parser.error("Set SUPABASE_DB_URL in .env before running this importer.")

    items, recipes, batches = source_catalog(source_dsn)
    product_count = sum(item["item_type"] == "finished_product" for item in items)
    material_count = sum(item["item_type"] == "raw_material" for item in items)
    print(
        f"Source catalog: {product_count} finished products, {material_count} raw materials, "
        f"{len(recipes)} recipes, and {len(batches)} inventory batches."
    )
    if args.check:
        return

    replace_local_catalog(items, recipes, batches)
    print("Local catalog import completed successfully.")


if __name__ == "__main__":
    main()
