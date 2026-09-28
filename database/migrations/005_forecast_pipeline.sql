-- Durable asynchronous forecasting with daily results and per-product diagnostics.

ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS queued_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0;
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS locked_at timestamptz;
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS last_error text;
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS total_products integer NOT NULL DEFAULT 0;
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS processed_products integer NOT NULL DEFAULT 0;
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS configuration jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE forecast_runs ALTER COLUMN started_at DROP NOT NULL;

UPDATE forecast_runs
SET queued_at = COALESCE(started_at, completed_at, now()),
    total_products = GREATEST(total_products, (
        SELECT COUNT(DISTINCT fr.product_id) FROM forecast_results fr
        WHERE fr.forecast_run_id = forecast_runs.forecast_run_id
    )),
    processed_products = GREATEST(processed_products, (
        SELECT COUNT(DISTINCT fr.product_id) FROM forecast_results fr
        WHERE fr.forecast_run_id = forecast_runs.forecast_run_id
    ));

ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS forecast_runs_status_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_status_check
    CHECK (status IN ('queued', 'running', 'completed', 'completed_with_warnings', 'failed'));
ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS forecast_runs_forecast_horizon_days_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_forecast_horizon_days_check
    CHECK (forecast_horizon_days BETWEEN 1 AND 365);
ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS forecast_runs_attempt_count_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_attempt_count_check CHECK (attempt_count >= 0);
ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS forecast_runs_total_products_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_total_products_check CHECK (total_products >= 0);
ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS forecast_runs_processed_products_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_processed_products_check
    CHECK (processed_products >= 0 AND processed_products <= total_products);

CREATE TABLE IF NOT EXISTS forecast_product_summaries (
    forecast_product_summary_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    forecast_run_id bigint NOT NULL REFERENCES forecast_runs(forecast_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    method text NOT NULL CHECK (method IN ('prophet', 'tsb', 'seasonal_naive_7d', 'moving_average_28d', 'none', 'legacy')),
    diagnostic_status text NOT NULL CHECK (diagnostic_status IN ('ok', 'fallback', 'insufficient_history', 'failed', 'legacy')),
    diagnostic_message text,
    history_days integer NOT NULL DEFAULT 0 CHECK (history_days >= 0),
    nonzero_days integer NOT NULL DEFAULT 0 CHECK (nonzero_days >= 0),
    score_metric text,
    backtest_score numeric(16,6),
    candidate_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    forecast_total numeric(14,3) CHECK (forecast_total IS NULL OR forecast_total >= 0),
    confidence_lower_total numeric(14,3) CHECK (confidence_lower_total IS NULL OR confidence_lower_total >= 0),
    confidence_upper_total numeric(14,3) CHECK (confidence_upper_total IS NULL OR confidence_upper_total >= 0),
    usable_stock numeric(14,3) CHECK (usable_stock IS NULL OR usable_stock >= 0),
    production_gap numeric(14,3),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (forecast_run_id, product_id),
    CHECK (confidence_lower_total IS NULL OR confidence_upper_total IS NULL OR confidence_upper_total >= confidence_lower_total)
);

INSERT INTO forecast_product_summaries (
    forecast_run_id, product_id, method, diagnostic_status, diagnostic_message,
    forecast_total, confidence_lower_total, confidence_upper_total, candidate_scores
)
SELECT fr.forecast_run_id, fr.product_id, 'legacy', 'legacy',
       'Migrated from the previous single-point forecast format.',
       SUM(fr.predicted_quantity),
       CASE WHEN COUNT(fr.confidence_lower) = COUNT(*) THEN SUM(fr.confidence_lower) END,
       CASE WHEN COUNT(fr.confidence_upper) = COUNT(*) THEN SUM(fr.confidence_upper) END,
       '{}'::jsonb
FROM forecast_results fr
GROUP BY fr.forecast_run_id, fr.product_id
ON CONFLICT (forecast_run_id, product_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS ix_forecast_runs_due ON forecast_runs (next_attempt_at, forecast_run_id)
    WHERE status = 'queued' AND attempt_count < 3;
CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_runs_one_active ON forecast_runs ((1))
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS ix_forecast_product_priority
    ON forecast_product_summaries (forecast_run_id, production_gap DESC NULLS LAST);
