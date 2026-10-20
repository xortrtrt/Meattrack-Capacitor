BEGIN;

ALTER TABLE activity_logs
    ADD COLUMN IF NOT EXISTS actor_role text,
    ADD COLUMN IF NOT EXISTS reason text,
    ADD COLUMN IF NOT EXISTS before_state jsonb,
    ADD COLUMN IF NOT EXISTS after_state jsonb,
    ADD COLUMN IF NOT EXISTS outcome text NOT NULL DEFAULT 'success',
    ADD COLUMN IF NOT EXISTS request_id text,
    ADD COLUMN IF NOT EXISTS source_key text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'activity_logs_outcome_check'
          AND conrelid = 'activity_logs'::regclass
    ) THEN
        ALTER TABLE activity_logs
            ADD CONSTRAINT activity_logs_outcome_check CHECK (outcome IN ('success', 'failure'));
    END IF;
END $$;

ALTER TABLE account_activation_tokens
    ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'activation';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'account_activation_tokens_purpose_check'
          AND conrelid = 'account_activation_tokens'::regclass
    ) THEN
        ALTER TABLE account_activation_tokens
            ADD CONSTRAINT account_activation_tokens_purpose_check
            CHECK (purpose IN ('activation', 'password_reset'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS owner_step_up_challenges (
    owner_step_up_challenge_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    otp_hash text NOT NULL CHECK (btrim(otp_hash) <> ''),
    action_scope text NOT NULL DEFAULT 'owner.sensitive' CHECK (btrim(action_scope) <> ''),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_owner_step_up_pending
    ON owner_step_up_challenges (account_id, created_at DESC)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS business_settings (
    setting_key text PRIMARY KEY CHECK (btrim(setting_key) <> ''),
    setting_value jsonb NOT NULL,
    updated_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO business_settings (setting_key, setting_value) VALUES
    ('order.payment_window_minutes', '15'::jsonb),
    ('inventory.near_expiry_days', '7'::jsonb),
    ('inventory.critical_expiry_days', '2'::jsonb),
    ('forecast.default_horizon_days', '7'::jsonb),
    ('notifications.owner_critical_alerts', 'true'::jsonb),
    ('business.contact_email', '""'::jsonb),
    ('business.contact_phone', '""'::jsonb),
    ('business.contact_address', '""'::jsonb),
    ('business.hours', '""'::jsonb)
ON CONFLICT (setting_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS system_status_reports (
    component text PRIMARY KEY CHECK (btrim(component) <> ''),
    status text NOT NULL CHECK (status IN ('ok', 'warning', 'error', 'unknown')),
    detail text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    checked_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE forecast_runs
    DROP CONSTRAINT IF EXISTS forecast_runs_status_check,
    ADD CONSTRAINT forecast_runs_status_check
        CHECK (status IN ('queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'cancelling', 'cancelled')),
    ADD COLUMN IF NOT EXISTS retry_of_forecast_run_id bigint REFERENCES forecast_runs(forecast_run_id) ON UPDATE CASCADE ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz;

ALTER TABLE inventory_movements
    DROP CONSTRAINT IF EXISTS inventory_movements_movement_type_check,
    ADD CONSTRAINT inventory_movements_movement_type_check CHECK (movement_type IN (
        'opening_balance', 'raw_receipt', 'production_consumption',
        'production_output', 'direct_finished_receipt', 'sale_fulfillment',
        'owner_correction'
    ));

CREATE INDEX IF NOT EXISTS ix_accounts_type_active ON accounts (account_type, is_active, account_id);
CREATE INDEX IF NOT EXISTS ix_inquiries_owner_queue ON inquiries (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_orders_owner_queue ON orders (status, order_date DESC);
CREATE INDEX IF NOT EXISTS ix_activity_logs_governance ON activity_logs (outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_activity_logs_actor_role ON activity_logs (actor_role, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_activity_logs_entity_action ON activity_logs (entity_type, action, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_resellers_owner_assignment ON resellers (team_leader_account_id, reseller_status);
CREATE INDEX IF NOT EXISTS ix_chat_conversations_owner_status ON chat_conversations (status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_runs_single_retry
    ON forecast_runs (retry_of_forecast_run_id) WHERE retry_of_forecast_run_id IS NOT NULL;

COMMIT;
