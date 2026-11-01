-- Acknowledgement does not change fulfillment, stock, or the return deadline.
CREATE TABLE IF NOT EXISTS reseller_order_receipts (
    order_id bigint PRIMARY KEY REFERENCES orders(order_id) ON DELETE RESTRICT,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION validate_reseller_order_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM orders o JOIN accounts a ON a.account_id=NEW.account_id
        WHERE o.order_id=NEW.order_id AND o.order_type='reseller' AND o.status='fulfilled'
          AND o.created_by_account_id=NEW.account_id AND a.account_type='reseller' AND a.is_active
    ) THEN
        RAISE EXCEPTION 'Only the reseller can acknowledge their completed order.';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS validate_reseller_order_receipt ON reseller_order_receipts;
CREATE TRIGGER validate_reseller_order_receipt BEFORE INSERT ON reseller_order_receipts
FOR EACH ROW EXECUTE FUNCTION validate_reseller_order_receipt();
DROP TRIGGER IF EXISTS immutable_reseller_order_receipts ON reseller_order_receipts;
CREATE TRIGGER immutable_reseller_order_receipts BEFORE UPDATE OR DELETE OR TRUNCATE ON reseller_order_receipts
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
