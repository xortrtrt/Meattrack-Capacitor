from __future__ import annotations

import calendar
import logging
import os
import tempfile
from datetime import date, timedelta
from importlib.metadata import PackageNotFoundError, version
import pandas as pd


LOGGER = logging.getLogger("meattrack.forecasting")

HISTORY_DAYS = 730
MAX_HORIZON_DAYS = 365
MIN_HISTORY_DAYS = 56
MIN_NONZERO_DAYS = 4
PROPHET_MIN_HISTORY_DAYS = 180
PROPHET_MIN_NONZERO_DAYS = 20
BACKTEST_FOLDS = 4
EVENT_PRIOR_SCALE = 8
TSB_ALPHA = 0.2
TSB_BETA = 0.2

METHOD_LABELS = {
    "prophet": "Prophet",
    "tsb": "TSB intermittent demand",
    "seasonal_naive_7d": "7-day seasonal naive",
    "moving_average_28d": "28-day moving average",
    "none": "No model",
    "legacy": "Legacy forecast",
}


def runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("pandas", "prophet"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:  # pragma: no cover - deployment packaging failure
            versions[package] = "unavailable"
    return versions


def business_events(start_date: date, end_date: date) -> pd.DataFrame:
    rows: list[dict] = []
    for year in range(start_date.year, end_date.year + 1):
        for month in range(1, 13):
            rows.extend(
                [
                    {
                        "holiday": "payday_window",
                        "ds": date(year, month, 15),
                        "lower_window": -1,
                        "upper_window": 1,
                        "prior_scale": EVENT_PRIOR_SCALE,
                    },
                    {
                        "holiday": "month_end_payday_window",
                        "ds": date(year, month, calendar.monthrange(year, month)[1]),
                        "lower_window": -1,
                        "upper_window": 1,
                        "prior_scale": EVENT_PRIOR_SCALE,
                    },
                ]
            )
        rows.extend(
            [
                {
                    "holiday": "christmas_rush",
                    "ds": date(year, 12, 24),
                    "lower_window": -8,
                    "upper_window": 1,
                    "prior_scale": EVENT_PRIOR_SCALE,
                },
                {
                    "holiday": "new_year_rush",
                    "ds": date(year, 12, 31),
                    "lower_window": -2,
                    "upper_window": 1,
                    "prior_scale": EVENT_PRIOR_SCALE,
                },
                {
                    "holiday": "batangas_sublian_foundation_season",
                    "ds": date(year, 7, 23),
                    "lower_window": -13,
                    "upper_window": 0,
                    "prior_scale": EVENT_PRIOR_SCALE,
                },
            ]
        )
    return pd.DataFrame(rows)


def build_daily_series(history_rows: list[dict], start_date: date, end_date: date) -> pd.DataFrame:
    if start_date > end_date:
        raise ValueError("Forecast history start must not be after its end.")
    index = pd.date_range(start=start_date, end=end_date, freq="D")
    quantities: dict[pd.Timestamp, float] = {}
    for row in history_rows:
        day = pd.Timestamp(row["sale_date"])
        quantities[day] = quantities.get(day, 0.0) + float(row["quantity"])
    frame = pd.DataFrame({"ds": index})
    frame["y"] = frame["ds"].map(quantities).fillna(0.0).astype(float)
    return frame


def _future_dates(last_date: date, periods: int) -> pd.DatetimeIndex:
    return pd.date_range(start=last_date + timedelta(days=1), periods=periods, freq="D")


def _eligible(method: str, frame: pd.DataFrame) -> bool:
    days = len(frame)
    nonzero = int((frame["y"] > 0).sum())
    if method == "prophet":
        return days >= PROPHET_MIN_HISTORY_DAYS and nonzero >= PROPHET_MIN_NONZERO_DAYS
    if method == "tsb":
        return days >= MIN_HISTORY_DAYS and nonzero >= MIN_NONZERO_DAYS
    if method == "moving_average_28d":
        return days >= 28 and nonzero >= MIN_NONZERO_DAYS
    if method == "seasonal_naive_7d":
        if days < 28 or nonzero < MIN_NONZERO_DAYS:
            return False
        nonzero_weeks = frame.loc[frame["y"] > 0, "ds"].dt.to_period("W").nunique()
        return int(nonzero_weeks) >= 2
    return False


def _tsb_level(values: list[float]) -> float:
    first = next((value for value in values if value > 0), 0.0)
    if first <= 0:
        return 0.0
    size = first
    probability = 1.0
    for value in values:
        occurred = 1.0 if value > 0 else 0.0
        probability += TSB_BETA * (occurred - probability)
        if value > 0:
            size += TSB_ALPHA * (value - size)
    return max(0.0, probability * size)


def _predict_baseline(method: str, frame: pd.DataFrame, periods: int) -> list[float]:
    values = [float(value) for value in frame["y"].tolist()]
    if method == "tsb":
        return [_tsb_level(values)] * periods
    if method == "moving_average_28d":
        level = sum(values[-28:]) / min(28, len(values))
        return [max(0.0, level)] * periods
    if method == "seasonal_naive_7d":
        pattern = values[-7:]
        return [max(0.0, pattern[index % 7]) for index in range(periods)]
    raise ValueError(f"Unknown forecast method: {method}")


def _predict_prophet(frame: pd.DataFrame, periods: int) -> list[dict]:
    os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
    logging.getLogger("prophet").setLevel(logging.ERROR)
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
    try:
        from prophet import Prophet
    except Exception as exc:  # pragma: no cover - environment failure
        raise RuntimeError("Prophet is unavailable") from exc
    logging.getLogger("prophet").setLevel(logging.ERROR)
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    last_date = frame.iloc[-1]["ds"].date()
    dates = _future_dates(last_date, periods)
    model = Prophet(
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=len(frame) >= 365,
        holidays=business_events(frame.iloc[0]["ds"].date(), dates[-1].date()),
        holidays_prior_scale=EVENT_PRIOR_SCALE,
        interval_width=0.8,
    )
    model.add_country_holidays(country_name="PH")
    model.fit(frame[["ds", "y"]])
    predicted = model.predict(pd.DataFrame({"ds": dates}))
    rows: list[dict] = []
    for row in predicted.to_dict("records"):
        point = max(0.0, float(row["yhat"]))
        lower = max(0.0, float(row.get("yhat_lower", point)))
        upper = max(lower, float(row.get("yhat_upper", point)))
        rows.append({"date": row["ds"].date(), "predicted": point, "lower": lower, "upper": upper})
    return rows


def _predict(method: str, frame: pd.DataFrame, periods: int) -> list[float]:
    if method == "prophet":
        return [row["predicted"] for row in _predict_prophet(frame, periods)]
    return _predict_baseline(method, frame, periods)


def _backtest(
    frame: pd.DataFrame,
    horizon: int,
    log_context: dict | None = None,
) -> tuple[dict[str, float], dict[str, list[float]], dict[str, str]]:
    validation_days = min(max(horizon, 7), 28)
    methods = ("prophet", "tsb", "seasonal_naive_7d", "moving_average_28d")
    errors: dict[str, list[float]] = {method: [] for method in methods}
    actuals: dict[str, list[float]] = {method: [] for method in methods}
    successes = {method: 0 for method in methods}
    failures: dict[str, str] = {}

    for fold in range(BACKTEST_FOLDS, 0, -1):
        test_end = len(frame) - (fold - 1) * validation_days
        test_start = test_end - validation_days
        if test_start <= 0:
            continue
        training = frame.iloc[:test_start].copy()
        observed = frame.iloc[test_start:test_end]["y"].astype(float).tolist()
        for method in methods:
            if not _eligible(method, training):
                continue
            try:
                predicted = _predict(method, training, len(observed))
            except Exception as exc:
                failures[method] = f"{exc.__class__.__name__}: {exc}"[:500]
                LOGGER.exception(
                    "Forecast backtest candidate failed for run=%s product=%s method=%s",
                    (log_context or {}).get("run_id"), (log_context or {}).get("product_id"), method,
                )
                continue
            successes[method] += 1
            actuals[method].extend(observed)
            errors[method].extend(actual - estimate for actual, estimate in zip(observed, predicted))

    scores: dict[str, float] = {}
    usable_errors: dict[str, list[float]] = {}
    for method in methods:
        if successes[method] < 3:
            continue
        denominator = max(sum(actuals[method]), 1.0)
        scores[method] = sum(abs(value) for value in errors[method]) / denominator
        usable_errors[method] = errors[method]
    return scores, usable_errors, failures


def _winner(scores: dict[str, float]) -> str | None:
    if not scores:
        return None
    best_score = min(scores.values())
    preference = ("tsb", "seasonal_naive_7d", "moving_average_28d", "prophet")
    close = {method for method, score in scores.items() if score <= best_score + 0.01}
    return next(method for method in preference if method in close)


def _quantile(values: list[float], percentile: float) -> float:
    return float(pd.Series(values, dtype=float).quantile(percentile))


def forecast_product(
    history_rows: list[dict],
    *,
    history_start: date,
    input_end: date,
    horizon: int,
    usable_stock: float,
    log_context: dict | None = None,
) -> dict:
    if not 1 <= horizon <= MAX_HORIZON_DAYS:
        raise ValueError(f"Forecast horizon must be between 1 and {MAX_HORIZON_DAYS} days.")
    frame = build_daily_series(history_rows, history_start, input_end)
    nonzero_days = int((frame["y"] > 0).sum())
    future_dates = _future_dates(input_end, horizon)

    if len(frame) < MIN_HISTORY_DAYS or nonzero_days < MIN_NONZERO_DAYS:
        daily = [
            {"forecast_date": day.date(), "predicted_quantity": 0.0, "confidence_lower": None, "confidence_upper": None}
            for day in future_dates
        ]
        return {
            "summary": {
                "method": "none",
                "diagnostic_status": "insufficient_history",
                "diagnostic_message": f"Needs at least {MIN_HISTORY_DAYS} calendar days and {MIN_NONZERO_DAYS} nonzero demand days.",
                "history_days": len(frame),
                "nonzero_days": nonzero_days,
                "score_metric": None,
                "backtest_score": None,
                "candidate_scores": {},
                "forecast_total": 0.0,
                "confidence_lower_total": None,
                "confidence_upper_total": None,
                "usable_stock": round(float(usable_stock), 3),
                "production_gap": round(-float(usable_stock), 3),
            },
            "daily": daily,
        }

    scores, residuals, failures = _backtest(frame, horizon, log_context=log_context)
    method = _winner(scores)
    diagnostic_status = "ok"
    diagnostic_message = "Selected by four-fold rolling backtest."
    if method is None and _eligible("tsb", frame):
        method = "tsb"
        diagnostic_status = "fallback"
        diagnostic_message = "Backtesting was incomplete; TSB was used as the intermittent-demand fallback."
    if method is None:
        return {
            "summary": {
                "method": "none", "diagnostic_status": "failed",
                "diagnostic_message": "No forecast candidate could be fitted.",
                "history_days": len(frame), "nonzero_days": nonzero_days,
                "score_metric": "wape", "backtest_score": None,
                "candidate_scores": {"scores": scores, "failures": failures},
                "forecast_total": None, "confidence_lower_total": None, "confidence_upper_total": None,
                "usable_stock": round(float(usable_stock), 3), "production_gap": None,
            },
            "daily": [],
        }

    ranked = sorted(scores, key=lambda name: (scores[name], ("tsb", "seasonal_naive_7d", "moving_average_28d", "prophet").index(name)))
    final_rows: list[dict] = []
    while method:
        try:
            if method == "prophet":
                predictions = _predict_prophet(frame, horizon)
                final_rows = [
                    {
                        "forecast_date": row["date"],
                        "predicted_quantity": round(row["predicted"], 3),
                        "confidence_lower": round(row["lower"], 3),
                        "confidence_upper": round(row["upper"], 3),
                    }
                    for row in predictions
                ]
            else:
                predictions = _predict_baseline(method, frame, horizon)
                method_residuals = residuals.get(method, [])
                use_bounds = len(method_residuals) >= 20
                low_error = _quantile(method_residuals, 0.10) if use_bounds else None
                high_error = _quantile(method_residuals, 0.90) if use_bounds else None
                for day, point in zip(future_dates, predictions):
                    lower = max(0.0, point + low_error) if low_error is not None else None
                    upper = max(lower or 0.0, point + high_error) if high_error is not None else None
                    final_rows.append(
                        {
                            "forecast_date": day.date(),
                            "predicted_quantity": round(max(0.0, point), 3),
                            "confidence_lower": round(lower, 3) if lower is not None else None,
                            "confidence_upper": round(upper, 3) if upper is not None else None,
                        }
                    )
            break
        except Exception as exc:
            failures[method] = f"{exc.__class__.__name__}: {exc}"[:500]
            LOGGER.exception(
                "Final forecast candidate failed for run=%s product=%s method=%s",
                (log_context or {}).get("run_id"), (log_context or {}).get("product_id"), method,
            )
            diagnostic_status = "fallback"
            diagnostic_message = f"{METHOD_LABELS[method]} failed; the next backtested candidate was used."
            ranked = [candidate for candidate in ranked if candidate != method]
            method = ranked[0] if ranked else ("tsb" if _eligible("tsb", frame) and method != "tsb" else None)

    if not method or not final_rows:
        return {
            "summary": {
                "method": "none", "diagnostic_status": "failed",
                "diagnostic_message": "All eligible forecast candidates failed.",
                "history_days": len(frame), "nonzero_days": nonzero_days,
                "score_metric": "wape", "backtest_score": None,
                "candidate_scores": {"scores": scores, "failures": failures},
                "forecast_total": None, "confidence_lower_total": None, "confidence_upper_total": None,
                "usable_stock": round(float(usable_stock), 3), "production_gap": None,
            },
            "daily": [],
        }

    total = round(sum(row["predicted_quantity"] for row in final_rows), 3)
    bounds_available = all(row["confidence_lower"] is not None and row["confidence_upper"] is not None for row in final_rows)
    lower_total = round(sum(row["confidence_lower"] for row in final_rows), 3) if bounds_available else None
    upper_total = round(sum(row["confidence_upper"] for row in final_rows), 3) if bounds_available else None
    return {
        "summary": {
            "method": method,
            "diagnostic_status": diagnostic_status,
            "diagnostic_message": diagnostic_message,
            "history_days": len(frame),
            "nonzero_days": nonzero_days,
            "score_metric": "wape",
            "backtest_score": round(scores[method], 6) if method in scores else None,
            "candidate_scores": {"scores": {key: round(value, 6) for key, value in scores.items()}, "failures": failures},
            "forecast_total": total,
            "confidence_lower_total": lower_total,
            "confidence_upper_total": upper_total,
            "usable_stock": round(float(usable_stock), 3),
            "production_gap": round(total - float(usable_stock), 3),
        },
        "daily": final_rows,
    }
