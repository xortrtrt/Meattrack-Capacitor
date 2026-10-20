from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.business_time import business_today
from app.config import APP_ENV, BUSINESS_TIMEZONE, database_dsn


DATASET_MARKER = "DUMMY_FORECAST_V1"
DEFAULT_DAYS = 730
DEFAULT_RANDOM_SEED = 20261019


@dataclass(frozen=True)
class ProductProfile:
    name: str
    category: str
    unit_price: float
    daily_base: float
    zero_chance: float


@dataclass(frozen=True)
class DummyOrder:
    order_key: str
    sold_at: datetime
    event_tags: str
    items: tuple[tuple[str, int, float], ...]

    @property
    def total_amount(self) -> float:
        return round(sum(quantity * unit_price for _, quantity, unit_price in self.items), 2)


SEED_PRODUCT_PROFILES = (
    ProductProfile("Pork Garlic Longganisa", "Pork", 120.00, 7.8, 0.03),
    ProductProfile("Tocino Ala Eh", "Pork", 130.00, 7.0, 0.04),
    ProductProfile("Beef Tapa Ala Eh", "Beef", 180.00, 3.8, 0.12),
    ProductProfile("Cheesy Overload Sausage", "Pork", 150.00, 4.4, 0.09),
    ProductProfile("Hungarian Sausage", "Beef", 160.00, 3.5, 0.13),
    ProductProfile("Bacon (Smoked)", "Pork", 170.00, 3.2, 0.15),
)


WEEKDAY_MULTIPLIERS = {
    0: 0.78,
    1: 0.88,
    2: 0.93,
    3: 1.00,
    4: 1.18,
    5: 1.34,
    6: 1.12,
}

RESELLER_ORDER_DAY_CHANCE = {
    0: 0.42,
    1: 0.50,
    2: 0.47,
    3: 0.55,
    4: 0.68,
    5: 0.55,
    6: 0.25,
}


def _poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    threshold = math.exp(-mean)
    product = 1.0
    count = 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def _is_payday_window(day: date) -> bool:
    next_day = day + timedelta(days=1)
    previous_day = day - timedelta(days=1)
    return day.day in {14, 15, 16} or next_day.month != day.month or previous_day.month != day.month


def demand_multiplier(day: date) -> tuple[float, list[str]]:
    multiplier = WEEKDAY_MULTIPLIERS[day.weekday()]
    tags = [day.strftime("%A").lower()]

    if _is_payday_window(day):
        multiplier *= 1.18
        tags.append("payday_window")
    if day.month == 12 and 16 <= day.day <= 24:
        multiplier *= 1.58
        tags.append("christmas_rush")
    elif day.month == 12 and 29 <= day.day <= 31:
        multiplier *= 1.38
        tags.append("new_year_rush")
    elif (day.month, day.day) in {(12, 25), (1, 1)}:
        multiplier *= 0.62
        tags.append("holiday_slowdown")
    if day.month == 7 and 10 <= day.day <= 23:
        multiplier *= 1.20
        tags.append("sublian_season")

    return multiplier, tags


def _daily_product_totals(
    profiles: tuple[ProductProfile, ...],
    day: date,
    day_index: int,
    total_days: int,
    rng: random.Random,
) -> tuple[dict[str, int], list[str]]:
    if rng.random() > RESELLER_ORDER_DAY_CHANCE[day.weekday()]:
        return {}, ["no_reseller_order"]

    event_multiplier, tags = demand_multiplier(day)
    progress = day_index / max(total_days - 1, 1)
    trend_multiplier = 0.94 + (0.12 * progress)
    promoted_index = (day.year * 12 + day.month) % len(profiles)

    quantities: dict[str, int] = {}
    for index, profile in enumerate(profiles):
        if rng.random() < profile.zero_chance:
            continue
        promotion_multiplier = 1.24 if index == promoted_index and day.day <= 7 else 1.0
        mean = profile.daily_base * event_multiplier * trend_multiplier * promotion_multiplier
        overdispersed_mean = rng.gammavariate(5.0, mean / 5.0)
        quantity = _poisson(rng, overdispersed_mean)
        if quantity > 0:
            quantities[profile.name] = quantity

    return quantities, tags


def _split_into_orders(
    profiles_by_name: dict[str, ProductProfile],
    day: date,
    quantities: dict[str, int],
    tags: list[str],
    rng: random.Random,
) -> list[DummyOrder]:
    total_packs = sum(quantities.values())
    if total_packs == 0:
        return []

    target_basket = rng.uniform(18.0, 30.0)
    order_count = min(4, max(1, round(total_packs / target_basket)))
    baskets: list[dict[str, int]] = [defaultdict(int) for _ in range(order_count)]
    for product_name, quantity in quantities.items():
        start = rng.randrange(order_count)
        for unit_index in range(quantity):
            basket_index = (start + unit_index + rng.randrange(min(order_count, 3))) % order_count
            baskets[basket_index][product_name] += 1

    zone = ZoneInfo(BUSINESS_TIMEZONE)
    nonempty_baskets = [basket for basket in baskets if basket]
    orders: list[DummyOrder] = []
    for index, basket in enumerate(nonempty_baskets, start=1):
        minutes = 9 * 60 + round((index - 0.5) * (9 * 60 / len(nonempty_baskets))) + rng.randint(-12, 12)
        sold_at = datetime.combine(day, time(hour=minutes // 60, minute=minutes % 60), tzinfo=zone)
        items = tuple(
            (name, quantity, profiles_by_name[name].unit_price)
            for name, quantity in sorted(basket.items())
        )
        orders.append(
            DummyOrder(
                order_key=f"{DATASET_MARKER}-{day:%Y%m%d}-{index:02d}",
                sold_at=sold_at,
                event_tags=",".join(tags),
                items=items,
            )
        )
    return orders


def generate_dummy_orders(
    profiles: tuple[ProductProfile, ...],
    start_date: date,
    end_date: date,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> list[DummyOrder]:
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if not profiles:
        raise ValueError("At least one product profile is required")

    rng = random.Random(random_seed)
    profiles_by_name = {profile.name: profile for profile in profiles}
    total_days = (end_date - start_date).days + 1
    orders: list[DummyOrder] = []
    for day_index in range(total_days):
        day = start_date + timedelta(days=day_index)
        quantities, tags = _daily_product_totals(profiles, day, day_index, total_days, rng)
        orders.extend(_split_into_orders(profiles_by_name, day, quantities, tags, rng))
    return orders


def write_csv(orders: list[DummyOrder], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "order_key",
                "sale_date",
                "fulfilled_at",
                "order_type",
                "product_name",
                "quantity_packs",
                "unit_price_php",
                "line_total_php",
                "event_tags",
            ]
        )
        for order in orders:
            for product_name, quantity, unit_price in order.items:
                writer.writerow(
                    [
                        order.order_key,
                        order.sold_at.date().isoformat(),
                        order.sold_at.isoformat(),
                        "reseller",
                        product_name,
                        quantity,
                        f"{unit_price:.2f}",
                        f"{quantity * unit_price:.2f}",
                        order.event_tags,
                    ]
                )


def insert_dummy_orders(
    cur,
    orders: list[DummyOrder],
    product_ids: dict[str, int],
    reseller_id: int,
    team_leader_account_id: int,
) -> None:
    for order in orders:
        transaction_key = "".join(character for character in order.order_key.upper() if character.isalnum())
        cur.execute(
            """
            INSERT INTO orders (
                order_type, reseller_id, team_leader_account_id, order_date, status,
                total_amount, notes,
                payment_transaction_number, payment_transaction_key, payment_paid_at,
                payment_amount, payment_ocr_confidence, payment_extraction_method,
                payment_confirmed_at, payment_ocr_engine
            )
            VALUES ('reseller', %s, %s, %s, 'pending', %s, %s,
                    %s, %s, %s, %s, 1.0, 'manual_corrected', %s, 'synthetic_seed')
            RETURNING order_id;
            """,
            (
                reseller_id,
                team_leader_account_id,
                order.sold_at,
                order.total_amount,
                f"{order.order_key}; drivers={order.event_tags}",
                order.order_key,
                transaction_key,
                order.sold_at,
                order.total_amount,
                order.sold_at,
            ),
        )
        order_id = cur.fetchone()[0]
        cur.executemany(
            """
            INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
            VALUES (%s, %s, %s, 'pack', %s);
            """,
            [
                (order_id, product_ids[product_name], quantity, unit_price)
                for product_name, quantity, unit_price in order.items
            ],
        )
        proof_content = f"synthetic payment proof for {order.order_key}".encode("utf-8")
        cur.execute(
            """
            INSERT INTO order_payment_proofs (
                order_id, filename, content_type, content, size_bytes, checksum_sha256, uploaded_at
            )
            VALUES (%s, %s, 'image/png', %s, %s, %s, %s);
            """,
            (
                order_id,
                f"{order.order_key}.png",
                proof_content,
                len(proof_content),
                hashlib.sha256(proof_content).hexdigest(),
                order.sold_at,
            ),
        )
        cur.execute(
            """
            UPDATE orders
            SET status = 'approved', approved_by_account_id = %s, approved_at = %s
            WHERE order_id = %s;

            UPDATE orders
            SET status = 'fulfilled', fulfilled_at = %s
            WHERE order_id = %s;
            """,
            (team_leader_account_id, order.sold_at, order_id, order.sold_at, order_id),
        )


def _profiles_from_database(cur) -> tuple[ProductProfile, ...]:
    cur.execute(
        """
        SELECT name, COALESCE(category, ''), base_price
        FROM inventory_items
        WHERE item_type = 'finished_product' AND is_active = true
        ORDER BY name, item_id;
        """
    )
    known = {profile.name: profile for profile in SEED_PRODUCT_PROFILES}
    profiles: list[ProductProfile] = []
    for name, category, base_price in cur.fetchall():
        known_profile = known.get(name)
        if known_profile:
            profiles.append(
                ProductProfile(name, category, float(base_price), known_profile.daily_base, known_profile.zero_chance)
            )
            continue
        category_key = (category or "").lower()
        daily_base = 5.2 if category_key in {"pork", "chicken"} else 3.6
        profiles.append(ProductProfile(name, category, float(base_price), daily_base, 0.10))
    return tuple(profiles)


def _ensure_local_development_database(dsn: str) -> None:
    parsed = urlparse(dsn)
    if APP_ENV == "production":
        raise RuntimeError("Dummy forecast sales cannot be loaded when APP_ENV=production.")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Dummy forecast sales may only be loaded into a local PostgreSQL database.")


def apply_to_database(start_date: date, end_date: date, random_seed: int, replace: bool, backdate_products: bool) -> int:
    dsn = database_dsn()
    _ensure_local_development_database(dsn)
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT account_id
                FROM accounts
                WHERE account_type = 'team_leader'
                  AND team_leader_role IN ('sales', 'sales-supervisor')
                ORDER BY is_active DESC, account_id
                LIMIT 1;
                """
            )
            creator = cur.fetchone()
            if not creator:
                raise RuntimeError("Create a sales team leader account before loading dummy sales.")
            cur.execute(
                """
                SELECT reseller_id
                FROM resellers
                ORDER BY (reseller_status = 'active') DESC, reseller_id
                LIMIT 1;
                """
            )
            reseller = cur.fetchone()
            if not reseller:
                raise RuntimeError("Create a reseller before loading dummy sales.")

            cur.execute("SELECT COUNT(*) FROM orders WHERE notes LIKE %s;", (f"{DATASET_MARKER}-%",))
            existing_count = int(cur.fetchone()[0])
            if existing_count and not replace:
                raise RuntimeError(
                    f"Found {existing_count} existing {DATASET_MARKER} orders. Re-run with --replace to refresh them."
                )
            if existing_count:
                cur.execute("DELETE FROM orders WHERE notes LIKE %s;", (f"{DATASET_MARKER}-%",))

            profiles = _profiles_from_database(cur)
            if not profiles:
                raise RuntimeError("No active finished products were found.")
            product_names = [profile.name for profile in profiles]
            cur.execute(
                """
                SELECT item_id, name, timezone(%s, created_at)::date
                FROM inventory_items
                WHERE item_type = 'finished_product' AND is_active = true AND name = ANY(%s);
                """,
                (BUSINESS_TIMEZONE, product_names),
            )
            product_rows = cur.fetchall()
            too_new = [name for _, name, created_date in product_rows if created_date > start_date]
            if too_new and not backdate_products:
                raise RuntimeError(
                    "The forecast clamps history to product created_at. Re-run with --backdate-products for this local demo "
                    f"catalog. Products affected: {', '.join(sorted(too_new))}"
                )
            if too_new:
                cur.execute(
                    """
                    UPDATE inventory_items
                    SET created_at = %s
                    WHERE item_type = 'finished_product' AND is_active = true AND name = ANY(%s);
                    """,
                    (datetime.combine(start_date, time(8, 0), tzinfo=ZoneInfo(BUSINESS_TIMEZONE)), too_new),
                )

            product_ids = {name: int(item_id) for item_id, name, _ in product_rows}
            orders = generate_dummy_orders(profiles, start_date, end_date, random_seed)
            insert_dummy_orders(cur, orders, product_ids, int(reseller[0]), int(creator[0]))
            return len(orders)


def _summary(orders: list[DummyOrder]) -> str:
    quantities: dict[str, int] = defaultdict(int)
    dates: dict[str, set[date]] = defaultdict(set)
    for order in orders:
        for product_name, quantity, _ in order.items:
            quantities[product_name] += quantity
            dates[product_name].add(order.sold_at.date())
    lines = [f"Generated {len(orders):,} fulfilled reseller orders."]
    for product_name in sorted(quantities):
        lines.append(f"  {product_name}: {quantities[product_name]:,} packs across {len(dates[product_name]):,} sale days")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic local-only sales history for forecast demos.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--end-date", type=date.fromisoformat, default=business_today())
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--apply", action="store_true", help="Insert the generated orders into the configured local database.")
    parser.add_argument("--replace", action="store_true", help="Delete and replace only orders tagged DUMMY_FORECAST_V1.")
    parser.add_argument(
        "--backdate-products",
        action="store_true",
        help="Backdate active demo products so the forecast includes the generated history.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.days < 180 or args.days > 730:
        raise SystemExit("--days must be between 180 and 730 so Prophet is eligible and the app history limit is respected.")
    start_date = args.end_date - timedelta(days=args.days - 1)
    preview_orders = generate_dummy_orders(SEED_PRODUCT_PROFILES, start_date, args.end_date, args.seed)
    print(_summary(preview_orders))
    if args.csv:
        write_csv(preview_orders, args.csv.resolve())
        print(f"CSV written to {args.csv.resolve()}")
    if args.apply:
        count = apply_to_database(start_date, args.end_date, args.seed, args.replace, args.backdate_products)
        print(f"Inserted {count:,} dummy orders into the local database.")


if __name__ == "__main__":
    main()
