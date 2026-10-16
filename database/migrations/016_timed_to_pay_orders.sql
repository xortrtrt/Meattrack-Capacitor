ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN ('to_pay', 'pending', 'approved', 'rejected', 'fulfilled', 'cancelled', 'expired'));

ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_due_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS expired_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'orders_to_pay_deadline_check'
          AND conrelid = 'orders'::regclass
    ) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_to_pay_deadline_check CHECK (
            status <> 'to_pay' OR (
                order_type = 'reseller'
                AND payment_due_at IS NOT NULL
                AND payment_due_at > order_date
                AND payment_transaction_number IS NULL
                AND payment_transaction_key IS NULL
                AND payment_paid_at IS NULL
                AND payment_amount IS NULL
                AND payment_ocr_confidence IS NULL
                AND payment_extraction_method IS NULL
                AND payment_confirmed_at IS NULL
                AND payment_ocr_engine IS NULL
            )
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS order_stock_reservations (
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    reserved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (order_id, product_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_reseller_to_pay
    ON orders (reseller_id)
    WHERE order_type = 'reseller' AND status = 'to_pay';
CREATE INDEX IF NOT EXISTS ix_orders_to_pay_due ON orders (payment_due_at, order_id)
    WHERE status = 'to_pay';
CREATE INDEX IF NOT EXISTS ix_order_stock_reservations_product
    ON order_stock_reservations (product_id, order_id);

CREATE OR REPLACE FUNCTION enforce_order_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status IN ('rejected', 'fulfilled', 'cancelled', 'expired') THEN
        RAISE EXCEPTION 'Order status % is terminal.', OLD.status;
    END IF;
    IF OLD.status = 'to_pay' AND NEW.status IN ('pending', 'expired') THEN RETURN NEW; END IF;
    IF OLD.status = 'pending' AND NEW.status = 'approved' THEN
        IF OLD.order_type = 'reseller' AND NOT EXISTS (
            SELECT 1 FROM order_payment_proofs WHERE order_id = OLD.order_id
        ) THEN
            RAISE EXCEPTION 'Proof of payment is required before approval.';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'approved' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    IF OLD.status IN ('pending', 'approved') AND NEW.status = 'rejected' THEN RETURN NEW; END IF;
    IF OLD.order_type = 'walk_in' AND OLD.status = 'pending' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid order status transition: % -> %.', OLD.status, NEW.status;
END $$;

CREATE OR REPLACE FUNCTION enforce_payment_proof_limit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE order_status text; proof_count integer;
BEGIN
    IF current_setting('meattrack.maintenance_import', true) = 'on' THEN RETURN NEW; END IF;
    SELECT status INTO order_status FROM orders WHERE order_id = NEW.order_id FOR UPDATE;
    IF order_status NOT IN ('to_pay', 'pending') THEN
        RAISE EXCEPTION 'Payment proof uploads are allowed only for orders awaiting payment or review.';
    END IF;
    SELECT count(*) INTO proof_count FROM order_payment_proofs WHERE order_id = NEW.order_id;
    IF proof_count >= 3 THEN RAISE EXCEPTION 'An order can have at most three payment proofs.'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION require_reseller_order_payment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND NEW.status NOT IN ('to_pay', 'expired') AND (
        NEW.payment_transaction_number IS NULL
        OR NEW.payment_transaction_key IS NULL
        OR NEW.payment_paid_at IS NULL
        OR NEW.payment_amount IS NULL
        OR NEW.payment_confirmed_at IS NULL
        OR NOT EXISTS (SELECT 1 FROM order_payment_proofs WHERE order_id = NEW.order_id)
    ) THEN
        RAISE EXCEPTION 'Reseller orders require confirmed payment details and proof before commit.';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_require_reseller_order_payment ON orders;
CREATE CONSTRAINT TRIGGER trg_require_reseller_order_payment
AFTER INSERT OR UPDATE ON orders DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_reseller_order_payment();

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
                FROM order_items WHERE order_id = NEW.order_id GROUP BY product_id
            ) items
            FULL JOIN order_stock_reservations reservations
              ON reservations.order_id = NEW.order_id
             AND reservations.product_id = items.product_id
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
DROP TRIGGER IF EXISTS trg_require_timed_order_reservations ON orders;
CREATE CONSTRAINT TRIGGER trg_require_timed_order_reservations
AFTER INSERT OR UPDATE OF status ON orders DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_timed_order_reservations();
