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
    profile_image_path text,
    profile_image_position_x numeric(5,2) NOT NULL DEFAULT 50,
    profile_image_position_y numeric(5,2) NOT NULL DEFAULT 50,
    profile_image_scale numeric(4,2) NOT NULL DEFAULT 1,
    team_leader_role text CHECK (team_leader_role IS NULL OR team_leader_role IN ('inventory', 'sales', 'sales-supervisor')),
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
    rejection_reason text CHECK (rejection_reason IS NULL OR char_length(btrim(rejection_reason)) BETWEEN 5 AND 1000),
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
        CHECK (status IN ('to_pay', 'pending', 'approved', 'rejected', 'fulfilled', 'cancelled', 'expired')),
    fulfilled_at timestamptz,
    total_amount numeric(12,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    notes text,
    rejection_reason text,
    rejection_payment_received boolean,
    cancellation_reason text,
    cancelled_at timestamptz,
    payment_transaction_number text,
    payment_transaction_key text,
    payment_paid_at timestamptz,
    payment_amount numeric(12,2),
    payment_ocr_confidence numeric(5,4),
    payment_extraction_method text,
    payment_confirmed_at timestamptz,
    payment_ocr_engine text,
    payment_due_at timestamptz,
    expired_at timestamptz,
    CONSTRAINT orders_rejection_reason_check CHECK (
        rejection_reason IS NULL OR char_length(btrim(rejection_reason)) BETWEEN 5 AND 400
    ),
    CONSTRAINT orders_rejection_payment_received_check CHECK (
        rejection_payment_received IS NULL OR status = 'rejected'
    ),
    CONSTRAINT orders_cancellation_reason_check CHECK (
        (status = 'cancelled' AND char_length(btrim(cancellation_reason)) BETWEEN 5 AND 400)
        OR (status <> 'cancelled' AND cancellation_reason IS NULL AND cancelled_at IS NULL)
    ),
    CONSTRAINT orders_payment_transaction_number_check
        CHECK (payment_transaction_number IS NULL OR btrim(payment_transaction_number) <> ''),
    CONSTRAINT orders_payment_transaction_key_check
        CHECK (payment_transaction_key IS NULL OR payment_transaction_key ~ '^[A-Z0-9]{6,64}$'),
    CONSTRAINT orders_payment_transaction_normalized_check CHECK (
        payment_transaction_key IS NULL OR (
            payment_transaction_number IS NOT NULL
            AND payment_transaction_key = upper(regexp_replace(payment_transaction_number, '[^A-Za-z0-9]', '', 'g'))
        )
    ),
    CONSTRAINT orders_payment_ocr_confidence_check
        CHECK (payment_ocr_confidence IS NULL OR payment_ocr_confidence BETWEEN 0 AND 1),
    CONSTRAINT orders_payment_amount_check
        CHECK (
            payment_amount IS NULL OR (
                payment_amount > 0
                AND payment_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            )
        ),
    CONSTRAINT orders_payment_extraction_method_check
        CHECK (payment_extraction_method IS NULL OR payment_extraction_method IN ('ocr_confirmed', 'manual_corrected')),
    CONSTRAINT orders_to_pay_deadline_check CHECK (
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
    )
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

CREATE TABLE order_stock_reservations (
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    reserved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (order_id, product_id)
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

CREATE TABLE order_refunds (
    order_refund_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL UNIQUE REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    amount numeric(12,2) NOT NULL CHECK (amount > 0 AND amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    currency char(3) NOT NULL DEFAULT 'PHP' CHECK (currency = 'PHP'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
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

CREATE TABLE order_refund_attempts (
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
    CHECK (refund_transaction_key IS NULL OR (refund_transaction_number IS NOT NULL AND refund_transaction_key = upper(regexp_replace(refund_transaction_number, '[^A-Za-z0-9]', '', 'g')))),
    CHECK (status <> 'completed' OR (refund_method IS NOT NULL AND refund_transaction_number IS NOT NULL AND refund_transaction_key IS NOT NULL AND transferred_at IS NOT NULL AND completed_at IS NOT NULL AND failure_reason IS NULL)),
    CHECK (status <> 'failed' OR (failure_reason IS NOT NULL AND failed_at IS NOT NULL AND refund_method IS NULL AND refund_transaction_number IS NULL AND refund_transaction_key IS NULL AND transferred_at IS NULL AND completed_at IS NULL)),
    CHECK (status <> 'processing' OR (refund_method IS NULL AND refund_transaction_number IS NULL AND refund_transaction_key IS NULL AND transferred_at IS NULL AND failure_reason IS NULL AND completed_at IS NULL AND failed_at IS NULL))
);

CREATE TABLE order_refund_proofs (
    order_refund_proof_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_refund_attempt_id bigint NOT NULL UNIQUE REFERENCES order_refund_attempts(order_refund_attempt_id) ON UPDATE CASCADE ON DELETE CASCADE,
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

CREATE TABLE order_refund_messages (
    order_refund_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_refund_id bigint NOT NULL REFERENCES order_refunds(order_refund_id) ON UPDATE CASCADE ON DELETE CASCADE,
    sender_account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    sender_role text NOT NULL CHECK (sender_role IN ('owner', 'sales-supervisor', 'reseller')),
    message text NOT NULL CHECK (char_length(btrim(message)) BETWEEN 1 AND 1000),
    created_at timestamptz NOT NULL DEFAULT now()
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
    forecast_horizon_days integer NOT NULL CHECK (forecast_horizon_days BETWEEN 1 AND 365),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'completed_with_warnings', 'failed')),
    queued_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    last_error text,
    total_products integer NOT NULL DEFAULT 0 CHECK (total_products >= 0),
    processed_products integer NOT NULL DEFAULT 0 CHECK (processed_products >= 0 AND processed_products <= total_products),
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes text,
    CHECK (input_period_end >= input_period_start)
);

CREATE TABLE forecast_product_summaries (
    forecast_product_summary_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    forecast_run_id bigint NOT NULL REFERENCES forecast_runs(forecast_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    method text NOT NULL CHECK (method IN ('prophet', 'tsb', 'seasonal_naive_7d', 'moving_average_28d', 'none', 'legacy')),
    diagnostic_status text NOT NULL CHECK (diagnostic_status IN ('ok', 'fallback', 'insufficient_history', 'failed', 'legacy')),
    diagnostic_message text,
    history_days integer NOT NULL DEFAULT 0 CHECK (history_days >= 0),
    nonzero_days integer NOT NULL DEFAULT 0 CHECK (nonzero_days >= 0),
    score_metric text,
    backtest_score numeric(16,6),
    candidate_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    forecast_total numeric(14,3) CHECK (forecast_total IS NULL OR forecast_total >= 0),
    confidence_lower_total numeric(14,3) CHECK (confidence_lower_total IS NULL OR confidence_lower_total >= 0),
    confidence_upper_total numeric(14,3) CHECK (confidence_upper_total IS NULL OR confidence_upper_total >= 0),
    usable_stock numeric(14,3) CHECK (usable_stock IS NULL OR usable_stock >= 0),
    production_gap numeric(14,3),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (forecast_run_id, product_id),
    CHECK (confidence_lower_total IS NULL OR confidence_upper_total IS NULL OR confidence_upper_total >= confidence_lower_total)
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

CREATE TABLE order_system_messages (
    order_system_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reseller_account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    event_type text NOT NULL CHECK (event_type IN ('approved', 'rejected', 'fulfilled', 'cancelled', 'refund_pending', 'refund_processing', 'refund_completed', 'refund_failed')),
    title text NOT NULL CHECK (btrim(title) <> ''),
    message text NOT NULL CHECK (btrim(message) <> ''),
    rejection_reason text,
    dedupe_key text NOT NULL UNIQUE,
    order_refund_attempt_id bigint REFERENCES order_refund_attempts(order_refund_attempt_id) ON UPDATE CASCADE ON DELETE SET NULL,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (event_type = 'rejected' AND char_length(btrim(rejection_reason)) BETWEEN 5 AND 400)
        OR (event_type <> 'rejected' AND rejection_reason IS NULL)
    )
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
    purpose text NOT NULL DEFAULT 'activation' CHECK (purpose IN ('activation', 'password_reset')),
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
        'production_output', 'direct_finished_receipt', 'sale_fulfillment',
        'owner_correction'
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
    IF OLD.status IN ('rejected', 'fulfilled', 'cancelled', 'expired') THEN RAISE EXCEPTION 'Order status % is terminal.', OLD.status; END IF;
    IF OLD.order_type = 'reseller'
       AND OLD.status IN ('to_pay', 'pending')
       AND NEW.status = 'cancelled'
    THEN
        IF OLD.payment_transaction_number IS NOT NULL
           OR OLD.payment_transaction_key IS NOT NULL
           OR OLD.payment_paid_at IS NOT NULL
           OR OLD.payment_amount IS NOT NULL
           OR OLD.payment_confirmed_at IS NOT NULL
           OR EXISTS (SELECT 1 FROM order_payment_proofs WHERE order_id = OLD.order_id)
        THEN
            RAISE EXCEPTION 'Orders with a submitted payment cannot be cancelled.';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'to_pay' AND NEW.status IN ('pending', 'expired') THEN RETURN NEW; END IF;
    IF OLD.status = 'pending' AND NEW.status = 'approved' THEN
        IF OLD.order_type = 'reseller' AND NOT EXISTS (SELECT 1 FROM order_payment_proofs WHERE order_id = OLD.order_id) THEN
            RAISE EXCEPTION 'Proof of payment is required before approval.';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'approved' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    IF OLD.status = 'pending' AND NEW.status = 'rejected' THEN RETURN NEW; END IF;
    IF OLD.order_type = 'walk_in' AND OLD.status = 'pending' AND NEW.status = 'fulfilled' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid order status transition: % -> %.', OLD.status, NEW.status;
END $$;
CREATE TRIGGER trg_enforce_order_status_transition
BEFORE UPDATE OF status ON orders FOR EACH ROW EXECUTE FUNCTION enforce_order_status_transition();

CREATE OR REPLACE FUNCTION enforce_order_refund_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF OLD.status = 'completed' THEN RAISE EXCEPTION 'Completed refunds are terminal.'; END IF;
    IF OLD.status = 'pending' AND NEW.status = 'processing' THEN RETURN NEW; END IF;
    IF OLD.status = 'processing' AND NEW.status IN ('completed', 'failed') THEN RETURN NEW; END IF;
    IF OLD.status = 'failed' AND NEW.status = 'processing' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Invalid refund status transition: % -> %.', OLD.status, NEW.status;
END $$;
CREATE TRIGGER trg_enforce_order_refund_status_transition
BEFORE UPDATE OF status ON order_refunds FOR EACH ROW EXECUTE FUNCTION enforce_order_refund_status_transition();

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
CREATE TRIGGER trg_protect_order_refund_audit_fields
BEFORE UPDATE ON order_refunds FOR EACH ROW EXECUTE FUNCTION protect_order_refund_audit_fields();

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
CREATE TRIGGER trg_protect_finished_refund_attempt
BEFORE UPDATE ON order_refund_attempts FOR EACH ROW EXECUTE FUNCTION protect_finished_refund_attempt();

CREATE OR REPLACE FUNCTION protect_order_refund_message()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Refund conversation messages are append-only.';
END $$;

CREATE TRIGGER trg_protect_order_refund_message
BEFORE UPDATE OR DELETE ON order_refund_messages
FOR EACH ROW EXECUTE FUNCTION protect_order_refund_message();

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
          AND a.account_type = 'team_leader'
          AND a.team_leader_role IN ('sales', 'sales-supervisor')
          AND a.is_active = true
    ) THEN RAISE EXCEPTION 'Reseller orders require an active sales team leader ownership snapshot.'; END IF;
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
    IF order_status NOT IN ('to_pay', 'pending') THEN
        RAISE EXCEPTION 'Payment proof uploads are allowed only for orders awaiting payment or review.';
    END IF;
    SELECT count(*) INTO proof_count FROM order_payment_proofs WHERE order_id = NEW.order_id;
    IF proof_count >= 3 THEN RAISE EXCEPTION 'An order can have at most three payment proofs.'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_enforce_payment_proof_limit
BEFORE INSERT ON order_payment_proofs FOR EACH ROW EXECUTE FUNCTION enforce_payment_proof_limit();

CREATE OR REPLACE FUNCTION require_reseller_order_payment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND NEW.status IN ('pending', 'approved', 'fulfilled') AND (
        NEW.payment_transaction_number IS NULL
        OR NEW.payment_transaction_key IS NULL
        OR NEW.payment_paid_at IS NULL
        OR NEW.payment_amount IS NULL
        OR NEW.payment_confirmed_at IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM order_payment_proofs WHERE order_id = NEW.order_id
        )
    ) THEN
        RAISE EXCEPTION 'Reseller orders require confirmed payment details and proof before commit.';
    END IF;
    RETURN NEW;
END $$;
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
CREATE CONSTRAINT TRIGGER trg_require_timed_order_reservations
AFTER INSERT OR UPDATE OF status ON orders DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_timed_order_reservations();

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
CREATE TRIGGER trg_prevent_reused_payment_proof
BEFORE INSERT ON order_payment_proofs FOR EACH ROW EXECUTE FUNCTION prevent_reused_payment_proof();

CREATE UNIQUE INDEX ux_accounts_email_lower ON accounts (lower(email));
CREATE INDEX ix_accounts_team_leader_role ON accounts (team_leader_role)
    WHERE account_type = 'team_leader';
CREATE UNIQUE INDEX ux_resellers_email_lower ON resellers (lower(email));
CREATE UNIQUE INDEX ux_inventory_items_type_name_lower ON inventory_items (item_type, lower(name));
CREATE INDEX ix_inventory_items_type_name ON inventory_items (item_type, name);
CREATE INDEX ix_inventory_batches_fefo ON inventory_batches (item_id, expiry_date, quantity_available)
    WHERE quality_status = 'approved' AND quantity_available > 0;
CREATE INDEX ix_orders_reseller_status ON orders (reseller_id, status);
CREATE UNIQUE INDEX ux_orders_reseller_to_pay
    ON orders (reseller_id)
    WHERE order_type = 'reseller' AND status = 'to_pay';
CREATE INDEX ix_orders_to_pay_due ON orders (payment_due_at, order_id)
    WHERE status = 'to_pay';
CREATE UNIQUE INDEX ux_orders_reseller_payment_transaction
    ON orders (reseller_id, payment_transaction_key)
    WHERE order_type = 'reseller' AND payment_transaction_key IS NOT NULL;
CREATE INDEX ix_order_payment_proofs_order ON order_payment_proofs (order_id, uploaded_at DESC);
CREATE INDEX ix_order_payment_proofs_checksum ON order_payment_proofs (checksum_sha256, order_id);
CREATE UNIQUE INDEX ux_order_refund_attempt_transaction ON order_refund_attempts (refund_transaction_key) WHERE refund_transaction_key IS NOT NULL;
CREATE INDEX ix_order_refunds_status_requested ON order_refunds (status, requested_at, order_refund_id);
CREATE INDEX ix_order_refunds_supervisor_status ON order_refunds (assigned_supervisor_account_id, status, requested_at);
CREATE INDEX ix_order_refund_attempts_refund ON order_refund_attempts (order_refund_id, attempt_number);
CREATE INDEX ix_order_refund_messages_refund_created ON order_refund_messages (order_refund_id, created_at, order_refund_message_id);
CREATE INDEX ix_order_stock_reservations_product ON order_stock_reservations (product_id, order_id);
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
CREATE INDEX ix_order_system_messages_reseller_created ON order_system_messages (reseller_account_id, created_at DESC, order_system_message_id DESC);
CREATE INDEX ix_order_system_messages_order ON order_system_messages (order_id, event_type);
CREATE INDEX ix_web_sessions_account ON web_sessions (account_id);
CREATE INDEX ix_web_sessions_expiry ON web_sessions (expires_at);
CREATE INDEX ix_security_events_window ON security_events (event_type, subject_key, created_at DESC);
CREATE UNIQUE INDEX ux_account_activation_pending ON account_activation_tokens (account_id) WHERE consumed_at IS NULL;
CREATE INDEX ix_notification_outbox_due ON notification_outbox (next_attempt_at, outbox_id)
    WHERE sent_at IS NULL AND attempt_count < 8;
CREATE INDEX ix_orders_team_leader_date ON orders (team_leader_account_id, order_date DESC);
CREATE INDEX ix_forecast_runs_due ON forecast_runs (next_attempt_at, forecast_run_id)
    WHERE status = 'queued' AND attempt_count < 3;
CREATE UNIQUE INDEX ux_forecast_runs_one_active ON forecast_runs ((1))
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_forecast_product_priority ON forecast_product_summaries (forecast_run_id, production_gap DESC NULLS LAST);
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

CREATE TABLE chat_conversations (
    conversation_id uuid PRIMARY KEY,
    display_name text NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 2 AND 80),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'active', 'awaiting_contact', 'follow_up', 'closed', 'cancelled')),
    escalation_reason text NOT NULL DEFAULT 'requested',
    assigned_team_leader_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    fallback_contact_type text CHECK (fallback_contact_type IS NULL OR fallback_contact_type IN ('email', 'phone')),
    fallback_contact text,
    privacy_consent_at timestamptz NOT NULL DEFAULT now(),
    queued_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    last_visitor_at timestamptz,
    last_leader_at timestamptz,
    visitor_typing_until timestamptz,
    leader_typing_until timestamptz,
    inquiry_form_requested_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((fallback_contact_type IS NULL) = (fallback_contact IS NULL))
);

CREATE TABLE chat_messages (
    chat_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE CASCADE,
    client_message_id uuid NOT NULL,
    sender_type text NOT NULL CHECK (sender_type IN ('visitor', 'team_leader', 'system')),
    sender_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    content text NOT NULL CHECK (char_length(btrim(content)) BETWEEN 1 AND 500),
    publication_status text NOT NULL DEFAULT 'pending' CHECK (publication_status IN ('pending', 'published', 'failed')),
    publish_attempts integer NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    last_publish_error text,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, client_message_id)
);

CREATE TABLE reseller_conversations (
    conversation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reseller_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    team_leader_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    last_message_at timestamptz,
    reseller_deleted_at timestamptz,
    team_leader_deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reseller_account_id, team_leader_account_id)
);

CREATE TABLE reseller_messages (
    reseller_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id bigint NOT NULL
        REFERENCES reseller_conversations(conversation_id) ON UPDATE CASCADE ON DELETE CASCADE,
    client_message_id uuid NOT NULL,
    sender_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    content text NOT NULL CHECK (char_length(btrim(content)) BETWEEN 1 AND 500),
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, client_message_id)
);

CREATE TABLE chat_service_ratings (
    conversation_id uuid PRIMARY KEY
        REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE CASCADE,
    team_leader_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE team_leader_presence (
    account_id bigint PRIMARY KEY REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    availability text NOT NULL DEFAULT 'offline' CHECK (availability IN ('available', 'busy', 'offline')),
    active_conversation_id uuid REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE SET NULL,
    heartbeat_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rate_limit_buckets (
    event_type text NOT NULL,
    subject_hash text NOT NULL,
    window_seconds integer NOT NULL CHECK (window_seconds > 0),
    bucket_started_at timestamptz NOT NULL,
    request_count integer NOT NULL DEFAULT 1 CHECK (request_count > 0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (event_type, subject_hash, window_seconds, bucket_started_at)
);

ALTER TABLE inquiries ADD COLUMN chat_conversation_id uuid UNIQUE
    REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE SET NULL;

CREATE INDEX ix_chat_conversations_queue ON chat_conversations (status, queued_at);
CREATE INDEX ix_chat_conversations_leader_status ON chat_conversations (assigned_team_leader_account_id, status, updated_at DESC);
CREATE INDEX ix_chat_messages_conversation_id ON chat_messages (conversation_id, chat_message_id);
CREATE INDEX ix_chat_messages_pending ON chat_messages (publication_status, created_at) WHERE publication_status <> 'published';
CREATE INDEX ix_reseller_conversations_leader_activity ON reseller_conversations (team_leader_account_id, last_message_at DESC NULLS LAST);
CREATE INDEX ix_reseller_messages_conversation ON reseller_messages (conversation_id, reseller_message_id);
CREATE INDEX ix_reseller_messages_unread ON reseller_messages (conversation_id, sender_account_id) WHERE read_at IS NULL;
CREATE INDEX ix_chat_service_ratings_leader_created ON chat_service_ratings (team_leader_account_id, created_at DESC);
CREATE INDEX ix_team_leader_presence_available ON team_leader_presence (availability, heartbeat_at DESC);
CREATE INDEX ix_rate_limit_buckets_expiry ON rate_limit_buckets (expires_at);

ALTER TABLE activity_logs
    ADD COLUMN actor_role text,
    ADD COLUMN reason text,
    ADD COLUMN before_state jsonb,
    ADD COLUMN after_state jsonb,
    ADD COLUMN outcome text NOT NULL DEFAULT 'success' CHECK (outcome IN ('success', 'failure')),
    ADD COLUMN request_id text,
    ADD COLUMN source_key text;

CREATE TABLE owner_step_up_challenges (
    owner_step_up_challenge_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    otp_hash text NOT NULL CHECK (btrim(otp_hash) <> ''),
    action_scope text NOT NULL DEFAULT 'owner.sensitive' CHECK (btrim(action_scope) <> ''),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_owner_step_up_pending ON owner_step_up_challenges (account_id, created_at DESC)
    WHERE consumed_at IS NULL;

CREATE TABLE business_settings (
    setting_key text PRIMARY KEY CHECK (btrim(setting_key) <> ''),
    setting_value jsonb NOT NULL,
    updated_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO business_settings (setting_key, setting_value) VALUES
    ('order.payment_window_minutes', '15'::jsonb),
    ('inventory.near_expiry_days', '14'::jsonb),
    ('inventory.critical_expiry_days', '2'::jsonb),
    ('forecast.default_horizon_days', '7'::jsonb),
    ('notifications.owner_critical_alerts', 'true'::jsonb),
    ('business.contact_email', '""'::jsonb),
    ('business.contact_phone', '""'::jsonb),
    ('business.contact_address', '""'::jsonb),
    ('business.hours', '""'::jsonb);

CREATE TABLE system_status_reports (
    component text PRIMARY KEY CHECK (btrim(component) <> ''),
    status text NOT NULL CHECK (status IN ('ok', 'warning', 'error', 'unknown')),
    detail text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    checked_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE forecast_runs
    DROP CONSTRAINT forecast_runs_status_check,
    ADD CONSTRAINT forecast_runs_status_check
        CHECK (status IN ('queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'cancelling', 'cancelled')),
    ADD COLUMN retry_of_forecast_run_id bigint REFERENCES forecast_runs(forecast_run_id) ON UPDATE CASCADE ON DELETE SET NULL,
    ADD COLUMN cancel_requested_at timestamptz;

CREATE INDEX ix_accounts_type_active ON accounts (account_type, is_active, account_id);
CREATE INDEX ix_inquiries_owner_queue ON inquiries (status, created_at DESC);
CREATE INDEX ix_orders_owner_queue ON orders (status, order_date DESC);
CREATE INDEX ix_activity_logs_governance ON activity_logs (outcome, created_at DESC);
CREATE INDEX ix_activity_logs_actor_role ON activity_logs (actor_role, created_at DESC);
CREATE INDEX ix_activity_logs_entity_action ON activity_logs (entity_type, action, created_at DESC);
CREATE INDEX ix_resellers_owner_assignment ON resellers (team_leader_account_id, reseller_status);
CREATE INDEX ix_chat_conversations_owner_status ON chat_conversations (status, updated_at DESC);
CREATE UNIQUE INDEX ux_forecast_runs_single_retry
    ON forecast_runs (retry_of_forecast_run_id) WHERE retry_of_forecast_run_id IS NOT NULL;



-- Batch traceability, expired holding stock, and reseller returns.
ALTER TABLE inventory_movements DROP CONSTRAINT IF EXISTS inventory_movements_movement_type_check;
ALTER TABLE inventory_movements ADD CONSTRAINT inventory_movements_movement_type_check CHECK (movement_type IN (
 'opening_balance','raw_receipt','production_consumption','production_output','direct_finished_receipt',
 'sale_fulfillment','owner_correction','expired_pullout','replacement_release'));
CREATE TABLE IF NOT EXISTS fulfillment_allocations (
    allocation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_item_id bigint NOT NULL REFERENCES order_items ON DELETE RESTRICT,
    batch_id bigint NOT NULL REFERENCES inventory_batches ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    verified_by_account_id bigint REFERENCES accounts,
    verification_note text,
    UNIQUE (order_item_id, batch_id)
);
-- Only recover mappings with one order line and a complete movement history.
INSERT INTO fulfillment_allocations (order_item_id, batch_id, quantity, verification_note)
SELECT oi.order_item_id, im.affected_batch_id, SUM(-im.quantity_delta), 'Recovered from fulfillment movements'
FROM inventory_movements im JOIN order_items oi ON oi.order_id = im.order_id AND oi.product_id = im.item_id
WHERE im.movement_type = 'sale_fulfillment' AND im.affected_batch_id IS NOT NULL
  AND (SELECT COUNT(*) FROM order_items x WHERE x.order_id = oi.order_id AND x.product_id = oi.product_id) = 1
  AND oi.quantity = (SELECT SUM(-m.quantity_delta) FROM inventory_movements m
                    WHERE m.order_id = oi.order_id AND m.item_id = oi.product_id AND m.movement_type = 'sale_fulfillment')
GROUP BY oi.order_item_id, im.affected_batch_id
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS product_returns (
    return_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders ON DELETE RESTRICT,
    resolution text NOT NULL CHECK (resolution IN ('refund','replace')),
    status text NOT NULL DEFAULT 'submitted' CHECK (status IN
      ('submitted','awaiting_receipt','discrepancy','settlement_pending','completed','rejected')),
    remarks text NOT NULL CHECK (char_length(btrim(remarks)) BETWEEN 5 AND 1000),
    requested_by_account_id bigint NOT NULL REFERENCES accounts,
    approved_by_account_id bigint REFERENCES accounts,
    credit numeric(12,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    quote_revision integer NOT NULL DEFAULT 0,
    accepted_revision integer,
    replacement_total numeric(12,2) NOT NULL DEFAULT 0 CHECK (replacement_total >= 0),
    payment_verified_by_account_id bigint REFERENCES accounts,
    payment_verified_at timestamptz,
    payment_reference text,
    payment_amount numeric(12,2) CHECK (payment_amount > 0),
    replacement_released_at timestamptz,
    verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_payment_reference ON product_returns(payment_reference) WHERE payment_reference IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_returns_order_status ON product_returns(order_id,status);
CREATE TABLE IF NOT EXISTS product_return_lines (
    return_line_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    allocation_id bigint NOT NULL REFERENCES fulfillment_allocations ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    received_quantity numeric(12,3) NOT NULL DEFAULT 0 CHECK (received_quantity >= 0 AND received_quantity <= quantity AND received_quantity = trunc(received_quantity)),
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE(return_id,allocation_id)
);
CREATE INDEX IF NOT EXISTS ix_return_lines_allocation ON product_return_lines(allocation_id);
CREATE TABLE IF NOT EXISTS product_return_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    actor_account_id bigint REFERENCES accounts,
    action text NOT NULL,
    remarks text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS replacement_quote_lines (
    quote_line_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    revision integer NOT NULL,
    product_id bigint NOT NULL REFERENCES inventory_items,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE(return_id,revision,product_id)
);
CREATE TABLE IF NOT EXISTS replacement_stock_reservations (
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    batch_id bigint NOT NULL REFERENCES inventory_batches ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    PRIMARY KEY(return_id,batch_id)
);
CREATE TABLE IF NOT EXISTS replacement_releases (
    release_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    batch_id bigint NOT NULL REFERENCES inventory_batches,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    released_by_account_id bigint NOT NULL REFERENCES accounts,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(return_id,batch_id)
);
CREATE OR REPLACE VIEW all_stock_reservations AS
SELECT order_id, product_id, quantity FROM order_stock_reservations
UNION ALL
SELECT NULL::bigint AS order_id, b.item_id, r.quantity FROM replacement_stock_reservations r
JOIN inventory_batches b ON b.batch_id = r.batch_id
WHERE b.quality_status = 'approved' AND b.expiry_date > (now() AT TIME ZONE 'Asia/Manila')::date;

CREATE TABLE IF NOT EXISTS return_attachments (
    attachment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns,
    purpose text NOT NULL CHECK (purpose IN ('evidence','payment')),
    revision integer NOT NULL DEFAULT 0,
    filename text NOT NULL,
    content_type text NOT NULL CHECK(content_type IN ('image/jpeg','image/png','image/webp')),
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK(size_bytes > 0 AND size_bytes <= 5242880),
    checksum_sha256 text NOT NULL,
    uploaded_by_account_id bigint NOT NULL REFERENCES accounts,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(return_id,purpose,revision,checksum_sha256)
);
CREATE TABLE IF NOT EXISTS expired_pullouts (
    pullout_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL UNIQUE REFERENCES inventory_batches,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS expired_holdings (
    holding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES inventory_items,
    batch_id bigint REFERENCES inventory_batches,
    return_line_id bigint REFERENCES product_return_lines,
    source text NOT NULL CHECK(source IN ('pullout','return','expired_receipt')),
    source_batch_code text NOT NULL,
    expiry_date date NOT NULL,
    quantity_received numeric(12,3) NOT NULL CHECK(quantity_received > 0 AND quantity_received = trunc(quantity_received)),
    quantity_remaining numeric(12,3) NOT NULL CHECK(quantity_remaining >= 0 AND quantity_remaining <= quantity_received AND quantity_remaining = trunc(quantity_remaining)),
    location text NOT NULL CHECK(char_length(btrim(location)) BETWEEN 2 AND 200),
    created_by_account_id bigint NOT NULL REFERENCES accounts,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS expired_stock_movements (
    movement_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    holding_id bigint NOT NULL REFERENCES expired_holdings,
    action text NOT NULL CHECK(action IN ('pullout','return_receipt','expired_receipt','disposal')),
    quantity numeric(12,3) NOT NULL CHECK(quantity <> 0 AND quantity = trunc(quantity)),
    actor_account_id bigint NOT NULL REFERENCES accounts,
    remarks text NOT NULL CHECK(char_length(btrim(remarks)) BETWEEN 5 AND 1000),
    disposal_method text,
    operation_key uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS return_id bigint REFERENCES product_returns;
ALTER TABLE order_refunds DROP CONSTRAINT IF EXISTS order_refunds_order_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_rejection_order_refund ON order_refunds(order_id) WHERE return_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_case_refund ON order_refunds(return_id) WHERE return_id IS NOT NULL;

UPDATE business_settings SET setting_value = '14'::jsonb
WHERE setting_key = 'inventory.near_expiry_days' AND (setting_value::text)::integer < 14;

CREATE INDEX IF NOT EXISTS ix_allocations_batch ON fulfillment_allocations(batch_id);
CREATE INDEX IF NOT EXISTS ix_returns_status_updated ON product_returns(status,updated_at);
CREATE INDEX IF NOT EXISTS ix_return_events_case ON product_return_events(return_id,event_id);
CREATE INDEX IF NOT EXISTS ix_holdings_product ON expired_holdings(product_id) WHERE quantity_remaining>0;
CREATE INDEX IF NOT EXISTS ix_replacement_reservations_batch ON replacement_stock_reservations(batch_id);


ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS operation_key uuid;
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_request_operation ON product_returns(operation_key) WHERE operation_key IS NOT NULL;
ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS operation_key uuid;
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_event_operation ON product_return_events(operation_key) WHERE operation_key IS NOT NULL;
ALTER TABLE replacement_quote_lines ADD COLUMN IF NOT EXISTS line_total numeric(12,2);
UPDATE replacement_quote_lines SET line_total=round(quantity*unit_price,2) WHERE line_total IS NULL;
ALTER TABLE replacement_quote_lines ALTER COLUMN line_total SET NOT NULL;
ALTER TABLE replacement_quote_lines DROP CONSTRAINT IF EXISTS replacement_quote_lines_total_check;
ALTER TABLE replacement_quote_lines ADD CONSTRAINT replacement_quote_lines_total_check CHECK(line_total>=0);


-- Explicit receipt verification and an auditable sales-to-custody settlement chain.
ALTER TABLE product_returns DROP CONSTRAINT IF EXISTS product_returns_status_check;
ALTER TABLE product_returns ADD CONSTRAINT product_returns_status_check CHECK (status IN
 ('submitted','awaiting_receipt','awaiting_inventory_verification','discrepancy','settlement_pending','completed','rejected'));
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS verified_by_account_id bigint REFERENCES accounts;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS verification_remarks text;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS request_hash text;

CREATE TABLE IF NOT EXISTS product_return_receipts (
 receipt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
 operation_key uuid NOT NULL UNIQUE,
 request_hash text,
 received_by_account_id bigint NOT NULL REFERENCES accounts,
 location text NOT NULL,
 remarks text NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS product_return_receipt_lines (
 receipt_line_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 receipt_id bigint NOT NULL REFERENCES product_return_receipts ON DELETE RESTRICT,
 return_line_id bigint NOT NULL REFERENCES product_return_lines ON DELETE RESTRICT,
 holding_id bigint NOT NULL UNIQUE REFERENCES expired_holdings ON DELETE RESTRICT,
 movement_id bigint NOT NULL UNIQUE REFERENCES expired_stock_movements ON DELETE RESTRICT,
 quantity numeric(12,3) NOT NULL CHECK(quantity>0 AND quantity=trunc(quantity)),
 UNIQUE(receipt_id,return_line_id)
);
CREATE INDEX IF NOT EXISTS ix_return_receipts_case ON product_return_receipts(return_id,receipt_id);
CREATE INDEX IF NOT EXISTS ix_return_receipt_lines_line ON product_return_receipt_lines(return_line_id);

-- Each existing custody receipt independently proves one delivery. Do not infer
-- grouping or a final verifier from old remarks or automatic verification dates.
INSERT INTO product_return_receipts(return_id,operation_key,received_by_account_id,location,remarks,received_at)
SELECT l.return_id,m.operation_key,m.actor_account_id,h.location,m.remarks,m.created_at
FROM expired_stock_movements m JOIN expired_holdings h USING(holding_id)
JOIN product_return_lines l ON l.return_line_id=h.return_line_id
JOIN fulfillment_allocations a ON a.allocation_id=l.allocation_id
JOIN inventory_batches b ON b.batch_id=a.batch_id
JOIN order_items oi ON oi.order_item_id=a.order_item_id
JOIN product_returns r ON r.return_id=l.return_id
WHERE m.action='return_receipt' AND h.source='return' AND h.batch_id=a.batch_id
 AND h.product_id=oi.product_id AND oi.order_id=r.order_id
 AND h.source_batch_code=b.batch_code AND h.expiry_date=b.expiry_date
 AND m.quantity=h.quantity_received
ON CONFLICT(operation_key) DO NOTHING;
INSERT INTO product_return_receipt_lines(receipt_id,return_line_id,holding_id,movement_id,quantity)
SELECT r.receipt_id,h.return_line_id,h.holding_id,m.movement_id,m.quantity
FROM product_return_receipts r JOIN expired_stock_movements m ON m.operation_key=r.operation_key
JOIN expired_holdings h USING(holding_id) JOIN product_return_lines l ON l.return_line_id=h.return_line_id
WHERE m.action='return_receipt' AND l.return_id=r.return_id
ON CONFLICT DO NOTHING;

ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS refund_id bigint REFERENCES order_refunds;
ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS refund_attempt_id bigint REFERENCES order_refund_attempts;
ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS amount numeric(12,2);
ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS request_hash text;
CREATE TABLE IF NOT EXISTS refund_operations (
 operation_key uuid PRIMARY KEY,
 refund_id bigint NOT NULL REFERENCES order_refunds ON DELETE RESTRICT,
 actor_account_id bigint NOT NULL REFERENCES accounts,
 action text NOT NULL CHECK(action IN ('start','fail','complete')),
 request_hash text NOT NULL,
 attempt_id bigint NOT NULL REFERENCES order_refund_attempts ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_refund_proof_checksum ON order_refund_proofs(checksum_sha256);

CREATE OR REPLACE FUNCTION protect_refund_trace_records() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Receipt and settlement audit records cannot be changed or deleted.';
END $$;
DROP TRIGGER IF EXISTS immutable_return_receipts ON product_return_receipts;
CREATE TRIGGER immutable_return_receipts BEFORE UPDATE OR DELETE OR TRUNCATE ON product_return_receipts
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
DROP TRIGGER IF EXISTS immutable_return_receipt_lines ON product_return_receipt_lines;
CREATE TRIGGER immutable_return_receipt_lines BEFORE UPDATE OR DELETE OR TRUNCATE ON product_return_receipt_lines
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
DROP TRIGGER IF EXISTS immutable_refund_operations ON refund_operations;
CREATE TRIGGER immutable_refund_operations BEFORE UPDATE OR DELETE OR TRUNCATE ON refund_operations
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();

CREATE OR REPLACE VIEW product_return_reconciliation AS
SELECT r.return_id,array_remove(ARRAY[
 CASE WHEN r.credit IS DISTINCT FROM (SELECT SUM(l.quantity*l.unit_price) FROM product_return_lines l WHERE l.return_id=r.return_id)
 THEN 'Return credit does not match original line totals.' END,
 CASE WHEN EXISTS(SELECT 1 FROM product_return_lines l WHERE l.return_id=r.return_id AND
   l.received_quantity<>COALESCE((SELECT SUM(x.quantity) FROM product_return_receipt_lines x
    JOIN product_return_receipts h USING(receipt_id) WHERE x.return_line_id=l.return_line_id AND h.return_id=r.return_id),0))
 THEN 'Received goods are missing matching receipt records.' END,
 CASE WHEN EXISTS(SELECT 1 FROM expired_holdings h JOIN product_return_lines l USING(return_line_id)
   WHERE l.return_id=r.return_id AND (h.quantity_remaining<>h.quantity_received+COALESCE(
    (SELECT SUM(m.quantity) FROM expired_stock_movements m WHERE m.holding_id=h.holding_id AND m.action='disposal'),0)
    OR NOT EXISTS(SELECT 1 FROM product_return_receipt_lines x WHERE x.holding_id=h.holding_id)))
 THEN 'Holding balance or receipt linkage requires review.' END,
 CASE WHEN f.amount IS NOT NULL AND f.amount<>CASE WHEN r.resolution='refund' THEN r.credit ELSE GREATEST(0,r.credit-r.replacement_total) END
 THEN 'Refund amount does not match verified credit.' END,
 CASE WHEN r.verified_at IS NOT NULL AND (r.approved_by_account_id IS NULL OR EXISTS(SELECT 1 FROM product_return_lines l WHERE l.return_id=r.return_id AND l.quantity<>l.received_quantity))
 THEN 'Inventory verification is missing complete approved receipts.' END,
 CASE WHEN r.verified_at IS NOT NULL AND f.order_refund_id IS NULL
  AND ((r.resolution='refund' AND r.credit>0) OR (r.resolution='replace' AND r.replacement_released_at IS NOT NULL AND r.credit>r.replacement_total))
 THEN 'Verified settlement is missing its refund record.' END,
 CASE WHEN r.status='completed' AND (r.verified_at IS NULL OR f.status IS DISTINCT FROM 'completed' AND f.order_refund_id IS NOT NULL
  OR r.resolution='replace' AND r.replacement_released_at IS NULL)
 THEN 'Completed case has unfinished required steps.' END
],NULL) AS flags
FROM product_returns r LEFT JOIN order_refunds f ON f.return_id=r.return_id;


-- Preserve submitted figures and order membership while exposing refund adjustments.
ALTER TABLE sales_reports ADD COLUMN IF NOT EXISTS refund_separation_version smallint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS sales_report_orders (
 sales_report_id bigint NOT NULL REFERENCES sales_reports ON DELETE RESTRICT,
 order_id bigint NOT NULL REFERENCES orders ON DELETE RESTRICT,
 gross_sales numeric(12,2) NOT NULL CHECK(gross_sales>=0),
 refunded_at_submission numeric(12,2) NOT NULL CHECK(refunded_at_submission>=0 AND refunded_at_submission<=gross_sales),
 PRIMARY KEY(sales_report_id,order_id)
);
CREATE TABLE IF NOT EXISTS sales_report_refunds (
 sales_report_id bigint NOT NULL REFERENCES sales_reports ON DELETE RESTRICT,
 order_refund_id bigint NOT NULL REFERENCES order_refunds ON DELETE RESTRICT,
 amount numeric(12,2) NOT NULL CHECK(amount>0),
 PRIMARY KEY(sales_report_id,order_refund_id)
);
CREATE INDEX IF NOT EXISTS ix_report_order_membership ON sales_report_orders(order_id);
-- Legacy membership is recovered only when its original gross and order count match.
WITH candidates AS (
 SELECT sr.sales_report_id,o.order_id,o.total_amount gross_sales,sr.total_sales,sr.total_orders,
 COALESCE((SELECT SUM(f.amount) FROM order_refunds f WHERE f.order_id=o.order_id AND f.status='completed' AND f.completed_at<=sr.submitted_at),0) refunded
 FROM sales_reports sr JOIN orders o ON
  ((o.order_type='walk_in' AND o.created_by_account_id=sr.submitted_by_account_id)
   OR (o.order_type='reseller' AND o.team_leader_account_id=sr.submitted_by_account_id))
 WHERE sr.report_source='team_leader' AND sr.refund_separation_version=0 AND o.status='fulfilled'
 AND (COALESCE(o.fulfilled_at,o.order_date) AT TIME ZONE 'Asia/Manila')::date BETWEEN sr.period_start AND sr.period_end
 AND COALESCE(o.fulfilled_at,o.order_date::timestamptz)<=sr.submitted_at
), proven AS (
 SELECT sales_report_id FROM candidates GROUP BY sales_report_id
 HAVING SUM(gross_sales)=MAX(total_sales) AND COUNT(*)=MAX(total_orders) AND bool_and(refunded<=gross_sales)
)
INSERT INTO sales_report_orders(sales_report_id,order_id,gross_sales,refunded_at_submission)
SELECT c.sales_report_id,c.order_id,c.gross_sales,c.refunded FROM candidates c JOIN proven USING(sales_report_id)
ON CONFLICT DO NOTHING;
INSERT INTO sales_report_refunds(sales_report_id,order_refund_id,amount)
SELECT s.sales_report_id,f.order_refund_id,f.amount FROM sales_report_orders s
JOIN sales_reports r USING(sales_report_id) JOIN order_refunds f ON f.order_id=s.order_id
WHERE f.status='completed' AND f.completed_at<=r.submitted_at AND r.refund_separation_version=0
ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW sales_report_balances AS
SELECT sr.sales_report_id,COALESCE(s.gross_sales,0) gross_sales,
 COALESCE(s.completed_refunds,0) refunded_sales,COALESCE(s.retained_sales,0) retained_sales,
 COALESCE(s.refunded_at_submission,0) refunded_at_submission,
 GREATEST(0,COALESCE(s.completed_refunds,0)-COALESCE(s.refunded_at_submission,0)) late_refund_adjustment,
 COALESCE(s.retained_orders,0) retained_orders,
 CASE WHEN sr.refund_separation_version=0 THEN
  COALESCE(s.gross_sales,0)<>sr.total_sales OR COALESCE(s.mapped_orders,0)<>sr.total_orders
 ELSE COALESCE(s.gross_sales,0)-COALESCE(s.refunded_at_submission,0)<>sr.total_sales
 END OR COALESCE(s.excess_refund,false) reconciliation_required
FROM sales_reports sr LEFT JOIN LATERAL (
 SELECT SUM(x.gross_sales) gross_sales,SUM(x.refunded_at_submission) refunded_at_submission,
 SUM(COALESCE(f.amount,0)) completed_refunds,
 SUM(GREATEST(0,x.gross_sales-COALESCE(f.amount,0))) retained_sales,
 COUNT(*) mapped_orders,COUNT(*) FILTER(WHERE x.gross_sales>COALESCE(f.amount,0)) retained_orders,
 bool_or(COALESCE(f.amount,0)>x.gross_sales) excess_refund
 FROM sales_report_orders x LEFT JOIN LATERAL (
  SELECT SUM(amount) amount FROM order_refunds WHERE order_id=x.order_id AND status='completed'
 ) f ON true WHERE x.sales_report_id=sr.sales_report_id
) s ON true WHERE sr.report_source='team_leader';

DROP TRIGGER IF EXISTS immutable_sales_report_orders ON sales_report_orders;
CREATE TRIGGER immutable_sales_report_orders BEFORE UPDATE OR DELETE OR TRUNCATE ON sales_report_orders
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
DROP TRIGGER IF EXISTS immutable_sales_report_refunds ON sales_report_refunds;
CREATE TRIGGER immutable_sales_report_refunds BEFORE UPDATE OR DELETE OR TRUNCATE ON sales_report_refunds
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();

COMMIT;
