-- Run with application and worker writes paused. Resolve active underpayments first.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM orders WHERE order_type='reseller' AND status IN ('pending','approved')
               AND (payment_amount IS NULL OR payment_amount < total_amount)) THEN
        RAISE EXCEPTION 'Active reseller orders have insufficient payment. Reconcile them before migration 040.';
    END IF;
END $$;

ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS payment_review_required boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS order_reservation_events (
    reservation_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders ON DELETE RESTRICT,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON DELETE RESTRICT,
    quantity_delta numeric(12,3) NOT NULL CHECK(quantity_delta<>0),
    action text NOT NULL CHECK(action IN ('reserved','released','fulfilled','migration_snapshot','unpaid_hold_released')),
    actor_account_id bigint REFERENCES accounts ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_order_reservation_events_product ON order_reservation_events(product_id,created_at,reservation_event_id);
CREATE INDEX IF NOT EXISTS ix_order_reservation_events_order ON order_reservation_events(order_id,reservation_event_id);
DROP TRIGGER IF EXISTS immutable_order_reservation_events ON order_reservation_events;
CREATE TRIGGER immutable_order_reservation_events BEFORE UPDATE OR DELETE OR TRUNCATE ON order_reservation_events
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();

DROP TRIGGER IF EXISTS record_order_reservations ON order_stock_reservations;

-- Snapshots are explicitly migration-time facts, not invented historical timestamps.
INSERT INTO order_reservation_events(order_id,product_id,quantity_delta,action)
SELECT r.order_id,r.product_id,r.quantity,'migration_snapshot' FROM order_stock_reservations r
WHERE NOT EXISTS(SELECT 1 FROM order_reservation_events e WHERE e.order_id=r.order_id AND e.product_id=r.product_id);
INSERT INTO order_reservation_events(order_id,product_id,quantity_delta,action)
SELECT r.order_id,r.product_id,-r.quantity,'unpaid_hold_released'
FROM order_stock_reservations r JOIN orders o USING(order_id) WHERE o.status='to_pay';
DELETE FROM order_stock_reservations r USING orders o WHERE r.order_id=o.order_id AND o.status='to_pay';

CREATE OR REPLACE FUNCTION record_order_reservation_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE oid bigint; pid bigint; qty numeric; state text; actor bigint;
BEGIN
    IF TG_OP='INSERT' THEN oid:=NEW.order_id; pid:=NEW.product_id; qty:=NEW.quantity;
    ELSE oid:=OLD.order_id; pid:=OLD.product_id; qty:=-OLD.quantity; END IF;
    SELECT status INTO state FROM orders WHERE order_id=oid;
    actor:=NULLIF(current_setting('meattrack.actor_account_id',true),'')::bigint;
    INSERT INTO order_reservation_events(order_id,product_id,quantity_delta,action,actor_account_id)
    VALUES(oid,pid,qty,CASE WHEN TG_OP='INSERT' THEN 'reserved'
        WHEN current_setting('meattrack.reservation_reason',true)='fulfilled' OR state='fulfilled' THEN 'fulfilled'
        ELSE 'released' END,actor);
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS record_order_reservations ON order_stock_reservations;
CREATE TRIGGER record_order_reservations AFTER INSERT OR DELETE ON order_stock_reservations
FOR EACH ROW EXECUTE FUNCTION record_order_reservation_event();
DROP TRIGGER IF EXISTS immutable_order_reservations ON order_stock_reservations;
CREATE TRIGGER immutable_order_reservations BEFORE UPDATE ON order_stock_reservations
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();

CREATE OR REPLACE FUNCTION guard_paid_order_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o orders%ROWTYPE; eligible numeric; held numeric;
BEGIN
    SELECT * INTO o FROM orders WHERE order_id=NEW.order_id FOR UPDATE;
    IF o.order_type<>'reseller' OR o.status NOT IN ('pending','approved')
       OR o.payment_amount IS NULL OR o.payment_amount<o.total_amount THEN
        RAISE EXCEPTION 'Only sufficiently paid active orders may reserve stock.';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('inventory-product:'||NEW.product_id,0));
    SELECT COALESCE(SUM(quantity_available),0) INTO eligible FROM inventory_batches
    WHERE item_id=NEW.product_id AND quality_status='approved'
      AND expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date;
    SELECT COALESCE(SUM(quantity),0) INTO held FROM all_stock_reservations WHERE product_id=NEW.product_id;
    IF held+NEW.quantity>eligible THEN RAISE EXCEPTION 'Insufficient available stock for reservation.'; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS guard_paid_order_reservation ON order_stock_reservations;
CREATE TRIGGER guard_paid_order_reservation BEFORE INSERT ON order_stock_reservations
FOR EACH ROW EXECUTE FUNCTION guard_paid_order_reservation();

CREATE OR REPLACE FUNCTION require_reseller_order_payment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o orders%ROWTYPE;
BEGIN
    -- Deferred triggers must inspect the final row, not an earlier to_pay snapshot.
    SELECT * INTO o FROM orders WHERE order_id=NEW.order_id;
    IF o.order_type='reseller' AND o.status IN ('pending','approved','fulfilled') AND (
        o.payment_transaction_number IS NULL OR o.payment_transaction_key IS NULL
        OR o.payment_paid_at IS NULL OR o.payment_amount IS NULL OR o.payment_amount<o.total_amount
        OR o.payment_confirmed_at IS NULL
        OR NOT EXISTS(SELECT 1 FROM order_payment_proofs WHERE order_id=o.order_id)) THEN
        RAISE EXCEPTION 'Reseller orders require sufficient confirmed payment details and proof before commit.';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION require_timed_order_reservations() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE oid bigint; o orders%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN oid:=OLD.order_id; ELSE oid:=NEW.order_id; END IF;
    SELECT * INTO o FROM orders WHERE order_id=oid;
    IF o.order_type<>'reseller' THEN RETURN NULL; END IF;
    IF o.status NOT IN ('pending','approved') AND EXISTS(
        SELECT 1 FROM order_stock_reservations WHERE order_id=oid) THEN
        RAISE EXCEPTION 'Only sufficiently paid active orders may reserve stock.';
    END IF;
    IF o.status IN ('pending','approved') AND (o.payment_amount IS NULL OR o.payment_amount<o.total_amount) THEN
        RAISE EXCEPTION 'Insufficient payment cannot reserve stock.';
    END IF;
    IF o.payment_due_at IS NOT NULL AND o.status IN ('pending','approved') AND (
        NOT EXISTS(SELECT 1 FROM order_items WHERE order_id=oid) OR EXISTS(
            SELECT 1 FROM (SELECT product_id,SUM(quantity) quantity FROM order_items WHERE order_id=oid GROUP BY product_id) i
            FULL JOIN (SELECT product_id,quantity FROM order_stock_reservations WHERE order_id=oid) r USING(product_id)
            WHERE i.quantity IS DISTINCT FROM r.quantity)) THEN
        RAISE EXCEPTION 'Paid reseller orders require matching stock reservations.';
    END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS validate_order_reservation_change ON order_stock_reservations;
CREATE CONSTRAINT TRIGGER validate_order_reservation_change AFTER INSERT OR DELETE ON order_stock_reservations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_timed_order_reservations();

-- A stock-shortage payment is rejected atomically instead of passing through pending.
CREATE OR REPLACE FUNCTION enforce_order_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status=OLD.status THEN RETURN NEW; END IF;
    IF OLD.status IN ('rejected','fulfilled','cancelled','expired') THEN
        RAISE EXCEPTION 'Order status % is terminal.',OLD.status;
    END IF;
    IF OLD.order_type='reseller' AND OLD.status IN ('to_pay','pending') AND NEW.status='cancelled' THEN
        IF OLD.payment_transaction_number IS NOT NULL OR OLD.payment_amount IS NOT NULL
           OR EXISTS(SELECT 1 FROM order_payment_proofs WHERE order_id=OLD.order_id) THEN
            RAISE EXCEPTION 'Orders with a submitted payment cannot be cancelled.';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status='to_pay' AND NEW.status IN ('pending','expired') THEN RETURN NEW; END IF;
    IF OLD.status='to_pay' AND NEW.status='rejected' AND NEW.payment_amount>=NEW.total_amount
       AND NEW.rejection_payment_received IS NULL AND NEW.rejection_reason='Stock unavailable after payment submission.' THEN RETURN NEW; END IF;
    IF OLD.status='pending' AND NEW.status IN ('approved','rejected') THEN RETURN NEW; END IF;
    IF OLD.status='approved' AND NEW.status='fulfilled' THEN RETURN NEW; END IF;
    IF OLD.order_type='walk_in' AND OLD.status='pending' AND NEW.status='fulfilled' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid order status transition: % -> %.',OLD.status,NEW.status;
END $$;
