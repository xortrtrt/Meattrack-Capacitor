ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_payment_amount_check;
ALTER TABLE orders ADD CONSTRAINT orders_payment_amount_check
    CHECK (
        payment_amount IS NULL OR (
            payment_amount > 0
            AND payment_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
        )
    );
