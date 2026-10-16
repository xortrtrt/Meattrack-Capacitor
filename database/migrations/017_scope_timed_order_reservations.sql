CREATE OR REPLACE FUNCTION require_timed_order_reservations()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller'
       AND NEW.payment_due_at IS NOT NULL
       AND NEW.status IN ('to_pay', 'pending', 'approved')
       AND (
            NOT EXISTS (SELECT 1 FROM order_items WHERE order_id = NEW.order_id)
            OR EXISTS (
                SELECT 1
                FROM (
                    SELECT product_id, SUM(quantity) AS quantity
                    FROM order_items
                    WHERE order_id = NEW.order_id
                    GROUP BY product_id
                ) items
                FULL JOIN (
                    SELECT product_id, quantity
                    FROM order_stock_reservations
                    WHERE order_id = NEW.order_id
                ) reservations
                  ON reservations.product_id = items.product_id
                WHERE COALESCE(items.quantity, 0) <> COALESCE(reservations.quantity, 0)
                   OR items.product_id IS NULL
                   OR reservations.product_id IS NULL
            )
       )
    THEN
        RAISE EXCEPTION 'Timed reseller orders require matching stock reservations.';
    END IF;
    RETURN NEW;
END $$;
