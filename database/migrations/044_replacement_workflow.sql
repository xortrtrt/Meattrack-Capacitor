-- Existing returns keep version 1. Only the new replacement submission path opts into version 2.
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS replacement_workflow_version smallint NOT NULL DEFAULT 1 CHECK (replacement_workflow_version IN (1,2));
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS replacement_prepared_at timestamptz;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS replacement_prepared_by_account_id bigint REFERENCES accounts;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS replacement_delivered_at timestamptz;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS replacement_delivered_by_account_id bigint REFERENCES accounts;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS replacement_payment_details jsonb;
ALTER TABLE replacement_quote_lines ADD COLUMN IF NOT EXISTS pricing_components jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS return_conversation_messages (
    message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    sender_account_id bigint REFERENCES accounts ON DELETE RESTRICT,
    client_message_id uuid NOT NULL,
    content text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 1000),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(return_id,sender_account_id,client_message_id)
);
CREATE INDEX IF NOT EXISTS ix_return_conversation_messages ON return_conversation_messages(return_id,message_id);
CREATE TABLE IF NOT EXISTS return_conversation_reads (
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    account_id bigint NOT NULL REFERENCES accounts ON DELETE RESTRICT,
    last_message_id bigint NOT NULL DEFAULT 0 CHECK (last_message_id>=0),
    PRIMARY KEY(return_id,account_id)
);
DROP TRIGGER IF EXISTS immutable_return_conversation_messages ON return_conversation_messages;
CREATE TRIGGER immutable_return_conversation_messages BEFORE UPDATE OR DELETE OR TRUNCATE ON return_conversation_messages
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
