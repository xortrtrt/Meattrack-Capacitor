from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import APP_ENV, database_dsn


DATASET_MARKER = "DUMMY_FORECAST_V1"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "forecast-seed-history.json"


def export_dataset(output_path: Path) -> dict:
    dsn = database_dsn()
    parsed = urlparse(dsn)
    if APP_ENV == "production" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Forecast seed export is restricted to a local development database.")

    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*), COUNT(*) FILTER (WHERE status <> 'fulfilled' OR order_type <> 'reseller')
            FROM orders
            WHERE notes LIKE %s;
            """,
            (f"{DATASET_MARKER}-%",),
        )
        source_order_count, invalid_order_count = cursor.fetchone()
        if not source_order_count:
            raise RuntimeError(f"No {DATASET_MARKER} orders were found in the local database.")
        if invalid_order_count:
            raise RuntimeError(f"Found {invalid_order_count} marked orders that are not fulfilled reseller sales.")

        cursor.execute(
            """
            SELECT i.name,
                   COALESCE(timezone('Asia/Manila', o.fulfilled_at)::date,
                            timezone('Asia/Manila', o.order_date)::date) AS sale_date,
                   SUM(oi.quantity)::bigint AS quantity
            FROM orders o
            JOIN order_items oi USING (order_id)
            JOIN inventory_items i ON i.item_id = oi.product_id
            WHERE o.notes LIKE %s
              AND o.status = 'fulfilled'
              AND o.order_type = 'reseller'
              AND i.item_type = 'finished_product'
              AND i.category IS DISTINCT FROM 'Package'
            GROUP BY i.name, COALESCE(timezone('Asia/Manila', o.fulfilled_at)::date,
                                      timezone('Asia/Manila', o.order_date)::date)
            ORDER BY i.name, sale_date;
            """,
            (f"{DATASET_MARKER}-%",),
        )
        rows = cursor.fetchall()

    if not rows:
        raise RuntimeError("Marked fulfilled orders contained no individual finished-product demand.")
    if any(quantity <= 0 for _, _, quantity in rows):
        raise RuntimeError("Forecast seed quantities must be positive.")

    dates = [sale_date for _, sale_date, _ in rows]
    payload = {
        "dataset_key": "synthetic_local_forecast_seed_v1",
        "source_name": DATASET_MARKER,
        "source_order_count": int(source_order_count),
        "source_period_start": min(dates).isoformat(),
        "source_period_end": max(dates).isoformat(),
        "daily_rows": [
            {"product_name": product_name, "sale_date": sale_date.isoformat(), "quantity": int(quantity)}
            for product_name, sale_date, quantity in rows
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export only the marked synthetic sales quantities as forecast seed data."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = export_dataset(args.output.resolve())
    names = {row["product_name"] for row in payload["daily_rows"]}
    total_packs = sum(row["quantity"] for row in payload["daily_rows"])
    print(
        f"Exported {payload['source_order_count']:,} marked source orders as "
        f"{len(payload['daily_rows']):,} daily product-demand rows for {len(names)} products "
        f"({total_packs:,} packs), {payload['source_period_start']} through "
        f"{payload['source_period_end']}."
    )
    print(f"File: {args.output.resolve()}")


if __name__ == "__main__":
    main()
