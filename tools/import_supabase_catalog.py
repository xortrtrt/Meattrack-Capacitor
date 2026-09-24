"""Import the catalog from the former Supabase database into local PostgreSQL.

The source URL is read from SUPABASE_DB_URL in the local, git-ignored .env file.
Use --check to inspect the source and --apply to replace a disposable local
catalog. The importer refuses to run if local business records reference a
catalog item.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import os
import re
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.config import database_dsn  # noqa: E402
from app import inventory_measurements as measurements  # noqa: E402


REFERENCE_TABLES = (
    "orders",
    "order_items",
    "alerts",
    "forecast_results",
    "reseller_cart_items",
    "sales_report_items",
    "inventory_movements",
)

PACK_DESCRIPTION_RE = re.compile(r"^.+ - ([0-9]+(?:\.[0-9]{1,3})?) (g|kg|ml) per pack\.$")


def _three_place_decimal(value: object, label: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    quantum = Decimal("0.001")
    if not parsed.is_finite() or parsed != parsed.quantize(quantum):
        raise ValueError(f"{label} must be finite and use at most three decimal places.")
    parsed = parsed.quantize(quantum)
    if positive and parsed <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    if nonnegative and parsed < 0:
        raise ValueError(f"{label} cannot be negative.")
    return parsed


def validate_and_normalize_catalog(
    items: list[dict], recipes: list[dict], batches: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    normalized_items: list[dict] = []
    item_lookup: dict[tuple[str, str], dict] = {}
    for source in items:
        item = dict(source)
        item_type = item.get("item_type")
        name = " ".join(str(item.get("name") or "").strip().split())
        if item_type not in {"raw_material", "finished_product"} or not name:
            raise ValueError("Catalog items require a valid type and name.")
        item["name"] = name
        if item_type == "raw_material":
            unit, multiplier = measurements.normalize_import_stock_unit(item.get("unit"))
            quantity = _three_place_decimal(item.get("quantity_available"), f"{name} quantity", nonnegative=True) * multiplier
            quantity = _three_place_decimal(quantity, f"{name} converted quantity", nonnegative=True)
            item.update({
                "unit": unit,
                "quantity_available": quantity,
                "base_price": Decimal("0.00"),
                "pack_size": None,
                "pack_size_unit": None,
                "pack_content_status": None,
            })
        else:
            if measurements.canonical_unit(item.get("unit")) != "pack":
                raise ValueError(f"{name} must use pack as its finished-product unit.")
            if _three_place_decimal(item.get("quantity_available"), f"{name} item quantity", nonnegative=True) != 0:
                raise ValueError(f"{name} cannot store finished stock on the item row.")
            match = PACK_DESCRIPTION_RE.fullmatch(str(item.get("description") or ""))
            if item.get("pack_content_status") == "declared":
                item.update({
                    "pack_size": _three_place_decimal(item.get("pack_size"), f"{name} pack size", positive=True),
                    "pack_size_unit": measurements.require_unit(
                        item.get("pack_size_unit"), measurements.CONTENT_UNITS, "Contents unit"
                    ),
                    "pack_content_status": "declared",
                })
            elif match:
                item.update({
                    "pack_size": measurements.parse_decimal_exact(match.group(1), "Pack size", scale=3, positive=True),
                    "pack_size_unit": match.group(2),
                    "pack_content_status": "declared",
                })
            else:
                item.update({"pack_size": None, "pack_size_unit": None, "pack_content_status": "unknown_legacy"})
            item["unit"] = "pack"
        key = (item_type, name)
        if key in item_lookup:
            raise ValueError(f"Duplicate catalog item: {item_type} {name}.")
        item_lookup[key] = item
        normalized_items.append(item)

    normalized_recipes: list[dict] = []
    seen_recipes: set[tuple[str, str]] = set()
    for source in recipes:
        recipe = dict(source)
        product_key = (
            str(recipe.get("product_type")),
            " ".join(str(recipe.get("product_name") or "").strip().split()),
        )
        material_key = (
            str(recipe.get("material_type")),
            " ".join(str(recipe.get("material_name") or "").strip().split()),
        )
        if product_key[0] != "finished_product" or product_key not in item_lookup:
            raise ValueError(f"Recipe product is invalid: {product_key[1]}.")
        if material_key[0] != "raw_material" or material_key not in item_lookup:
            raise ValueError(f"Recipe material is invalid: {material_key[1]}.")
        pair = (product_key[1], material_key[1])
        if pair in seen_recipes:
            raise ValueError(f"Duplicate recipe ingredient: {pair[0]} / {pair[1]}.")
        seen_recipes.add(pair)
        required = _three_place_decimal(recipe.get("quantity_required"), "Recipe quantity", positive=True)
        material = item_lookup[material_key]
        recipe["product_name"] = product_key[1]
        recipe["material_name"] = material_key[1]
        recipe["quantity_required"] = measurements.convert_quantity(
            required, recipe.get("unit"), material["unit"], material["name"]
        )
        recipe["unit"] = material["unit"]
        normalized_recipes.append(recipe)

    normalized_batches: list[dict] = []
    batch_codes: set[str] = set()
    for source in batches:
        batch = dict(source)
        item_key = (
            str(batch.get("item_type")),
            " ".join(str(batch.get("name") or "").strip().split()),
        )
        batch["name"] = item_key[1]
        if item_key[0] != "finished_product" or item_key not in item_lookup:
            raise ValueError(f"Batch product is invalid: {item_key[1]}.")
        code = str(batch.get("batch_code") or "").strip().upper()
        if not code or code in batch_codes:
            raise ValueError(f"Batch code is missing or duplicated: {code}.")
        batch_codes.add(code)
        received = _three_place_decimal(batch.get("quantity_received"), f"{code} received quantity", positive=True)
        available = _three_place_decimal(batch.get("quantity_available"), f"{code} available quantity", nonnegative=True)
        if received != received.to_integral_value() or available != available.to_integral_value():
            raise ValueError(f"{code} must contain whole packs.")
        if available > received:
            raise ValueError(f"{code} available quantity exceeds received quantity.")
        if measurements.canonical_unit(batch.get("unit")) != "pack":
            raise ValueError(f"{code} must use pack as its unit.")
        if batch.get("expiry_date") < batch.get("received_date"):
            raise ValueError(f"{code} expires before it was received.")
        batch.update({"batch_code": code, "quantity_received": received, "quantity_available": available, "unit": "pack"})
        normalized_batches.append(batch)

    return normalized_items, normalized_recipes, normalized_batches


def source_catalog(source_dsn: str) -> tuple[list[dict], list[dict], list[dict]]:
    with psycopg2.connect(source_dsn, connect_timeout=15) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'inventory_items';
            """)
            item_columns = {row["column_name"] for row in cursor.fetchall()}
            pack_columns = (
                ", pack_size, pack_size_unit, pack_content_status"
                if {"pack_size", "pack_size_unit", "pack_content_status"}.issubset(item_columns)
                else ", NULL::numeric AS pack_size, NULL::text AS pack_size_unit, NULL::text AS pack_content_status"
            )
            cursor.execute(
                f"""
                SELECT item_id, item_type, category, name, description, unit,
                       base_price, quantity_available, is_active, created_at
                       {pack_columns}
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
                        quantity_available, pack_size, pack_size_unit,
                        pack_content_status, is_active, created_at
                    ) VALUES (%(item_type)s, %(category)s, %(name)s, %(description)s,
                              %(unit)s, %(base_price)s, %(quantity_available)s,
                              %(pack_size)s, %(pack_size_unit)s, %(pack_content_status)s,
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

            cursor.execute("""
                INSERT INTO inventory_movements (
                    item_id, movement_type, quantity_delta, unit,
                    balance_before, balance_after, actor_name, note
                )
                SELECT item_id, 'opening_balance', quantity_available, unit,
                       0, quantity_available, 'MEATTRACK', 'Imported opening raw-material balance'
                FROM inventory_items
                WHERE item_type = 'raw_material';

                INSERT INTO inventory_movements (
                    item_id, affected_batch_id, movement_type, quantity_delta, unit,
                    balance_before, balance_after, actor_name, note
                )
                SELECT item_id, batch_id, 'opening_balance', quantity_available, unit,
                       0, quantity_available, 'MEATTRACK', 'Imported opening finished-batch balance'
                FROM inventory_batches;
            """)


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

    items, recipes, batches = validate_and_normalize_catalog(*source_catalog(source_dsn))
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
