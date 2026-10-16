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
    IF OLD.status = 'pending' AND NEW.status = 'rejected' THEN RETURN NEW; END IF;
    IF OLD.status = 'approved' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    IF OLD.order_type = 'walk_in' AND OLD.status = 'pending' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid order status transition: % -> %.', OLD.status, NEW.status;
END $$;
