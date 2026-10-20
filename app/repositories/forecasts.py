from __future__ import annotations


from ._shared import *
from .notifications import _create_notification_cursor


def list_forecasts(
    limit: int | None = None,
    q: str = "",
    page: int | None = None,
    page_size: int = 10,
    sort: str = "",
    run_id: int | None = None,
) -> list[dict]:
    query = """
        SELECT fps.forecast_product_summary_id AS forecast_result_id,
               fps.forecast_product_summary_id,
               fps.forecast_run_id,
               fps.product_id,
               p.name AS product,
               daily.forecast_start,
               daily.forecast_date,
               fps.forecast_total AS predicted_quantity,
               fps.forecast_total,
               fps.confidence_lower_total AS confidence_lower,
               fps.confidence_upper_total AS confidence_upper,
               CASE
                   WHEN fps.confidence_lower_total IS NOT NULL AND fps.confidence_upper_total IS NOT NULL
                   THEN trim(to_char(fps.confidence_lower_total, 'FM999999990.0')) || ' - ' || trim(to_char(fps.confidence_upper_total, 'FM999999990.0')) || ' packs planning range'
                   ELSE 'Planning range unavailable'
               END AS confidence,
               fps.method,
               fps.diagnostic_status,
               fps.diagnostic_message,
               fps.history_days,
               fps.nonzero_days,
               fps.score_metric,
               fps.backtest_score,
               fps.usable_stock,
               fps.production_gap,
               GREATEST(COALESCE(fps.production_gap, 0), 0) AS suggested_production,
               f.model_name,
               f.forecast_horizon_days,
               f.status,
               f.notes
        FROM forecast_product_summaries fps
        JOIN inventory_items p ON p.item_id = fps.product_id
        JOIN forecast_runs f ON f.forecast_run_id = fps.forecast_run_id
        LEFT JOIN LATERAL (
            SELECT MIN(fr.forecast_date) AS forecast_start, MAX(fr.forecast_date) AS forecast_date
            FROM forecast_results fr
            WHERE fr.forecast_run_id = fps.forecast_run_id AND fr.product_id = fps.product_id
        ) daily ON true
        WHERE p.item_type = 'finished_product'
          AND fps.forecast_run_id = COALESCE(%s, (
              SELECT forecast_run_id FROM forecast_runs
              WHERE status IN ('completed', 'completed_with_warnings')
              ORDER BY forecast_run_id DESC LIMIT 1
          ))
    """
    params: list[object] = [run_id]
    q = q.strip()
    if q:
        query += " AND p.name ILIKE %s"
        params.append(f"%{q}%")
    query += f" ORDER BY {safe_sort_sql(sort, FORECAST_SORTS, FORECAST_SORTS['newest'])}"
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        query += " LIMIT %s"
        params.append(limit)
    elif page is not None:
        if page < 1:
            raise ValueError("page must be positive")
        query += " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    forecasts = clean_row(fetch_all(query + ";", tuple(params)))
    if forecasts:
        daily_rows = clean_row(fetch_all(
            """
            SELECT forecast_run_id, product_id, forecast_date, predicted_quantity,
                   confidence_lower, confidence_upper
            FROM forecast_results
            WHERE forecast_run_id = %s AND product_id = ANY(%s)
            ORDER BY product_id, forecast_date;
            """,
            (forecasts[0]["forecast_run_id"], [row["product_id"] for row in forecasts]),
        ))
        by_product: dict[int, list[dict]] = {}
        for daily in daily_rows:
            by_product.setdefault(int(daily["product_id"]), []).append(daily)
        for forecast in forecasts:
            forecast["daily"] = by_product.get(int(forecast["product_id"]), [])
            forecast["method_label"] = forecasting.METHOD_LABELS.get(forecast["method"], forecast["method"])
    return forecasts


def latest_forecast_run() -> dict | None:
    row = fetch_one("""
        SELECT f.forecast_run_id, f.run_by_account_id, a.name AS requested_by,
               f.model_name, f.input_period_start, f.input_period_end,
               f.forecast_horizon_days, f.status, f.started_at, f.completed_at,
               f.queued_at, f.total_products, f.processed_products, f.last_error, f.notes
        FROM forecast_runs f
        LEFT JOIN accounts a ON a.account_id = f.run_by_account_id
        ORDER BY f.forecast_run_id DESC
        LIMIT 1;
    """)
    return clean_row(row) if row else None


def count_forecasts(q: str = "", run_id: int | None = None) -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM forecast_product_summaries fps
        JOIN inventory_items p ON p.item_id = fps.product_id
        WHERE p.item_type = 'finished_product'
          AND fps.forecast_run_id = COALESCE(%s, (
              SELECT forecast_run_id FROM forecast_runs
              WHERE status IN ('completed', 'completed_with_warnings')
              ORDER BY forecast_run_id DESC LIMIT 1
          ))
    """
    params: list[object] = [run_id]
    q = q.strip()
    if q:
        query += " AND p.name ILIKE %s"
        params.append(f"%{q}%")
    row = fetch_one(query + ";", tuple(params))
    return int(row["total"])


def product_sales_history(product_ids: list[int], start_date: date, end_date: date) -> dict[int, list[dict]]:
    if not product_ids:
        return {}
    rows = clean_row(fetch_all("""
        SELECT oi.product_id,
               COALESCE(
                   timezone(%s, o.fulfilled_at)::date,
                   timezone(%s, o.order_date)::date
               ) AS sale_date,
               COALESCE(SUM(oi.quantity), 0) AS quantity
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        LEFT JOIN accounts creator ON creator.account_id = o.created_by_account_id
        LEFT JOIN accounts assigned_leader ON assigned_leader.account_id = o.team_leader_account_id
        WHERE o.status = 'fulfilled'
          AND (
              (o.order_type = 'walk_in' AND creator.account_type = 'team_leader'
               AND creator.team_leader_role IN ('sales', 'sales-supervisor'))
              OR
              (o.order_type = 'reseller' AND assigned_leader.account_type = 'team_leader'
               AND assigned_leader.team_leader_role IN ('sales', 'sales-supervisor'))
          )
          AND p.item_type = 'finished_product'
          AND oi.product_id = ANY(%s)
          AND COALESCE(
              timezone(%s, o.fulfilled_at)::date,
              timezone(%s, o.order_date)::date
          ) BETWEEN %s AND %s
        GROUP BY oi.product_id, COALESCE(
            timezone(%s, o.fulfilled_at)::date,
            timezone(%s, o.order_date)::date
        )
        ORDER BY oi.product_id, sale_date;
    """, (
        BUSINESS_TIMEZONE, BUSINESS_TIMEZONE, product_ids,
        BUSINESS_TIMEZONE, BUSINESS_TIMEZONE, start_date, end_date,
        BUSINESS_TIMEZONE, BUSINESS_TIMEZONE,
    )))
    grouped: dict[int, list[dict]] = {product_id: [] for product_id in product_ids}
    for row in rows:
        grouped.setdefault(row["product_id"], []).append(row)
    return grouped


def forecast_business_events(start_date: date, end_date: date):
    return forecasting.business_events(start_date, end_date)


def queue_forecast(model_name: str, forecast_horizon_days: int, requesting_account_id: int) -> dict:
    clean_name = " ".join((model_name or "").strip().split())
    if not 3 <= len(clean_name) <= 80:
        raise ValueError("Model name must be between 3 and 80 characters.")
    if not 1 <= int(forecast_horizon_days) <= forecasting.MAX_HORIZON_DAYS:
        raise ValueError(f"Forecast horizon must be between 1 and {forecasting.MAX_HORIZON_DAYS} days.")

    input_end = business_today()
    input_start = input_end - timedelta(days=forecasting.HISTORY_DAYS - 1)
    configuration = {
        "history_days": forecasting.HISTORY_DAYS,
        "maximum_horizon_days": forecasting.MAX_HORIZON_DAYS,
        "backtest_folds": forecasting.BACKTEST_FOLDS,
        "minimum_history_days": forecasting.MIN_HISTORY_DAYS,
        "minimum_nonzero_days": forecasting.MIN_NONZERO_DAYS,
        "prophet_minimum_history_days": forecasting.PROPHET_MIN_HISTORY_DAYS,
        "prophet_minimum_nonzero_days": forecasting.PROPHET_MIN_NONZERO_DAYS,
        "tsb_alpha": forecasting.TSB_ALPHA,
        "tsb_beta": forecasting.TSB_BETA,
        "demand_source": "fulfilled_sales_leader_attributed_order_items",
        "business_timezone": BUSINESS_TIMEZONE,
        "input_cutoff": input_end.isoformat(),
        "package_versions": forecasting.runtime_versions(),
    }
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('meattrack-forecast-queue'));")
        cur.execute(
            """
            SELECT account_id FROM accounts
            WHERE account_id = %s AND account_type = 'owner' AND is_active = true
            FOR UPDATE;
            """,
            (requesting_account_id,),
        )
        if not cur.fetchone():
            raise ValueError("An active owner account is required to run a forecast.")
        cur.execute(
            """
            SELECT forecast_run_id, status FROM forecast_runs
            WHERE status IN ('queued', 'running')
            ORDER BY forecast_run_id DESC LIMIT 1;
            """
        )
        existing = cur.fetchone()
        if existing:
            return {"forecast_run_id": int(existing["forecast_run_id"]), "status": existing["status"], "existing": True}
        cur.execute(
            """
            INSERT INTO forecast_runs (
                run_by_account_id, model_name, input_period_start, input_period_end,
                forecast_horizon_days, status, queued_at, next_attempt_at, configuration
            )
            VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, %s::jsonb)
            RETURNING forecast_run_id, status;
            """,
            (
                requesting_account_id, clean_name, input_start, input_end, int(forecast_horizon_days),
                business_now(), business_now(), json.dumps(configuration),
            ),
        )
        run = dict(cur.fetchone())
        _add_log_cursor(
            cur,
            actor_account_id=requesting_account_id,
            action="forecast_queued",
            entity_type="forecast_runs",
            entity_id=run["forecast_run_id"],
        )
    run["existing"] = False
    return clean_row(run)


def recover_stale_forecast_runs() -> int:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE forecast_runs
            SET status = 'cancelled', completed_at = now(), locked_at = NULL,
                notes = COALESCE(notes, 'Cancelled by an owner.')
            WHERE status = 'cancelling' AND locked_at < now() - interval '30 minutes';
            """
        )
        cur.execute(
            """
            UPDATE forecast_runs
            SET status = CASE WHEN attempt_count >= 3 THEN 'failed' ELSE 'queued' END,
                next_attempt_at = CASE WHEN attempt_count >= 3 THEN next_attempt_at ELSE now() END,
                locked_at = NULL,
                completed_at = CASE WHEN attempt_count >= 3 THEN now() ELSE completed_at END,
                last_error = COALESCE(last_error, 'Worker lock expired before completion.'),
                processed_products = 0
            WHERE status = 'running' AND locked_at < now() - interval '30 minutes'
            RETURNING forecast_run_id, run_by_account_id, status;
            """
        )
        recovered = [dict(row) for row in cur.fetchall()]
        for run in recovered:
            if run["status"] != "failed":
                continue
            _add_log_cursor(
                cur, actor_account_id=run["run_by_account_id"], action="forecast_failed",
                entity_type="forecast_runs", entity_id=run["forecast_run_id"],
            )
            _create_notification_cursor(
                cur, recipient_account_id=run["run_by_account_id"], category="forecast", severity="warning",
                title="Forecast failed", message="The forecast worker stopped before completion after three attempts.",
                target_url=f"/portal/owner/forecasts?run_id={run['forecast_run_id']}",
                source_type="forecast_runs", source_id=run["forecast_run_id"],
                dedupe_key=f"forecast-run-failed-{run['forecast_run_id']}",
            )
        return len(recovered)


def claim_forecast_run() -> dict | None:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT forecast_run_id, run_by_account_id, model_name, input_period_start,
                   input_period_end, forecast_horizon_days, attempt_count
            FROM forecast_runs
            WHERE status = 'queued' AND attempt_count < 3 AND next_attempt_at <= now()
            ORDER BY forecast_run_id
            FOR UPDATE SKIP LOCKED
            LIMIT 1;
            """
        )
        run = cur.fetchone()
        if not run:
            return None
        cur.execute(
            """
            UPDATE forecast_runs
            SET status = 'running', started_at = COALESCE(started_at, now()),
                locked_at = now(), attempt_count = attempt_count + 1,
                processed_products = 0, last_error = NULL
            WHERE forecast_run_id = %s;
            """,
            (run["forecast_run_id"],),
        )
        claimed = dict(run)
        claimed["attempt_count"] = int(run["attempt_count"]) + 1
        return clean_row(claimed)


def _forecast_products(input_end: date) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT p.item_id AS product_id, p.name, timezone(%s, p.created_at)::date AS product_created_date,
               COALESCE(SUM(ib.quantity_available) FILTER (
                   WHERE ib.quality_status = 'approved' AND ib.expiry_date >= %s
               ), 0) AS usable_stock
        FROM inventory_items p
        LEFT JOIN inventory_batches ib ON ib.item_id = p.item_id
        WHERE p.item_type = 'finished_product' AND p.is_active = true
        GROUP BY p.item_id, p.name, p.created_at
        ORDER BY p.name, p.item_id;
        """,
        (BUSINESS_TIMEZONE, input_end),
    ))


def process_forecast_run(run: dict) -> dict:
    run_id = int(run["forecast_run_id"])
    input_start = run["input_period_start"]
    input_end = run["input_period_end"]
    horizon = int(run["forecast_horizon_days"])
    products = _forecast_products(input_end)
    product_ids = [int(product["product_id"]) for product in products]
    histories = product_sales_history(product_ids, input_start, input_end)
    execute_write(
        "UPDATE forecast_runs SET total_products = %s, processed_products = 0, locked_at = now() WHERE forecast_run_id = %s;",
        (len(products), run_id),
    )

    outputs: list[tuple[dict, dict]] = []
    for index, product in enumerate(products, start=1):
        cancellation = fetch_one("SELECT status FROM forecast_runs WHERE forecast_run_id = %s;", (run_id,))
        if cancellation and cancellation["status"] == "cancelling":
            execute_write(
                """
                UPDATE forecast_runs SET status = 'cancelled', completed_at = now(), locked_at = NULL,
                    notes = 'Cancelled by an owner between product forecasts.'
                WHERE forecast_run_id = %s AND status = 'cancelling';
                """,
                (run_id,),
            )
            return {"forecast_run_id": run_id, "status": "cancelled", "warnings": 0}
        created_date = product["product_created_date"]
        history_start = max(input_start, created_date) if created_date else input_start
        try:
            output = forecasting.forecast_product(
                histories.get(int(product["product_id"]), []),
                history_start=history_start,
                input_end=input_end,
                horizon=horizon,
                usable_stock=float(product["usable_stock"]),
                log_context={"run_id": run_id, "product_id": int(product["product_id"])},
            )
        except Exception as exc:
            logging.getLogger("meattrack.forecasting").exception(
                "Product forecast failed for run=%s product=%s", run_id, product["product_id"]
            )
            output = {
                "summary": {
                    "method": "none", "diagnostic_status": "failed",
                    "diagnostic_message": f"{exc.__class__.__name__}: {exc}"[:500],
                    "history_days": 0, "nonzero_days": 0, "score_metric": None,
                    "backtest_score": None, "candidate_scores": {"failures": {"pipeline": exc.__class__.__name__}},
                    "forecast_total": None, "confidence_lower_total": None, "confidence_upper_total": None,
                    "usable_stock": float(product["usable_stock"]), "production_gap": None,
                },
                "daily": [],
            }
        outputs.append((product, output))
        execute_write(
            "UPDATE forecast_runs SET processed_products = %s, locked_at = now() WHERE forecast_run_id = %s AND status = 'running';",
            (index, run_id),
        )

    warning_count = sum(
        1 for _, output in outputs
        if output["summary"]["diagnostic_status"] in {"fallback", "insufficient_history", "failed"}
    )
    final_status = "completed_with_warnings" if warning_count else "completed"
    method_counts: dict[str, int] = {}
    for _, output in outputs:
        method = output["summary"]["method"]
        method_counts[method] = method_counts.get(method, 0) + 1
    notes = "Daily horizon forecast completed. " + ", ".join(
        f"{forecasting.METHOD_LABELS.get(method, method)}: {count}" for method, count in sorted(method_counts.items())
    )
    if warning_count:
        notes += f". {warning_count} product(s) require review."

    with get_transaction_cursor() as cur:
        cur.execute("SELECT status FROM forecast_runs WHERE forecast_run_id = %s FOR UPDATE;", (run_id,))
        current = cur.fetchone()
        if current and current["status"] == "cancelling":
            cur.execute(
                """
                UPDATE forecast_runs SET status = 'cancelled', completed_at = now(), locked_at = NULL,
                    notes = 'Cancelled by an owner after product processing.'
                WHERE forecast_run_id = %s;
                """,
                (run_id,),
            )
            return {"forecast_run_id": run_id, "status": "cancelled", "warnings": 0}
        cur.execute("DELETE FROM forecast_results WHERE forecast_run_id = %s;", (run_id,))
        cur.execute("DELETE FROM forecast_product_summaries WHERE forecast_run_id = %s;", (run_id,))
        for product, output in outputs:
            summary = output["summary"]
            cur.execute(
                """
                INSERT INTO forecast_product_summaries (
                    forecast_run_id, product_id, method, diagnostic_status, diagnostic_message,
                    history_days, nonzero_days, score_metric, backtest_score, candidate_scores,
                    forecast_total, confidence_lower_total, confidence_upper_total, usable_stock, production_gap
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s);
                """,
                (
                    run_id, product["product_id"], summary["method"], summary["diagnostic_status"],
                    summary["diagnostic_message"], summary["history_days"], summary["nonzero_days"],
                    summary["score_metric"], summary["backtest_score"], json.dumps(summary["candidate_scores"]),
                    summary["forecast_total"], summary["confidence_lower_total"], summary["confidence_upper_total"],
                    summary["usable_stock"], summary["production_gap"],
                ),
            )
            for daily in output["daily"]:
                cur.execute(
                    """
                    INSERT INTO forecast_results (
                        forecast_run_id, product_id, forecast_date, predicted_quantity,
                        confidence_lower, confidence_upper
                    ) VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (
                        run_id, product["product_id"], daily["forecast_date"], daily["predicted_quantity"],
                        daily["confidence_lower"], daily["confidence_upper"],
                    ),
                )
        cur.execute(
            """
            UPDATE forecast_runs
            SET status = %s, completed_at = now(), locked_at = NULL, last_error = NULL,
                processed_products = total_products, notes = %s
            WHERE forecast_run_id = %s;
            """,
            (final_status, notes, run_id),
        )
        _add_log_cursor(
            cur, actor_account_id=run.get("run_by_account_id"), action="forecast_completed",
            entity_type="forecast_runs", entity_id=run_id,
        )
        _create_notification_cursor(
            cur, recipient_account_id=run.get("run_by_account_id"), category="forecast",
            severity="warning" if warning_count else "info",
            title="Forecast completed with review items" if warning_count else "Forecast completed",
            message=f"{run['model_name']} generated {horizon} daily demand predictions for {len(products)} product(s).",
            target_url=f"/portal/owner/forecasts?run_id={run_id}", source_type="forecast_runs", source_id=run_id,
            dedupe_key=f"forecast-run-completed-{run_id}",
        )
    return {"forecast_run_id": run_id, "status": final_status, "warnings": warning_count}


def fail_forecast_run(run: dict, exc: Exception) -> str:
    run_id = int(run["forecast_run_id"])
    attempt = int(run.get("attempt_count", 1))
    error = f"{exc.__class__.__name__}: {exc}"[:1000]
    with get_transaction_cursor() as cur:
        if attempt < 3:
            delay = 2 ** max(0, attempt - 1)
            cur.execute(
                """
                UPDATE forecast_runs
                SET status = 'queued', next_attempt_at = now() + make_interval(mins => %s),
                    locked_at = NULL, last_error = %s, processed_products = 0
                WHERE forecast_run_id = %s;
                """,
                (delay, error, run_id),
            )
            return "queued"
        cur.execute(
            """
            UPDATE forecast_runs
            SET status = 'failed', completed_at = now(), locked_at = NULL, last_error = %s
            WHERE forecast_run_id = %s;
            """,
            (error, run_id),
        )
        _add_log_cursor(
            cur, actor_account_id=run.get("run_by_account_id"), action="forecast_failed",
            entity_type="forecast_runs", entity_id=run_id,
        )
        _create_notification_cursor(
            cur, recipient_account_id=run.get("run_by_account_id"), category="forecast", severity="warning",
            title="Forecast failed", message="The forecast could not be completed after three attempts.",
            target_url=f"/portal/owner/forecasts?run_id={run_id}", source_type="forecast_runs", source_id=run_id,
            dedupe_key=f"forecast-run-failed-{run_id}",
        )
        return "failed"


def forecast_run_status(forecast_run_id: int, owner_account_id: int) -> dict | None:
    row = fetch_one(
        """
        SELECT forecast_run_id, status, processed_products, total_products, completed_at,
               CASE
                   WHEN status = 'queued' THEN 'Forecast is queued.'
                   WHEN status = 'running' THEN 'Forecast is running.'
                   WHEN status = 'completed' THEN 'Forecast completed.'
                   WHEN status = 'completed_with_warnings' THEN 'Forecast completed with products requiring review.'
                   WHEN status = 'cancelling' THEN 'Forecast cancellation is pending.'
                   WHEN status = 'cancelled' THEN 'Forecast was cancelled.'
                   ELSE 'Forecast failed.'
               END AS message
        FROM forecast_runs
        WHERE forecast_run_id = %s
          AND EXISTS (
              SELECT 1 FROM accounts
              WHERE account_id = %s AND account_type = 'owner' AND is_active = true
          );
        """,
        (forecast_run_id, owner_account_id),
    )
    return clean_row(row) if row else None


def get_forecast_run(forecast_run_id: int) -> dict | None:
    row = fetch_one(
        """
        SELECT f.forecast_run_id, f.run_by_account_id, a.name AS requested_by,
               f.model_name, f.input_period_start, f.input_period_end,
               f.forecast_horizon_days, f.status, f.queued_at, f.started_at,
               f.completed_at, f.total_products, f.processed_products, f.last_error, f.notes
        FROM forecast_runs f
        LEFT JOIN accounts a ON a.account_id = f.run_by_account_id
        WHERE f.forecast_run_id = %s;
        """,
        (forecast_run_id,),
    )
    return clean_row(row) if row else None


def list_forecast_runs(limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("Forecast run limit must be between 1 and 100.")
    return clean_row(fetch_all(
        """
        SELECT f.forecast_run_id, f.model_name, f.forecast_horizon_days, f.status,
               f.queued_at, f.completed_at, a.name AS requested_by
        FROM forecast_runs f
        LEFT JOIN accounts a ON a.account_id = f.run_by_account_id
        ORDER BY f.forecast_run_id DESC
        LIMIT %s;
        """,
        (limit,),
    ))


def retry_forecast_run(forecast_run_id: int, actor_account_id: int, reason: str) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('meattrack-forecast-queue'));")
        cur.execute("SELECT * FROM forecast_runs WHERE forecast_run_id = %s FOR UPDATE;", (forecast_run_id,))
        original = cur.fetchone()
        if not original or original["status"] != "failed":
            raise ValueError("Only failed forecast runs can be retried.")
        cur.execute(
            "SELECT forecast_run_id FROM forecast_runs WHERE retry_of_forecast_run_id = %s LIMIT 1;",
            (forecast_run_id,),
        )
        if cur.fetchone():
            raise ValueError("A retry has already been created for this forecast run.")
        cur.execute(
            """
            INSERT INTO forecast_runs (
                run_by_account_id, model_name, input_period_start, input_period_end,
                forecast_horizon_days, status, queued_at, next_attempt_at,
                configuration, retry_of_forecast_run_id, notes
            ) VALUES (%s, %s, %s, %s, %s, 'queued', now(), now(), %s::jsonb, %s, %s)
            RETURNING forecast_run_id, status;
            """,
            (
                actor_account_id, original["model_name"], original["input_period_start"],
                original["input_period_end"], original["forecast_horizon_days"],
                json.dumps(original["configuration"]), forecast_run_id,
                f"Owner retry of run #{forecast_run_id}: {clean_reason}",
            ),
        )
        retry = dict(cur.fetchone())
        from .superuser import _audit_cursor

        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="retried_forecast_run",
            entity_type="forecast_runs",
            entity_id=retry["forecast_run_id"],
            reason=clean_reason,
            before_state={"forecast_run_id": forecast_run_id, "status": original["status"]},
            after_state={"retry_forecast_run_id": retry["forecast_run_id"], "status": retry["status"]},
        )
    return clean_row(retry)


def cancel_forecast_run(forecast_run_id: int, actor_account_id: int, reason: str) -> dict:
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5 or len(clean_reason) > 500:
        raise ValueError("Enter a reason using 5 to 500 characters.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM forecast_runs WHERE forecast_run_id = %s FOR UPDATE;", (forecast_run_id,))
        run = cur.fetchone()
        if not run or run["status"] not in {"queued", "running"}:
            raise ValueError("Only queued or running forecasts can be cancelled.")
        new_status = "cancelled" if run["status"] == "queued" else "cancelling"
        cur.execute(
            """
            UPDATE forecast_runs
            SET status = %s, cancel_requested_at = now(),
                completed_at = CASE WHEN %s = 'cancelled' THEN now() ELSE completed_at END,
                locked_at = CASE WHEN %s = 'cancelled' THEN NULL ELSE locked_at END
            WHERE forecast_run_id = %s RETURNING forecast_run_id, status;
            """,
            (new_status, new_status, new_status, forecast_run_id),
        )
        updated = dict(cur.fetchone())
        from .superuser import _audit_cursor

        _audit_cursor(
            cur,
            actor_account_id=actor_account_id,
            actor_role="owner",
            action="cancelled_forecast_run",
            entity_type="forecast_runs",
            entity_id=forecast_run_id,
            reason=clean_reason,
            before_state={"status": run["status"]},
            after_state={"status": new_status},
        )
    return clean_row(updated)


__all__ = ['list_forecasts', 'latest_forecast_run', 'count_forecasts', 'product_sales_history', 'forecast_business_events', 'queue_forecast', 'recover_stale_forecast_runs', 'claim_forecast_run', '_forecast_products', 'process_forecast_run', 'fail_forecast_run', 'forecast_run_status', 'get_forecast_run', 'list_forecast_runs', 'retry_forecast_run', 'cancel_forecast_run']
