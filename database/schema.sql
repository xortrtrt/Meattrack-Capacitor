BEGIN;

-- MEATTRACK PostgreSQL schema (classroom simplified)
-- Keeps only the tables used by the current public site and portals.

CREATE TABLE departments (
    department_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_name text NOT NULL UNIQUE,
    description text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    account_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_type text NOT NULL
        CHECK (account_type IN ('owner', 'team_leader', 'reseller')),
    reseller_id bigint,
    name text NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    team_leader_role text CHECK (team_leader_role IS NULL OR team_leader_role IN ('inventory', 'sales')),
    is_active boolean NOT NULL DEFAULT true,
    activation_status text NOT NULL DEFAULT 'active' CHECK (activation_status IN ('pending', 'active')),
    activated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE activity_logs (
    activity_log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    action text NOT NULL,
    entity_type text,
    entity_id bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE inquiries (
    inquiry_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    contact_number text NOT NULL,
    email text NOT NULL,
    business_name text NOT NULL,
    message text,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'assigned', 'contacted', 'approved', 'rejected', 'closed', 'onboarded')),
    assigned_team_leader_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    reviewed_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    reviewed_at timestamptz,
    follow_up_sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE resellers (
    reseller_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inquiry_id bigint UNIQUE REFERENCES inquiries(inquiry_id) ON UPDATE CASCADE ON DELETE SET NULL,
    business_name text NOT NULL,
    contact_person text NOT NULL,
    email text NOT NULL,
    contact_number text NOT NULL,
    address text,
    reseller_status text NOT NULL DEFAULT 'active'
        CHECK (reseller_status IN ('pending', 'active', 'suspended', 'inactive')),
    team_leader_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    approved_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE accounts
    ADD CONSTRAINT fk_accounts_reseller
    FOREIGN KEY (reseller_id)
    REFERENCES resellers(reseller_id)
    ON UPDATE CASCADE
    ON DELETE SET NULL;

CREATE TABLE inventory_items (
    item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_type text NOT NULL CHECK (item_type IN ('raw_material', 'finished_product')),
    category text,
    name text NOT NULL,
    description text,
    unit text NOT NULL,
    base_price numeric(12,2) NOT NULL DEFAULT 0 CHECK (base_price >= 0 AND base_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    quantity_available numeric(12,3) NOT NULL DEFAULT 0 CHECK (quantity_available >= 0),
    pack_size numeric(12,3),
    pack_size_unit text,
    pack_content_status text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_type, name),
    CHECK (btrim(unit) <> ''),
    CHECK (item_type = 'finished_product' OR base_price = 0),
    CHECK (item_type = 'raw_material' OR quantity_available = 0),
    CONSTRAINT inventory_items_measurement_contract_check CHECK (
        (
            item_type = 'raw_material'
            AND unit IN ('kg', 'g', 'ml')
            AND pack_size IS NULL
            AND pack_size_unit IS NULL
            AND pack_content_status IS NULL
        )
        OR
        (
            item_type = 'finished_product'
            AND unit = 'pack'
            AND (
                (pack_content_status = 'declared' AND pack_size > 0 AND pack_size_unit IN ('g', 'kg', 'ml'))
                OR
                (pack_content_status = 'unknown_legacy' AND pack_size IS NULL AND pack_size_unit IS NULL)
            )
        )
    )
);

CREATE TABLE inventory_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    batch_code text NOT NULL UNIQUE,
    source_type text NOT NULL DEFAULT 'direct_received'
        CHECK (source_type IN ('direct_received', 'production')),
    quantity_received numeric(12,3) NOT NULL CHECK (quantity_received > 0),
    quantity_available numeric(12,3) NOT NULL CHECK (quantity_available >= 0),
    unit text NOT NULL,
    received_date date NOT NULL DEFAULT CURRENT_DATE,
    expiry_date date NOT NULL,
    quality_status text NOT NULL DEFAULT 'approved'
        CHECK (quality_status IN ('pending', 'approved', 'rejected', 'expired', 'spoiled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (quantity_available <= quantity_received),
    CONSTRAINT inventory_batches_whole_pack_check CHECK (
        quantity_received = trunc(quantity_received) AND quantity_available = trunc(quantity_available)
    ),
    CHECK (expiry_date >= received_date),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE product_recipes (
    recipe_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_item_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    material_item_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity_required numeric(12,3) NOT NULL CHECK (quantity_required > 0),
    unit text NOT NULL,
    UNIQUE (product_item_id, material_item_id),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE orders (
    order_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_type text NOT NULL
        CHECK (order_type IN ('walk_in', 'reseller')),
    reseller_id bigint REFERENCES resellers(reseller_id) ON UPDATE CASCADE ON DELETE SET NULL,
    created_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    team_leader_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    approved_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    approved_at timestamptz,
    order_date timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'fulfilled', 'cancelled')),
    fulfilled_at timestamptz,
    total_amount numeric(12,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    notes text
);

CREATE TABLE order_items (
    order_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    unit text NOT NULL DEFAULT 'pack',
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0 AND unit_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    line_total numeric(12,2) GENERATED ALWAYS AS (round(quantity * unit_price, 2)) STORED,
    CONSTRAINT order_items_whole_pack_check CHECK (quantity = trunc(quantity)),
    CONSTRAINT order_items_pack_unit_check CHECK (unit = 'pack')
);

CREATE TABLE order_payment_proofs (
    order_payment_proof_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    uploaded_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    filename text NOT NULL,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 5242880),
    checksum_sha256 text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\/]'),
    CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    CHECK (length(checksum_sha256) = 64)
);

CREATE TABLE reseller_cart_items (
    cart_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, product_id),
    CONSTRAINT reseller_cart_items_whole_pack_check CHECK (quantity = trunc(quantity))
);

CREATE TABLE sales_reports (
    sales_report_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_source text NOT NULL
        CHECK (report_source IN ('team_leader', 'reseller')),
    submitted_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    reseller_id bigint REFERENCES resellers(reseller_id) ON UPDATE CASCADE ON DELETE SET NULL,
    department_id bigint REFERENCES departments(department_id) ON UPDATE CASCADE ON DELETE SET NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    total_sales numeric(12,2) NOT NULL DEFAULT 0 CHECK (total_sales >= 0),
    total_orders integer NOT NULL DEFAULT 0 CHECK (total_orders >= 0),
    notes text,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start)
);

CREATE TABLE sales_report_items (
    sales_report_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sales_report_id bigint NOT NULL REFERENCES sales_reports(sales_report_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity_sold numeric(12,3) NOT NULL CHECK (quantity_sold > 0),
    unit text NOT NULL DEFAULT 'pack',
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0 AND unit_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    line_total numeric(12,2) GENERATED ALWAYS AS (round(quantity_sold * unit_price, 2)) STORED,
    CONSTRAINT sales_report_items_whole_pack_check CHECK (quantity_sold = trunc(quantity_sold)),
    CONSTRAINT sales_report_items_pack_unit_check CHECK (unit = 'pack')
);

CREATE TABLE sales_report_attachments (
    sales_report_attachment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sales_report_id bigint NOT NULL REFERENCES sales_reports(sales_report_id) ON UPDATE CASCADE ON DELETE CASCADE,
    filename text NOT NULL,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 5242880),
    checksum_sha256 text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\/]'),
    CHECK (btrim(content_type) <> ''),
    CHECK (length(checksum_sha256) = 64)
);

CREATE TABLE alerts (
    alert_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_type text NOT NULL
        CHECK (alert_type IN ('low_stock', 'near_expiry', 'expired_batch', 'forecast')),
    severity text NOT NULL DEFAULT 'warning'
        CHECK (severity IN ('info', 'warning', 'critical')),
    product_id bigint REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_batch_id bigint REFERENCES inventory_batches(batch_id) ON UPDATE CASCADE ON DELETE CASCADE,
    raw_material_id bigint REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    message text NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved')),
    triggered_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        product_id IS NOT NULL
        OR product_batch_id IS NOT NULL
        OR raw_material_id IS NOT NULL
    )
);

CREATE TABLE forecast_runs (
    forecast_run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    model_name text NOT NULL,
    input_period_start date NOT NULL,
    input_period_end date NOT NULL,
    forecast_horizon_days integer NOT NULL CHECK (forecast_horizon_days > 0),
    status text NOT NULL DEFAULT 'completed'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    notes text,
    CHECK (input_period_end >= input_period_start)
);

CREATE TABLE forecast_results (
    forecast_result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    forecast_run_id bigint NOT NULL REFERENCES forecast_runs(forecast_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    forecast_date date NOT NULL,
    predicted_quantity numeric(12,3) NOT NULL CHECK (predicted_quantity >= 0),
    confidence_lower numeric(12,3) CHECK (confidence_lower IS NULL OR confidence_lower >= 0),
    confidence_upper numeric(12,3) CHECK (confidence_upper IS NULL OR confidence_upper >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (forecast_run_id, product_id, forecast_date),
    CHECK (confidence_lower IS NULL OR confidence_upper IS NULL OR confidence_upper >= confidence_lower)
);

CREATE TABLE user_consents (
    user_consent_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    policy_version text NOT NULL,
    consent_source text NOT NULL,
    provider text,
    accepted_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(policy_version) <> ''),
    CHECK (btrim(consent_source) <> '')
);

CREATE TABLE account_password_otps (
    account_password_otp_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    otp_hash text NOT NULL,
    pending_password_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(otp_hash) <> ''),
    CHECK (btrim(pending_password_hash) <> '')
);

CREATE TABLE account_login_otps (
    account_login_otp_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    otp_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(otp_hash) <> '')
);

CREATE TABLE notifications (
    notification_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipient_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    recipient_role text CHECK (recipient_role IN ('owner', 'team-leader', 'reseller')),
    category text NOT NULL,
    severity text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    title text NOT NULL,
    message text NOT NULL,
    target_url text,
    source_type text,
    source_id bigint,
    dedupe_key text UNIQUE,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (recipient_account_id IS NOT NULL OR recipient_role IS NOT NULL),
    CHECK (btrim(category) <> ''),
    CHECK (btrim(title) <> ''),
    CHECK (btrim(message) <> '')
);

CREATE TABLE notification_recipients (
    notification_id bigint NOT NULL REFERENCES notifications(notification_id) ON UPDATE CASCADE ON DELETE CASCADE,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    read_at timestamptz,
    PRIMARY KEY (notification_id, account_id)
);

CREATE TABLE web_sessions (
    token_hash char(64) PRIMARY KEY,
    account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    data jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (length(token_hash) = 64)
);

CREATE TABLE security_events (
    security_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    subject_key text NOT NULL CHECK (btrim(subject_key) <> ''),
    succeeded boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE account_activation_tokens (
    activation_token_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notification_outbox (
    outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    dedupe_key text NOT NULL UNIQUE,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 8),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    sent_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE inventory_movements (
    movement_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    affected_batch_id bigint REFERENCES inventory_batches(batch_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    production_batch_id bigint REFERENCES inventory_batches(batch_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    order_id bigint REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    movement_type text NOT NULL CHECK (movement_type IN (
        'opening_balance', 'raw_receipt', 'production_consumption',
        'production_output', 'direct_finished_receipt', 'sale_fulfillment'
    )),
    quantity_delta numeric(12,3) NOT NULL,
    unit text NOT NULL,
    balance_before numeric(12,3) NOT NULL CHECK (balance_before >= 0),
    balance_after numeric(12,3) NOT NULL CHECK (balance_after >= 0),
    actor_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    actor_name text NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (balance_after = balance_before + quantity_delta),
    CHECK (quantity_delta <> 0 OR movement_type = 'opening_balance'),
    CHECK (btrim(unit) <> ''),
    CHECK (btrim(actor_name) <> '')
);

CREATE OR REPLACE FUNCTION validate_finished_product_reference()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    product_type text;
BEGIN
    SELECT item_type INTO product_type FROM inventory_items WHERE item_id = NEW.product_id;
    IF product_type IS DISTINCT FROM 'finished_product' THEN
        RAISE EXCEPTION 'Cart, order, and report lines must reference a finished product.';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_cart_finished_product
BEFORE INSERT OR UPDATE OF product_id ON reseller_cart_items
FOR EACH ROW EXECUTE FUNCTION validate_finished_product_reference();
CREATE TRIGGER trg_validate_order_item_finished_product
BEFORE INSERT OR UPDATE OF product_id ON order_items
FOR EACH ROW EXECUTE FUNCTION validate_finished_product_reference();
CREATE TRIGGER trg_validate_report_item_finished_product
BEFORE INSERT OR UPDATE OF product_id ON sales_report_items
FOR EACH ROW EXECUTE FUNCTION validate_finished_product_reference();

CREATE OR REPLACE FUNCTION validate_product_recipe_measurements()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    product_type text;
    material_type text;
    material_unit text;
BEGIN
    SELECT item_type INTO product_type FROM inventory_items WHERE item_id = NEW.product_item_id;
    SELECT item_type, unit INTO material_type, material_unit FROM inventory_items WHERE item_id = NEW.material_item_id;
    IF product_type IS DISTINCT FROM 'finished_product' THEN
        RAISE EXCEPTION 'Recipe product must be a finished product.';
    END IF;
    IF material_type IS DISTINCT FROM 'raw_material' THEN
        RAISE EXCEPTION 'Recipe ingredient must be a raw material.';
    END IF;
    IF NEW.unit IS DISTINCT FROM material_unit THEN
        RAISE EXCEPTION 'Recipe unit must match the raw material stock unit.';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_product_recipe_measurements
BEFORE INSERT OR UPDATE ON product_recipes
FOR EACH ROW EXECUTE FUNCTION validate_product_recipe_measurements();

CREATE OR REPLACE FUNCTION validate_inventory_batch_measurements()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    product_type text;
    product_unit text;
BEGIN
    SELECT item_type, unit INTO product_type, product_unit FROM inventory_items WHERE item_id = NEW.item_id;
    IF product_type IS DISTINCT FROM 'finished_product' THEN
        RAISE EXCEPTION 'Inventory batches may only belong to finished products.';
    END IF;
    IF NEW.unit IS DISTINCT FROM product_unit OR NEW.unit <> 'pack' THEN
        RAISE EXCEPTION 'Inventory batch unit must match the finished product pack unit.';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_inventory_batch_measurements
BEFORE INSERT OR UPDATE ON inventory_batches
FOR EACH ROW EXECUTE FUNCTION validate_inventory_batch_measurements();

CREATE OR REPLACE FUNCTION protect_inventory_item_measurements()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.item_type IS DISTINCT FROM OLD.item_type OR NEW.unit IS DISTINCT FROM OLD.unit THEN
        IF EXISTS (
            SELECT 1 FROM product_recipes
            WHERE product_item_id = OLD.item_id OR material_item_id = OLD.item_id
        ) OR EXISTS (
            SELECT 1 FROM inventory_batches WHERE item_id = OLD.item_id
        ) THEN
            RAISE EXCEPTION 'Inventory item type and unit cannot change after recipes or batches exist.';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_protect_inventory_item_measurements
BEFORE UPDATE OF item_type, unit ON inventory_items
FOR EACH ROW EXECUTE FUNCTION protect_inventory_item_measurements();

CREATE OR REPLACE FUNCTION validate_inventory_movement()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    affected_type text;
    affected_unit text;
    batch_item_id bigint;
    production_item_type text;
BEGIN
    SELECT item_type, unit INTO affected_type, affected_unit FROM inventory_items WHERE item_id = NEW.item_id;
    IF affected_type IS NULL THEN
        RAISE EXCEPTION 'Inventory movement item does not exist.';
    END IF;
    IF NEW.unit IS DISTINCT FROM affected_unit THEN
        RAISE EXCEPTION 'Inventory movement unit must match the item stock unit.';
    END IF;
    IF affected_type = 'raw_material' AND NEW.affected_batch_id IS NOT NULL THEN
        RAISE EXCEPTION 'Raw material movements cannot have an affected finished batch.';
    END IF;
    IF affected_type = 'finished_product' AND NEW.affected_batch_id IS NULL THEN
        RAISE EXCEPTION 'Finished product movements require an affected batch.';
    END IF;
    IF NEW.affected_batch_id IS NOT NULL THEN
        SELECT item_id INTO batch_item_id FROM inventory_batches WHERE batch_id = NEW.affected_batch_id;
        IF batch_item_id IS DISTINCT FROM NEW.item_id THEN
            RAISE EXCEPTION 'Affected batch does not belong to the movement item.';
        END IF;
    END IF;
    IF NEW.production_batch_id IS NOT NULL THEN
        SELECT ii.item_type INTO production_item_type
        FROM inventory_batches ib JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE ib.batch_id = NEW.production_batch_id;
        IF production_item_type IS DISTINCT FROM 'finished_product' THEN
            RAISE EXCEPTION 'Production batch must identify a finished-product batch.';
        END IF;
    END IF;
    IF NEW.movement_type = 'production_consumption'
       AND (affected_type <> 'raw_material' OR NEW.production_batch_id IS NULL OR NEW.quantity_delta >= 0) THEN
        RAISE EXCEPTION 'Production consumption must deduct raw material for a production batch.';
    END IF;
    IF NEW.movement_type IN ('production_output', 'direct_finished_receipt')
       AND (affected_type <> 'finished_product' OR NEW.quantity_delta <= 0) THEN
        RAISE EXCEPTION 'Finished receipt movements must add finished stock.';
    END IF;
    IF NEW.movement_type = 'sale_fulfillment'
       AND (affected_type <> 'finished_product' OR NEW.order_id IS NULL OR NEW.quantity_delta >= 0) THEN
        RAISE EXCEPTION 'Sale fulfillment must deduct finished stock for an order.';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_validate_inventory_movement
BEFORE INSERT ON inventory_movements
FOR EACH ROW EXECUTE FUNCTION validate_inventory_movement();

CREATE OR REPLACE FUNCTION reject_inventory_movement_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Inventory movements are immutable.';
END $$;

CREATE TRIGGER trg_inventory_movements_no_update_delete
BEFORE UPDATE OR DELETE ON inventory_movements
FOR EACH ROW EXECUTE FUNCTION reject_inventory_movement_mutation();
CREATE TRIGGER trg_inventory_movements_no_truncate
BEFORE TRUNCATE ON inventory_movements
FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();

CREATE OR REPLACE FUNCTION enforce_inquiry_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status IN ('approved', 'rejected', 'closed', 'onboarded') THEN
        RAISE EXCEPTION 'Inquiry status % is terminal.', OLD.status;
    END IF;
    IF NEW.status IN ('approved', 'rejected') AND OLD.status IN ('pending', 'assigned', 'contacted') THEN RETURN NEW; END IF;
    IF OLD.status = 'pending' AND NEW.status IN ('assigned', 'contacted') THEN RETURN NEW; END IF;
    IF OLD.status = 'assigned' AND NEW.status = 'contacted' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid inquiry status transition: % -> %.', OLD.status, NEW.status;
END $$;
CREATE TRIGGER trg_enforce_inquiry_status_transition
BEFORE UPDATE OF status ON inquiries FOR EACH ROW EXECUTE FUNCTION enforce_inquiry_status_transition();

CREATE OR REPLACE FUNCTION enforce_order_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status IN ('rejected', 'fulfilled', 'cancelled') THEN RAISE EXCEPTION 'Order status % is terminal.', OLD.status; END IF;
    IF OLD.status = 'pending' AND NEW.status = 'approved' THEN
        IF OLD.order_type = 'reseller' AND NOT EXISTS (SELECT 1 FROM order_payment_proofs WHERE order_id = OLD.order_id) THEN
            RAISE EXCEPTION 'Proof of payment is required before approval.';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'approved' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    IF OLD.status IN ('pending', 'approved') AND NEW.status = 'rejected' THEN RETURN NEW; END IF;
    IF OLD.order_type = 'walk_in' AND OLD.status = 'pending' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid order status transition: % -> %.', OLD.status, NEW.status;
END $$;
CREATE TRIGGER trg_enforce_order_status_transition
BEFORE UPDATE OF status ON orders FOR EACH ROW EXECUTE FUNCTION enforce_order_status_transition();

CREATE OR REPLACE FUNCTION enforce_order_team_leader_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.team_leader_account_id IS DISTINCT FROM NEW.team_leader_account_id THEN
        RAISE EXCEPTION 'Order team leader ownership is immutable.';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_enforce_order_team_leader_snapshot
BEFORE UPDATE OF team_leader_account_id ON orders FOR EACH ROW EXECUTE FUNCTION enforce_order_team_leader_snapshot();

CREATE OR REPLACE FUNCTION validate_order_team_leader_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND NOT EXISTS (
        SELECT 1 FROM accounts a WHERE a.account_id = NEW.team_leader_account_id
          AND a.account_type = 'team_leader' AND a.team_leader_role = 'sales'
    ) THEN RAISE EXCEPTION 'Reseller orders require a sales team leader ownership snapshot.'; END IF;
    RETURN NEW;
END $$;
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
CREATE TRIGGER trg_enforce_payment_proof_limit
BEFORE INSERT ON order_payment_proofs FOR EACH ROW EXECUTE FUNCTION enforce_payment_proof_limit();

CREATE UNIQUE INDEX ux_accounts_email_lower ON accounts (lower(email));
CREATE INDEX ix_accounts_team_leader_role ON accounts (team_leader_role)
    WHERE account_type = 'team_leader';
CREATE UNIQUE INDEX ux_resellers_email_lower ON resellers (lower(email));
CREATE UNIQUE INDEX ux_inventory_items_type_name_lower ON inventory_items (item_type, lower(name));
CREATE INDEX ix_inventory_items_type_name ON inventory_items (item_type, name);
CREATE INDEX ix_inventory_batches_fefo ON inventory_batches (item_id, expiry_date, quantity_available)
    WHERE quality_status = 'approved' AND quantity_available > 0;
CREATE INDEX ix_orders_reseller_status ON orders (reseller_id, status);
CREATE INDEX ix_order_payment_proofs_order ON order_payment_proofs (order_id, uploaded_at DESC);
CREATE INDEX ix_resellers_team_leader ON resellers (team_leader_account_id);
CREATE INDEX ix_reseller_cart_items_account_updated ON reseller_cart_items (account_id, updated_at DESC);
CREATE INDEX ix_reseller_cart_items_product ON reseller_cart_items (product_id);
CREATE INDEX ix_sales_report_items_report ON sales_report_items (sales_report_id);
CREATE INDEX ix_sales_report_items_product ON sales_report_items (product_id);
CREATE INDEX ix_sales_report_attachments_report ON sales_report_attachments (sales_report_id);
CREATE INDEX ix_activity_logs_account_created ON activity_logs (account_id, created_at DESC);
CREATE INDEX ix_alerts_status_type ON alerts (status, alert_type);
CREATE INDEX ix_user_consents_account_accepted ON user_consents (account_id, accepted_at DESC);
CREATE INDEX ix_account_password_otps_pending ON account_password_otps (account_id, created_at DESC)
    WHERE consumed_at IS NULL;
CREATE INDEX ix_account_login_otps_pending ON account_login_otps (account_id, created_at DESC)
    WHERE consumed_at IS NULL;
CREATE INDEX ix_notifications_role_read_created ON notifications (recipient_role, read_at, created_at DESC);
CREATE INDEX ix_notifications_account_read_created ON notifications (recipient_account_id, read_at, created_at DESC);
CREATE INDEX ix_notification_recipients_account_unread ON notification_recipients (account_id, read_at, notification_id DESC);
CREATE INDEX ix_web_sessions_account ON web_sessions (account_id);
CREATE INDEX ix_web_sessions_expiry ON web_sessions (expires_at);
CREATE INDEX ix_security_events_window ON security_events (event_type, subject_key, created_at DESC);
CREATE UNIQUE INDEX ux_account_activation_pending ON account_activation_tokens (account_id) WHERE consumed_at IS NULL;
CREATE INDEX ix_notification_outbox_due ON notification_outbox (next_attempt_at, outbox_id)
    WHERE sent_at IS NULL AND attempt_count < 8;
CREATE INDEX ix_orders_team_leader_date ON orders (team_leader_account_id, order_date DESC);
CREATE UNIQUE INDEX ux_alert_batch_open_type ON alerts (product_batch_id, alert_type)
    WHERE product_batch_id IS NOT NULL AND status IN ('open', 'acknowledged');
CREATE UNIQUE INDEX ux_inventory_movements_sale_batch
    ON inventory_movements (order_id, affected_batch_id, movement_type)
    WHERE movement_type = 'sale_fulfillment';
CREATE UNIQUE INDEX ux_inventory_movements_production_output
    ON inventory_movements (affected_batch_id, movement_type)
    WHERE movement_type = 'production_output';
CREATE UNIQUE INDEX ux_inventory_movements_production_consumption
    ON inventory_movements (production_batch_id, item_id, movement_type)
    WHERE movement_type = 'production_consumption';
CREATE INDEX ix_inventory_movements_created ON inventory_movements (created_at DESC, movement_id DESC);
CREATE INDEX ix_inventory_movements_item_created ON inventory_movements (item_id, created_at DESC);

COMMIT;
