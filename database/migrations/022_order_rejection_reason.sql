ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS rejection_reason text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'orders_rejection_reason_check'
          AND conrelid = 'orders'::regclass
    ) THEN
        ALTER TABLE orders
            ADD CONSTRAINT orders_rejection_reason_check
            CHECK (
                rejection_reason IS NULL
                OR char_length(btrim(rejection_reason)) BETWEEN 5 AND 400
            );
    END IF;
END $$;
