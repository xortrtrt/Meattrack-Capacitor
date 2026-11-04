from __future__ import annotations

from datetime import date, timedelta
from contextlib import contextmanager

import pandas as pd
import pytest

from app import forecasting
from app import repositories
from app import worker
from app.repositories import forecasts as forecast_repository


def test_build_daily_series_fills_missing_days_with_zero():
    start = date(2026, 1, 1)
    frame = forecasting.build_daily_series(
        [
            {"sale_date": start, "quantity": 3},
            {"sale_date": start + timedelta(days=2), "quantity": 5},
        ],
        start,
        start + timedelta(days=3),
    )

    assert frame["y"].tolist() == [3.0, 0.0, 5.0, 0.0]


def test_build_daily_series_matches_timezone_aware_sale_dates_to_local_days():
    start = date(2026, 1, 1)
    frame = forecasting.build_daily_series(
        [
            {
                "sale_date": pd.Timestamp("2026-01-01T00:00:00+08:00"),
                "quantity": 7,
            }
        ],
        start,
        start + timedelta(days=1),
    )

    assert frame["y"].tolist() == [7.0, 0.0]


def test_insufficient_history_starts_tomorrow_and_covers_full_horizon():
    today = date(2026, 9, 28)
    result = forecasting.forecast_product(
        [],
        history_start=today - timedelta(days=20),
        input_end=today,
        horizon=7,
        usable_stock=10,
    )

    assert result["summary"]["diagnostic_status"] == "insufficient_history"
    assert len(result["daily"]) == 7
    assert result["daily"][0]["forecast_date"] == today + timedelta(days=1)
    assert result["daily"][-1]["forecast_date"] == today + timedelta(days=7)
    assert result["summary"]["forecast_total"] == 0
    assert result["summary"]["production_gap"] == -10


def test_backtested_baseline_saves_daily_predictions_and_total(monkeypatch):
    today = date(2026, 9, 28)
    start = today - timedelta(days=89)
    history = [
        {"sale_date": start + timedelta(days=index), "quantity": 4 if index % 7 == 0 else 0}
        for index in range(90)
    ]
    monkeypatch.setattr(
        forecasting,
        "_backtest",
        lambda frame, horizon, log_context=None: ({"tsb": 0.2, "moving_average_28d": 0.5}, {"tsb": [0.0] * 28}, {}),
    )

    result = forecasting.forecast_product(
        history,
        history_start=start,
        input_end=today,
        horizon=14,
        usable_stock=2,
    )

    assert result["summary"]["method"] == "tsb"
    assert result["summary"]["diagnostic_status"] == "fallback"
    assert len(result["daily"]) == 14
    assert result["daily"][0]["forecast_date"] == today + timedelta(days=1)
    assert result["summary"]["forecast_total"] == round(
        sum(row["predicted_quantity"] for row in result["daily"]), 3
    )


@pytest.mark.parametrize("scores", [{"prophet": 0.9, "tsb": 0.1}, {"tsb": 0.1}])
def test_prophet_is_primary_even_with_worse_or_unavailable_backtest(monkeypatch, scores):
    start = date(2026, 1, 1)
    end = start + timedelta(days=179)
    history = [{"sale_date": start + timedelta(days=i), "quantity": 5} for i in range(180)]
    monkeypatch.setattr(forecasting, "_backtest", lambda *args, **kwargs: (scores, {}, {}))
    monkeypatch.setattr(forecasting, "_predict_prophet", lambda frame, periods: [
        {"date": end + timedelta(days=i + 1), "predicted": 5, "lower": 3, "upper": 7}
        for i in range(periods)
    ])
    result = forecasting.forecast_product(history, history_start=start, input_end=end, horizon=7, usable_stock=10)
    assert result["summary"]["method"] == "prophet"
    assert result["summary"]["diagnostic_status"] == "ok"
    assert result["summary"]["backtest_score"] == scores.get("prophet")
    assert result["summary"]["forecast_total"] == 35
    assert result["summary"]["production_gap"] == 25
    assert len(result["daily"]) == 7


def test_prophet_fit_failure_uses_best_backtested_baseline(monkeypatch):
    start = date(2026, 1, 1)
    history = [{"sale_date": start + timedelta(days=i), "quantity": 5} for i in range(180)]
    monkeypatch.setattr(forecasting, "_backtest", lambda *args, **kwargs: (
        {"prophet": 0.9, "tsb": 0.1, "moving_average_28d": 0.2}, {"tsb": [0.0] * 28}, {}
    ))
    def fail_prophet(*args):
        raise RuntimeError("Model unavailable")
    monkeypatch.setattr(forecasting, "_predict_prophet", fail_prophet)
    result = forecasting.forecast_product(history, history_start=start,
        input_end=start + timedelta(days=179), horizon=7, usable_stock=0)
    assert result["summary"]["method"] == "tsb"
    assert result["summary"]["diagnostic_status"] == "fallback"
    assert "prophet" in result["summary"]["candidate_scores"]["failures"]
    assert len(result["daily"]) == 7


def test_winner_prefers_prophet_over_lower_baseline_error():
    assert forecasting._winner({"prophet": 0.9, "tsb": 0.1}) == "prophet"
    assert forecasting._winner({"tsb": 0.1, "moving_average_28d": 0.5}) == "tsb"
    assert forecasting._winner({}) is None


def test_horizon_is_limited_server_side():
    today = date(2026, 9, 28)
    try:
        forecasting.forecast_product([], history_start=today, input_end=today, horizon=366, usable_stock=0)
    except ValueError as exc:
        assert "between 1 and 365" in str(exc)
    else:
        raise AssertionError("Expected the server-side horizon limit to reject 366 days")


def test_prophet_future_frame_begins_after_input_end(monkeypatch):
    captured = {}

    class FakeModel:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def add_country_holidays(self, country_name):
            captured["country"] = country_name

        def fit(self, frame):
            captured["fit_end"] = frame.iloc[-1]["ds"].date()

        def predict(self, future):
            captured["future"] = future.copy()
            result = future.copy()
            result["yhat"] = 2.0
            result["yhat_lower"] = 1.0
            result["yhat_upper"] = 3.0
            return result

    monkeypatch.setitem(__import__("sys").modules, "prophet", type("ProphetModule", (), {"Prophet": FakeModel}))
    end = date(2026, 9, 28)
    frame = pd.DataFrame({
        "ds": pd.date_range(end=end, periods=200, freq="D"),
        "y": [1.0] * 200,
    })

    rows = forecasting._predict_prophet(frame, 7)

    assert captured["country"] == "PH"
    assert captured["fit_end"] == end
    assert captured["future"].iloc[0]["ds"].date() == end + timedelta(days=1)
    assert captured["future"].iloc[-1]["ds"].date() == end + timedelta(days=7)
    assert len(rows) == 7


def test_real_prophet_integration_preserves_requested_future_dates():
    end = date(2026, 9, 28)
    frame = pd.DataFrame({
        "ds": pd.date_range(end=end, periods=200, freq="D"),
        "y": [3.0 if index % 7 == 0 else 0.0 for index in range(200)],
    })

    rows = forecasting._predict_prophet(frame, 7)

    assert rows[0]["date"] == end + timedelta(days=1)
    assert rows[-1]["date"] == end + timedelta(days=7)
    assert all(row["predicted"] >= 0 for row in rows)


def test_queue_forecast_uses_requesting_owner_and_async_status(monkeypatch):
    calls = []

    class Cursor:
        last_query = ""

        def execute(self, query, params=None):
            self.last_query = query
            calls.append((query, params))

        def fetchone(self):
            if "FROM accounts" in self.last_query:
                return {"account_id": 42}
            if "status IN ('queued', 'running')" in self.last_query:
                return None
            if "INSERT INTO forecast_runs" in self.last_query:
                return {"forecast_run_id": 9, "status": "queued"}
            return None

    @contextmanager
    def transaction():
        yield Cursor()

    monkeypatch.setattr(forecast_repository, "get_transaction_cursor", transaction)
    run = repositories.queue_forecast("Demand forecast", 30, 42)

    insert = next(call for call in calls if "INSERT INTO forecast_runs" in call[0])
    assert insert[1][0] == 42
    assert insert[1][4] == 30
    assert '"demand_source": "fulfilled_sales_leader_attributed_order_items"' in insert[1][-1]
    assert run == {"forecast_run_id": 9, "status": "queued", "existing": False}


def test_queue_forecast_rejects_horizon_above_server_limit():
    try:
        repositories.queue_forecast("Demand forecast", 366, 42)
    except ValueError as exc:
        assert "between 1 and 365" in str(exc)
    else:
        raise AssertionError("Expected queue validation to reject 366 days")


def test_sales_history_uses_sales_leader_attributed_fulfilled_orders(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        forecast_repository,
        "fetch_all",
        lambda query, params=None: captured.update(query=query, params=params) or [],
    )

    repositories.product_sales_history([1], date(2026, 1, 1), date(2026, 1, 31))

    assert "o.order_type = 'walk_in'" in captured["query"]
    assert "o.order_type = 'reseller'" in captured["query"]
    assert "team_leader_role IN ('sales', 'sales-supervisor')" in captured["query"]
    assert "sales_report_items" not in captured["query"]


def test_worker_forecast_failure_is_persisted_for_retry(monkeypatch):
    run = {"forecast_run_id": 4, "attempt_count": 1}
    failures = []
    monkeypatch.setattr(worker.repositories, "claim_forecast_run", lambda: run)
    monkeypatch.setattr(worker.repositories, "process_forecast_run", lambda value: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(worker.repositories, "fail_forecast_run", lambda value, exc: failures.append((value, str(exc))))

    assert worker.process_one_forecast() is True
    assert failures == [(run, "boom")]
