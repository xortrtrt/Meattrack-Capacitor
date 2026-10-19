ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS rejection_payment_received boolean;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_rejection_payment_received_check') THEN
        ALTER TABLE orders ADD CONSTRAINT orders_rejection_payment_received_check
            CHECK (rejection_payment_received IS NULL OR status = 'rejected');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS order_refunds (
    order_refund_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL UNIQUE REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    amount numeric(12,2) NOT NULL CHECK (
        amount > 0 AND amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    currency char(3) NOT NULL DEFAULT 'PHP' CHECK (currency = 'PHP'),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    requested_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    assigned_supervisor_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    processing_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    processing_started_at timestamptz,
    completed_at timestamptz,
    failed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'processing' OR (processing_by_account_id IS NOT NULL AND processing_started_at IS NOT NULL)),
    CHECK (status <> 'completed' OR (completed_at IS NOT NULL AND failed_at IS NULL)),
    CHECK (status <> 'failed' OR (failed_at IS NOT NULL AND completed_at IS NULL))
);

CREATE TABLE IF NOT EXISTS order_refund_attempts (
    order_refund_attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_refund_id bigint NOT NULL REFERENCES order_refunds(order_refund_id) ON UPDATE CASCADE ON DELETE CASCADE,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    status text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'completed', 'failed')),
    processor_account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    refund_method text,
    refund_transaction_number text,
    refund_transaction_key text,
    transferred_at timestamptz,
    internal_note text CHECK (internal_note IS NULL OR char_length(btrim(internal_note)) <= 1000),
    failure_reason text CHECK (failure_reason IS NULL OR char_length(btrim(failure_reason)) BETWEEN 5 AND 500),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    failed_at timestamptz,
    UNIQUE (order_refund_id, attempt_number),
    CHECK (refund_method IS NULL OR char_length(btrim(refund_method)) BETWEEN 2 AND 100),
    CHECK (refund_transaction_number IS NULL OR btrim(refund_transaction_number) <> ''),
    CHECK (refund_transaction_key IS NULL OR refund_transaction_key ~ '^[A-Z0-9]{6,64}$'),
    CHECK (
        refund_transaction_key IS NULL OR (
            refund_transaction_number IS NOT NULL
            AND refund_transaction_key = upper(regexp_replace(refund_transaction_number, '[^A-Za-z0-9]', '', 'g'))
        )
    ),
    CHECK (
        status <> 'completed' OR (
            refund_method IS NOT NULL
            AND refund_transaction_number IS NOT NULL
            AND refund_transaction_key IS NOT NULL
            AND transferred_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND failure_reason IS NULL
        )
    ),
    CHECK (status <> 'failed' OR (
        failure_reason IS NOT NULL AND failed_at IS NOT NULL
        AND refund_method IS NULL AND refund_transaction_number IS NULL
        AND refund_transaction_key IS NULL AND transferred_at IS NULL AND completed_at IS NULL
    )),
    CHECK (status <> 'processing' OR (
        refund_method IS NULL AND refund_transaction_number IS NULL
        AND refund_transaction_key IS NULL AND transferred_at IS NULL
        AND failure_reason IS NULL AND completed_at IS NULL AND failed_at IS NULL
    ))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_order_refund_attempt_transaction
    ON order_refund_attempts (refund_transaction_key)
    WHERE refund_transaction_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS order_refund_proofs (
    order_refund_proof_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_refund_attempt_id bigint NOT NULL UNIQUE
        REFERENCES order_refund_attempts(order_refund_attempt_id) ON UPDATE CASCADE ON DELETE CASCADE,
    uploaded_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    filename text NOT NULL,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 5242880),
    checksum_sha256 char(64) NOT NULL CHECK (length(checksum_sha256) = 64),
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\/]'),
    CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp'))
);

ALTER TABLE order_system_messages
    DROP CONSTRAINT IF EXISTS order_system_messages_event_type_check,
    DROP CONSTRAINT IF EXISTS order_system_messages_reseller_account_id_order_id_event_type_key,
    DROP CONSTRAINT IF EXISTS order_system_messages_check;

ALTER TABLE order_system_messages
    ADD COLUMN IF NOT EXISTS dedupe_key text,
    ADD COLUMN IF NOT EXISTS order_refund_attempt_id bigint
        REFERENCES order_refund_attempts(order_refund_attempt_id) ON UPDATE CASCADE ON DELETE SET NULL;

UPDATE order_system_messages
SET dedupe_key = 'order-' || order_id || '-' || event_type
WHERE dedupe_key IS NULL;

ALTER TABLE order_system_messages
    ALTER COLUMN dedupe_key SET NOT NULL,
    ADD CONSTRAINT order_system_messages_event_type_check CHECK (
        event_type IN (
            'approved', 'rejected', 'fulfilled',
            'refund_pending', 'refund_processing', 'refund_completed', 'refund_failed'
        )
    ),
    ADD CONSTRAINT order_system_messages_rejection_reason_check CHECK (
        (event_type = 'rejected' AND char_length(btrim(rejection_reason)) BETWEEN 5 AND 400)
        OR (event_type <> 'rejected' AND rejection_reason IS NULL)
    );

CREATE UNIQUE INDEX IF NOT EXISTS ux_order_system_messages_dedupe
    ON order_system_messages (dedupe_key);
CREATE INDEX IF NOT EXISTS ix_order_refunds_status_requested
    ON order_refunds (status, requested_at, order_refund_id);
CREATE INDEX IF NOT EXISTS ix_order_refunds_supervisor_status
    ON order_refunds (assigned_supervisor_account_id, status, requested_at);
CREATE INDEX IF NOT EXISTS ix_order_refund_attempts_refund
    ON order_refund_attempts (order_refund_id, attempt_number);

CREATE OR REPLACE FUNCTION enforce_order_refund_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'Completed refunds are terminal.';
    END IF;
    IF OLD.status = 'pending' AND NEW.status = 'processing' THEN RETURN NEW; END IF;
    IF OLD.status = 'processing' AND NEW.status IN ('completed', 'failed') THEN RETURN NEW; END IF;
    IF OLD.status = 'failed' AND NEW.status = 'processing' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid refund status transition: % -> %.', OLD.status, NEW.status;
END $$;

DROP TRIGGER IF EXISTS trg_enforce_order_refund_status_transition ON order_refunds;
CREATE TRIGGER trg_enforce_order_refund_status_transition
BEFORE UPDATE OF status ON order_refunds
FOR EACH ROW EXECUTE FUNCTION enforce_order_refund_status_transition();

CREATE OR REPLACE FUNCTION protect_order_refund_audit_fields()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.amount IS DISTINCT FROM OLD.amount
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.requested_by_account_id IS DISTINCT FROM OLD.requested_by_account_id
       OR NEW.requested_at IS DISTINCT FROM OLD.requested_at THEN
        RAISE EXCEPTION 'Locked refund request fields cannot be changed.';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_protect_order_refund_audit_fields ON order_refunds;
CREATE TRIGGER trg_protect_order_refund_audit_fields
BEFORE UPDATE ON order_refunds
FOR EACH ROW EXECUTE FUNCTION protect_order_refund_audit_fields();

CREATE OR REPLACE FUNCTION protect_finished_refund_attempt()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'Completed and failed refund attempts are append-only.';
    END IF;
    IF NEW.order_refund_id IS DISTINCT FROM OLD.order_refund_id
       OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
       OR NEW.processor_account_id IS DISTINCT FROM OLD.processor_account_id
       OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'Refund attempt identity fields cannot be changed.';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_protect_finished_refund_attempt ON order_refund_attempts;
CREATE TRIGGER trg_protect_finished_refund_attempt
BEFORE UPDATE ON order_refund_attempts
FOR EACH ROW EXECUTE FUNCTION protect_finished_refund_attempt();
