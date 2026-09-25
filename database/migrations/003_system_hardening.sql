-- Transaction, ownership, notification, session, onboarding, and worker hardening.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM inventory_items WHERE base_price IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))
       OR EXISTS (SELECT 1 FROM order_items WHERE unit_price IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))
       OR EXISTS (SELECT 1 FROM sales_report_items WHERE unit_price IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)) THEN
        RAISE EXCEPTION 'System hardening migration blocked: non-finite money values exist.';
    END IF;
END $$;

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS activation_status text NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS activated_at timestamptz;

ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_activation_status_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_activation_status_check
    CHECK (activation_status IN ('pending', 'active'));

UPDATE accounts
SET activation_status = 'active', activated_at = COALESCE(activated_at, created_at)
WHERE activation_status = 'active' AND activated_at IS NULL;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS team_leader_account_id bigint;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_orders_team_leader') THEN
        ALTER TABLE orders ADD CONSTRAINT fk_orders_team_leader
            FOREIGN KEY (team_leader_account_id) REFERENCES accounts(account_id)
            ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END $$;

UPDATE orders o
SET team_leader_account_id = r.team_leader_account_id
FROM resellers r
WHERE o.reseller_id = r.reseller_id
  AND o.team_leader_account_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_orders_team_leader_date
    ON orders (team_leader_account_id, order_date DESC);

CREATE TABLE IF NOT EXISTS notification_recipients (
    notification_id bigint NOT NULL REFERENCES notifications(notification_id) ON UPDATE CASCADE ON DELETE CASCADE,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    read_at timestamptz,
    PRIMARY KEY (notification_id, account_id)
);

INSERT INTO notification_recipients (notification_id, account_id, read_at)
SELECT n.notification_id, a.account_id, n.read_at
FROM notifications n
JOIN accounts a ON a.is_active = true
 AND (
      a.account_id = n.recipient_account_id
      OR (n.recipient_account_id IS NULL AND
          ((n.recipient_role = 'owner' AND a.account_type = 'owner')
           OR (n.recipient_role = 'reseller' AND a.account_type = 'reseller')
           OR (n.recipient_role = 'team-leader' AND a.account_type = 'team_leader'
               AND ((n.category = 'inventory' AND a.team_leader_role = 'inventory')
                    OR (n.category <> 'inventory' AND a.team_leader_role = 'sales')))))
 )
ON CONFLICT DO NOTHING;

CREATE INDEX IF NOT EXISTS ix_notification_recipients_account_unread
    ON notification_recipients (account_id, read_at, notification_id DESC);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash char(64) PRIMARY KEY,
    account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (length(token_hash) = 64),
    CHECK (jsonb_typeof(data) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_web_sessions_account ON web_sessions (account_id);
CREATE INDEX IF NOT EXISTS ix_web_sessions_expiry ON web_sessions (expires_at);

CREATE TABLE IF NOT EXISTS security_events (
    security_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL,
    subject_key text NOT NULL,
    succeeded boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(event_type) <> ''),
    CHECK (btrim(subject_key) <> '')
);

CREATE INDEX IF NOT EXISTS ix_security_events_window
    ON security_events (event_type, subject_key, created_at DESC);

CREATE TABLE IF NOT EXISTS account_activation_tokens (
    activation_token_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(token_hash) = 64)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_account_activation_pending
    ON account_activation_tokens (account_id) WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS notification_outbox (
    outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    dedupe_key text NOT NULL UNIQUE,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 8),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    sent_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(event_type) <> ''),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_notification_outbox_due
    ON notification_outbox (next_attempt_at, outbox_id) WHERE sent_at IS NULL AND attempt_count < 8;

ALTER TABLE inventory_items DROP CONSTRAINT IF EXISTS inventory_items_base_price_finite_check;
ALTER TABLE inventory_items ADD CONSTRAINT inventory_items_base_price_finite_check
    CHECK (base_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
ALTER TABLE order_items DROP CONSTRAINT IF EXISTS order_items_unit_price_finite_check;
ALTER TABLE order_items ADD CONSTRAINT order_items_unit_price_finite_check
    CHECK (unit_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
ALTER TABLE sales_report_items DROP CONSTRAINT IF EXISTS sales_report_items_unit_price_finite_check;
ALTER TABLE sales_report_items ADD CONSTRAINT sales_report_items_unit_price_finite_check
    CHECK (unit_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));

CREATE OR REPLACE FUNCTION enforce_inquiry_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status IN ('approved', 'rejected', 'closed', 'onboarded') THEN
        RAISE EXCEPTION 'Inquiry status % is terminal.', OLD.status;
    END IF;
    IF NEW.status IN ('approved', 'rejected') AND OLD.status IN ('pending', 'assigned', 'contacted') THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'pending' AND NEW.status IN ('assigned', 'contacted') THEN RETURN NEW; END IF;
    IF OLD.status = 'assigned' AND NEW.status = 'contacted' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid inquiry status transition: % -> %.', OLD.status, NEW.status;
END $$;

DROP TRIGGER IF EXISTS trg_enforce_inquiry_status_transition ON inquiries;
CREATE TRIGGER trg_enforce_inquiry_status_transition
BEFORE UPDATE OF status ON inquiries
FOR EACH ROW EXECUTE FUNCTION enforce_inquiry_status_transition();

CREATE OR REPLACE FUNCTION enforce_order_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status IN ('rejected', 'fulfilled', 'cancelled') THEN
        RAISE EXCEPTION 'Order status % is terminal.', OLD.status;
    END IF;
    IF OLD.status = 'pending' AND NEW.status = 'approved' THEN
        IF OLD.order_type = 'reseller' AND NOT EXISTS (
            SELECT 1 FROM order_payment_proofs WHERE order_id = OLD.order_id
        ) THEN RAISE EXCEPTION 'Proof of payment is required before approval.'; END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'approved' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    IF OLD.status IN ('pending', 'approved') AND NEW.status = 'rejected' THEN RETURN NEW; END IF;
    IF OLD.order_type = 'walk_in' AND OLD.status = 'pending' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid order status transition: % -> %.', OLD.status, NEW.status;
END $$;

DROP TRIGGER IF EXISTS trg_enforce_order_status_transition ON orders;
CREATE TRIGGER trg_enforce_order_status_transition
BEFORE UPDATE OF status ON orders
FOR EACH ROW EXECUTE FUNCTION enforce_order_status_transition();

CREATE OR REPLACE FUNCTION enforce_order_team_leader_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.team_leader_account_id IS DISTINCT FROM NEW.team_leader_account_id THEN
        RAISE EXCEPTION 'Order team leader ownership is immutable.';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_enforce_order_team_leader_snapshot ON orders;
CREATE TRIGGER trg_enforce_order_team_leader_snapshot
BEFORE UPDATE OF team_leader_account_id ON orders
FOR EACH ROW EXECUTE FUNCTION enforce_order_team_leader_snapshot();

CREATE OR REPLACE FUNCTION validate_order_team_leader_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND NOT EXISTS (
        SELECT 1 FROM accounts a
        WHERE a.account_id = NEW.team_leader_account_id
          AND a.account_type = 'team_leader' AND a.team_leader_role = 'sales'
    ) THEN
        RAISE EXCEPTION 'Reseller orders require a sales team leader ownership snapshot.';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_validate_order_team_leader_snapshot ON orders;
CREATE TRIGGER trg_validate_order_team_leader_snapshot
BEFORE INSERT ON orders FOR EACH ROW EXECUTE FUNCTION validate_order_team_leader_snapshot();

CREATE OR REPLACE FUNCTION enforce_payment_proof_limit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE order_status text; proof_count integer;
BEGIN
    IF current_setting('meattrack.maintenance_import', true) = 'on' THEN RETURN NEW; END IF;
    SELECT status INTO order_status FROM orders WHERE order_id = NEW.order_id FOR UPDATE;
    IF order_status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'Payment proof uploads are allowed only for pending orders.';
    END IF;
    SELECT count(*) INTO proof_count FROM order_payment_proofs WHERE order_id = NEW.order_id;
    IF proof_count >= 3 THEN RAISE EXCEPTION 'An order can have at most three payment proofs.'; END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_enforce_payment_proof_limit ON order_payment_proofs;
CREATE TRIGGER trg_enforce_payment_proof_limit
BEFORE INSERT ON order_payment_proofs
FOR EACH ROW EXECUTE FUNCTION enforce_payment_proof_limit();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM alerts
        WHERE product_batch_id IS NOT NULL AND status IN ('open', 'acknowledged')
        GROUP BY product_batch_id, alert_type HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'System hardening migration blocked: duplicate unresolved batch alerts exist.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_batch_open_type
    ON alerts (product_batch_id, alert_type)
    WHERE product_batch_id IS NOT NULL AND status IN ('open', 'acknowledged');
