CREATE OR REPLACE FUNCTION require_reseller_order_payment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller'
       AND NEW.status IN ('pending', 'approved', 'fulfilled')
       AND (
            NEW.payment_transaction_number IS NULL
            OR NEW.payment_transaction_key IS NULL
            OR NEW.payment_paid_at IS NULL
            OR NEW.payment_amount IS NULL
            OR NEW.payment_confirmed_at IS NULL
            OR NOT EXISTS (
                SELECT 1
                FROM order_payment_proofs
                WHERE order_id = NEW.order_id
            )
       )
    THEN
        RAISE EXCEPTION 'Reseller orders require confirmed payment details and proof before commit.';
    END IF;
    RETURN NEW;
END $$;
