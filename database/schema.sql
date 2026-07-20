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
    auth_user_id uuid,
    auth_provider text,
    is_active boolean NOT NULL DEFAULT true,
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

CREATE TABLE media_assets (
    media_asset_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0),
    checksum_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\/]'),
    CHECK (btrim(content_type) <> ''),
    CHECK (length(checksum_sha256) = 64)
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
    base_price numeric(12,2) NOT NULL DEFAULT 0 CHECK (base_price >= 0),
    quantity_available numeric(12,3) NOT NULL DEFAULT 0 CHECK (quantity_available >= 0),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_type, name),
    CHECK (btrim(unit) <> ''),
    CHECK (item_type = 'finished_product' OR base_price = 0),
    CHECK (item_type = 'raw_material' OR quantity_available = 0)
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
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total numeric(12,2) GENERATED ALWAYS AS (round(quantity * unit_price, 2)) STORED
);

CREATE TABLE reseller_cart_items (
    cart_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, product_id)
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
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total numeric(12,2) GENERATED ALWAYS AS (round(quantity_sold * unit_price, 2)) STORED
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

CREATE UNIQUE INDEX ux_accounts_email_lower ON accounts (lower(email));
CREATE UNIQUE INDEX ux_accounts_auth_user_id ON accounts (auth_user_id)
    WHERE auth_user_id IS NOT NULL;
CREATE UNIQUE INDEX ux_resellers_email_lower ON resellers (lower(email));
CREATE UNIQUE INDEX ux_inventory_items_type_name_lower ON inventory_items (item_type, lower(name));
CREATE INDEX ix_inventory_items_type_name ON inventory_items (item_type, name);
CREATE INDEX ix_inventory_batches_fefo ON inventory_batches (item_id, expiry_date, quantity_available)
    WHERE quality_status = 'approved' AND quantity_available > 0;
CREATE INDEX ix_orders_reseller_status ON orders (reseller_id, status);
CREATE INDEX ix_reseller_cart_items_account_updated ON reseller_cart_items (account_id, updated_at DESC);
CREATE INDEX ix_reseller_cart_items_product ON reseller_cart_items (product_id);
CREATE INDEX ix_sales_report_items_report ON sales_report_items (sales_report_id);
CREATE INDEX ix_sales_report_items_product ON sales_report_items (product_id);
CREATE INDEX ix_sales_report_attachments_report ON sales_report_attachments (sales_report_id);
CREATE INDEX ix_activity_logs_account_created ON activity_logs (account_id, created_at DESC);
CREATE INDEX ix_alerts_status_type ON alerts (status, alert_type);
CREATE INDEX ix_user_consents_account_accepted ON user_consents (account_id, accepted_at DESC);
CREATE INDEX ix_notifications_role_read_created ON notifications (recipient_role, read_at, created_at DESC);
CREATE INDEX ix_notifications_account_read_created ON notifications (recipient_account_id, read_at, created_at DESC);

COMMIT;
