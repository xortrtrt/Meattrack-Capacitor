from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
import sys
from urllib.parse import urlparse

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import APP_ENV, database_dsn


EXPECTED_SOURCE = "DUMMY_FORECAST_V1"
EXPECTED_DATASET_KEY = "synthetic_local_forecast_seed_v1"


def load_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "dataset_key",
        "source_name",
        "source_order_count",
        "source_period_start",
        "source_period_end",
        "daily_rows",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError("Forecast seed file is missing required metadata or daily rows.")
    if payload["source_name"] != EXPECTED_SOURCE:
        raise ValueError("The import accepts only the marked DUMMY_FORECAST_V1 dataset.")
    if payload["dataset_key"] != EXPECTED_DATASET_KEY:
        raise ValueError("The import accepts only the reserved local forecast seed dataset key.")
    if not isinstance(payload["dataset_key"], str) or not payload["dataset_key"].strip():
        raise ValueError("Dataset key must be a non-empty string.")
    if not isinstance(payload["source_order_count"], int) or payload["source_order_count"] <= 0:
        raise ValueError("Source order count must be a positive integer.")
    date.fromisoformat(payload["source_period_start"])
    date.fromisoformat(payload["source_period_end"])
    if payload["source_period_end"] < payload["source_period_start"]:
        raise ValueError("Source period end precedes its start.")
    if not isinstance(payload["daily_rows"], list) or not payload["daily_rows"]:
        raise ValueError("No daily product-demand rows were supplied.")

    seen: set[tuple[str, str]] = set()
    row_dates: list[str] = []
    for row in payload["daily_rows"]:
        if not isinstance(row, dict) or not {"product_name", "sale_date", "quantity"}.issubset(row):
            raise ValueError("Each daily row must contain product_name, sale_date, and quantity.")
        if not isinstance(row["product_name"], str) or not row["product_name"].strip():
            raise ValueError("Daily row product name must be a non-empty string.")
        date.fromisoformat(row["sale_date"])
        if not isinstance(row["quantity"], int) or row["quantity"] <= 0:
            raise ValueError("Daily demand quantities must be positive whole packs.")
        row_key = (row["product_name"].casefold(), row["sale_date"])
        if row_key in seen:
            raise ValueError("Duplicate product/date row found in seed file.")
        seen.add(row_key)
        row_dates.append(row["sale_date"])
    if min(row_dates) != payload["source_period_start"] or max(row_dates) != payload["source_period_end"]:
        raise ValueError("Dataset period metadata does not match the included daily rows.")
    return payload


def import_dataset(path: Path, team_leader_email: str, apply: bool) -> dict:
    payload = load_payload(path)
    dsn = database_dsn()
    parsed = urlparse(dsn)
    if apply and (APP_ENV != "production" or parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise RuntimeError("Applying forecast seed data requires the production database environment.")

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account_id, name
                FROM accounts
                WHERE lower(email) = lower(%s)
                  AND account_type = 'team_leader'
                  AND team_leader_role = 'sales'
                  AND is_active = true;
                """,
                (team_leader_email.strip(),),
            )
            leaders = cursor.fetchall()
            if len(leaders) != 1:
                raise RuntimeError("The supplied email must identify exactly one active Sales Leader account.")
            leader_id, leader_name = leaders[0]

            cursor.execute(
                """
                SELECT item_id, name
                FROM inventory_items
                WHERE item_type = 'finished_product'
                  AND is_active = true
                  AND category IS DISTINCT FROM 'Package';
                """
            )
            product_map: dict[str, int] = {}
            ambiguous: set[str] = set()
            for product_id, product_name in cursor.fetchall():
                normalized = product_name.strip().casefold()
                if normalized in product_map:
                    ambiguous.add(normalized)
                product_map[normalized] = int(product_id)
            if ambiguous:
                raise RuntimeError("Active individual catalog contains ambiguous duplicate product names.")

            prepared_rows = []
            unmapped: set[str] = set()
            for row in payload["daily_rows"]:
                product_id = product_map.get(row["product_name"].strip().casefold())
                if product_id is None:
                    unmapped.add(row["product_name"])
                prepared_rows.append(
                    (
                        payload["dataset_key"],
                        row["product_name"].strip(),
                        product_id,
                        date.fromisoformat(row["sale_date"]),
                        row["quantity"],
                    )
                )

            mapped_rows = len(prepared_rows) - sum(
                1 for row in prepared_rows if row[2] is None
            )
            summary = {
                "dataset_key": payload["dataset_key"],
                "source_order_count": payload["source_order_count"],
                "daily_rows": len(prepared_rows),
                "mapped_rows": mapped_rows,
                "mapped_products": len(
                    {row[2] for row in prepared_rows if row[2] is not None}
                ),
                "unmapped_products": sorted(unmapped),
                "assigned_to": leader_name,
                "applied": apply,
            }
            if not apply:
                return summary

            cursor.execute(
                """
                INSERT INTO forecast_demand_seed_datasets (
                    dataset_key, source_name, source_order_count, source_period_start,
                    source_period_end, team_leader_account_id, imported_at
                ) VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (dataset_key) DO UPDATE SET
                    source_name = EXCLUDED.source_name,
                    source_order_count = EXCLUDED.source_order_count,
                    source_period_start = EXCLUDED.source_period_start,
                    source_period_end = EXCLUDED.source_period_end,
                    team_leader_account_id = EXCLUDED.team_leader_account_id,
                    imported_at = now();
                """,
                (
                    payload["dataset_key"],
                    payload["source_name"],
                    payload["source_order_count"],
                    date.fromisoformat(payload["source_period_start"]),
                    date.fromisoformat(payload["source_period_end"]),
                    int(leader_id),
                ),
            )
            cursor.execute(
                "DELETE FROM forecast_demand_seed_daily WHERE dataset_key = %s;",
                (payload["dataset_key"],),
            )
            cursor.executemany(
                """
                INSERT INTO forecast_demand_seed_daily (
                    dataset_key, source_product_name, product_id, sale_date, quantity
                ) VALUES (%s, %s, %s, %s, %s);
                """,
                prepared_rows,
            )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import synthetic daily demand for forecasting without creating operational orders."
    )
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--team-leader-email", required=True)
    parser.add_argument("--apply", action="store_true", help="Write this dataset to the production forecast seed tables.")
    args = parser.parse_args()

    summary = import_dataset(args.file.resolve(), args.team_leader_email, args.apply)
    mode = "Applied" if args.apply else "Dry run"
    print(f"{mode}: {summary['source_order_count']:,} source orders represented by "
          f"{summary['daily_rows']:,} daily demand rows; {summary['mapped_rows']:,} rows mapped to "
          f"{summary['mapped_products']} active individual products; assigned to {summary['assigned_to']}.")
    if summary["unmapped_products"]:
        print("Unmapped product names remain stored but are excluded from forecasts until catalog setup: "
              + ", ".join(summary["unmapped_products"]))


if __name__ == "__main__":
    main()
