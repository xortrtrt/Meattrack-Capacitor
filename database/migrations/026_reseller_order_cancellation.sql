ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS cancellation_reason text,
    ADD COLUMN IF NOT EXISTS cancelled_at timestamptz;

UPDATE orders
SET cancellation_reason = COALESCE(
        NULLIF(btrim(cancellation_reason), ''),
        'Cancelled before cancellation reasons were recorded.'
    ),
    cancelled_at = COALESCE(cancelled_at, order_date)
WHERE status = 'cancelled';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'orders_cancellation_reason_check'
          AND conrelid = 'orders'::regclass
    ) THEN
        ALTER TABLE orders
            ADD CONSTRAINT orders_cancellation_reason_check CHECK (
                (status = 'cancelled' AND char_length(btrim(cancellation_reason)) BETWEEN 5 AND 400)
                OR (status <> 'cancelled' AND cancellation_reason IS NULL AND cancelled_at IS NULL)
            );
    END IF;
END $$;

ALTER TABLE order_system_messages
    DROP CONSTRAINT IF EXISTS order_system_messages_event_type_check;

ALTER TABLE order_system_messages
    ADD CONSTRAINT order_system_messages_event_type_check CHECK (
        event_type IN (
            'approved', 'rejected', 'fulfilled', 'cancelled',
            'refund_pending', 'refund_processing', 'refund_completed', 'refund_failed'
        )
    );
