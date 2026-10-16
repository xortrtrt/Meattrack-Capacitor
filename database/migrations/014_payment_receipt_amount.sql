ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_amount numeric(12,2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'orders_payment_amount_check'
          AND conrelid = 'orders'::regclass
    ) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_payment_amount_check
            CHECK (payment_amount IS NULL OR payment_amount > 0);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION require_reseller_order_payment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND (
        NEW.payment_transaction_number IS NULL
        OR NEW.payment_transaction_key IS NULL
        OR NEW.payment_paid_at IS NULL
        OR NEW.payment_amount IS NULL
        OR NEW.payment_confirmed_at IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM order_payment_proofs WHERE order_id = NEW.order_id
        )
    ) THEN
        RAISE EXCEPTION 'Reseller orders require confirmed payment details and proof before commit.';
    END IF;
    RETURN NEW;
END $$;
