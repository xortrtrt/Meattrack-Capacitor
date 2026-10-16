ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_transaction_number text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_transaction_key text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_paid_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_ocr_confidence numeric(5,4);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_extraction_method text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_confirmed_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_ocr_engine text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_payment_transaction_number_check' AND conrelid = 'orders'::regclass) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_payment_transaction_number_check
            CHECK (payment_transaction_number IS NULL OR btrim(payment_transaction_number) <> '');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_payment_transaction_key_check' AND conrelid = 'orders'::regclass) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_payment_transaction_key_check
            CHECK (payment_transaction_key IS NULL OR payment_transaction_key ~ '^[A-Z0-9]{6,64}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_payment_transaction_normalized_check' AND conrelid = 'orders'::regclass) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_payment_transaction_normalized_check
            CHECK (
                payment_transaction_key IS NULL OR (
                    payment_transaction_number IS NOT NULL
                    AND payment_transaction_key = upper(regexp_replace(payment_transaction_number, '[^A-Za-z0-9]', '', 'g'))
                )
            );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_payment_ocr_confidence_check' AND conrelid = 'orders'::regclass) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_payment_ocr_confidence_check
            CHECK (payment_ocr_confidence IS NULL OR payment_ocr_confidence BETWEEN 0 AND 1);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_payment_extraction_method_check' AND conrelid = 'orders'::regclass) THEN
        ALTER TABLE orders ADD CONSTRAINT orders_payment_extraction_method_check
            CHECK (payment_extraction_method IS NULL OR payment_extraction_method IN ('ocr_confirmed', 'manual_corrected'));
    END IF;
END $$;

CREATE OR REPLACE FUNCTION require_reseller_order_payment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND (
        NEW.payment_transaction_number IS NULL
        OR NEW.payment_transaction_key IS NULL
        OR NEW.payment_paid_at IS NULL
        OR NEW.payment_confirmed_at IS NULL
        OR NOT EXISTS (SELECT 1 FROM order_payment_proofs WHERE order_id = NEW.order_id)
    ) THEN
        RAISE EXCEPTION 'Reseller orders require confirmed payment details and proof before commit.';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_require_reseller_order_payment ON orders;
CREATE CONSTRAINT TRIGGER trg_require_reseller_order_payment
AFTER INSERT ON orders DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_reseller_order_payment();

CREATE OR REPLACE FUNCTION prevent_reused_payment_proof()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE proof_reseller_id bigint;
BEGIN
    SELECT reseller_id INTO proof_reseller_id FROM orders WHERE order_id = NEW.order_id;
    IF proof_reseller_id IS NULL THEN RETURN NEW; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(proof_reseller_id::text || ':' || NEW.checksum_sha256, 0));
    IF EXISTS (
        SELECT 1
        FROM order_payment_proofs existing_proof
        JOIN orders existing_order ON existing_order.order_id = existing_proof.order_id
        WHERE existing_order.reseller_id = proof_reseller_id
          AND existing_proof.checksum_sha256 = NEW.checksum_sha256
    ) THEN
        RAISE EXCEPTION 'This payment screenshot has already been used for another order.';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_prevent_reused_payment_proof ON order_payment_proofs;
CREATE TRIGGER trg_prevent_reused_payment_proof
BEFORE INSERT ON order_payment_proofs FOR EACH ROW EXECUTE FUNCTION prevent_reused_payment_proof();

CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_reseller_payment_transaction
    ON orders (reseller_id, payment_transaction_key)
    WHERE order_type = 'reseller' AND payment_transaction_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_order_payment_proofs_checksum
    ON order_payment_proofs (checksum_sha256, order_id);
