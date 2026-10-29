-- Combined reports retain the exact document approved by their author.
CREATE TABLE IF NOT EXISTS sales_report_activity_snapshots (
    sales_report_id bigint PRIMARY KEY REFERENCES sales_reports ON DELETE RESTRICT,
    author_account_id bigint NOT NULL REFERENCES accounts ON DELETE RESTRICT,
    submission_key uuid NOT NULL UNIQUE,
    schema_version smallint NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    captured_at timestamptz NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
);
DROP TRIGGER IF EXISTS immutable_activity_report_snapshots ON sales_report_activity_snapshots;
CREATE TRIGGER immutable_activity_report_snapshots
BEFORE UPDATE OR DELETE OR TRUNCATE ON sales_report_activity_snapshots
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
CREATE INDEX IF NOT EXISTS ix_activity_report_actor
ON activity_logs(account_id, entity_type, entity_id, action);
CREATE INDEX IF NOT EXISTS ix_return_report_actor
ON product_return_events(actor_account_id, return_id, action);
