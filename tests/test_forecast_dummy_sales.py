from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app import forecasting
from tools.forecast_dummy_sales import SEED_PRODUCT_PROFILES, demand_multiplier, generate_dummy_orders


def _daily_totals(orders):
    totals = defaultdict(int)
    for order in orders:
        for product_name, quantity, _ in order.items:
            totals[(order.sold_at.date(), product_name)] += quantity
    return totals


def test_dummy_sales_are_deterministic_and_forecast_eligible():
    end_date = date(2026, 10, 19)
    start_date = end_date - timedelta(days=729)

    first = generate_dummy_orders(SEED_PRODUCT_PROFILES, start_date, end_date, random_seed=42)
    second = generate_dummy_orders(SEED_PRODUCT_PROFILES, start_date, end_date, random_seed=42)

    assert first == second
    assert first[0].sold_at.tzinfo is not None
    sale_days = defaultdict(set)
    for order in first:
        assert order.order_key.startswith("DUMMY_FORECAST_V1-")
        assert order.total_amount > 0
        assert len(order.items) >= 1
        for product_name, quantity, unit_price in order.items:
            assert quantity == int(quantity) and quantity > 0
            assert unit_price > 0
            sale_days[product_name].add(order.sold_at.date())

    assert set(sale_days) == {profile.name for profile in SEED_PRODUCT_PROFILES}
    assert all(len(days) >= 20 for days in sale_days.values())
    assert 150 <= len(first) <= 700
    daily_totals = _daily_totals(first)
    for profile in SEED_PRODUCT_PROFILES:
        history = [
            {"sale_date": day, "quantity": quantity}
            for (day, product_name), quantity in daily_totals.items()
            if product_name == profile.name
        ]
        frame = forecasting.build_daily_series(history, start_date, end_date)
        assert forecasting._eligible("prophet", frame)


def test_calendar_drivers_raise_expected_demand_multipliers():
    regular_monday, _ = demand_multiplier(date(2026, 6, 8))
    payday_monday, payday_tags = demand_multiplier(date(2026, 6, 15))
    christmas_friday, christmas_tags = demand_multiplier(date(2026, 12, 18))
    regular_friday, _ = demand_multiplier(date(2026, 11, 20))

    assert payday_monday > regular_monday
    assert "payday_window" in payday_tags
    assert christmas_friday > regular_friday
    assert "christmas_rush" in christmas_tags


def test_generated_history_contains_weekly_and_event_variation():
    end_date = date(2026, 12, 31)
    start_date = end_date - timedelta(days=729)
    orders = generate_dummy_orders(SEED_PRODUCT_PROFILES, start_date, end_date, random_seed=7)
    totals = _daily_totals(orders)
    product = SEED_PRODUCT_PROFILES[0].name

    saturday_values = [quantity for (day, name), quantity in totals.items() if name == product and day.weekday() == 5]
    monday_values = [quantity for (day, name), quantity in totals.items() if name == product and day.weekday() == 0]
    christmas_values = [
        quantity
        for (day, name), quantity in totals.items()
        if name == product and day.month == 12 and 16 <= day.day <= 24
    ]
    ordinary_values = [
        quantity
        for (day, name), quantity in totals.items()
        if name == product and day.month in {2, 3, 4} and day.weekday() == 4
    ]

    assert sum(saturday_values) / len(saturday_values) > sum(monday_values) / len(monday_values)
    assert sum(christmas_values) / len(christmas_values) > sum(ordinary_values) / len(ordinary_values)
