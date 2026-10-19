CREATE TABLE IF NOT EXISTS order_refund_messages (
    order_refund_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_refund_id bigint NOT NULL
        REFERENCES order_refunds(order_refund_id) ON UPDATE CASCADE ON DELETE CASCADE,
    sender_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    sender_role text NOT NULL CHECK (sender_role IN ('owner', 'sales-supervisor', 'reseller')),
    message text NOT NULL CHECK (char_length(btrim(message)) BETWEEN 1 AND 1000),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_order_refund_messages_refund_created
    ON order_refund_messages (order_refund_id, created_at, order_refund_message_id);

CREATE OR REPLACE FUNCTION protect_order_refund_message()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Refund conversation messages are append-only.';
END $$;

DROP TRIGGER IF EXISTS trg_protect_order_refund_message ON order_refund_messages;
CREATE TRIGGER trg_protect_order_refund_message
BEFORE UPDATE OR DELETE ON order_refund_messages
FOR EACH ROW EXECUTE FUNCTION protect_order_refund_message();
