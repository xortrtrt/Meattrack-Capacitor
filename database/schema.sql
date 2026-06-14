BEGIN;

-- MEATTRACK PostgreSQL schema
-- Source of truth for the revised ERD.

CREATE TABLE departments (
    department_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_name text NOT NULL UNIQUE,
    description text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE employees (
    employee_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_id bigint NOT NULL REFERENCES departments(department_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    name text NOT NULL,
    position text NOT NULL,
    employment_status text NOT NULL DEFAULT 'active'
        CHECK (employment_status IN ('active', 'inactive')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    account_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_type text NOT NULL
        CHECK (account_type IN ('owner', 'team_leader', 'reseller')),
    employee_id bigint REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    reseller_id bigint,
    name text NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (account_type = 'owner' AND employee_id IS NULL AND reseller_id IS NULL)
        OR
        (account_type = 'team_leader' AND employee_id IS NOT NULL AND reseller_id IS NULL)
        OR
        (account_type = 'reseller' AND employee_id IS NULL AND reseller_id IS NOT NULL)
    ),
    CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE TABLE account_2fa_codes (
    two_factor_code_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    code_hash text NOT NULL,
    purpose text NOT NULL DEFAULT 'login'
        CHECK (purpose IN ('login', 'password_reset', 'account_activation')),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK (used_at IS NULL OR used_at >= created_at)
);

CREATE TABLE account_sessions (
    session_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    login_at timestamptz NOT NULL DEFAULT now(),
    logout_at timestamptz,
    expires_at timestamptz NOT NULL,
    ip_address inet,
    user_agent text,
    CHECK (expires_at > login_at),
    CHECK (logout_at IS NULL OR logout_at >= login_at)
);

CREATE TABLE activity_logs (
    activity_log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    action text NOT NULL,
    entity_type text,
    entity_id bigint,
    ip_address inet,
    user_agent text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE department_leaders (
    department_leader_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_id bigint NOT NULL REFERENCES departments(department_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    team_leader_employee_id bigint NOT NULL REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    team_leader_account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    started_on date NOT NULL DEFAULT CURRENT_DATE,
    ended_on date,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ended_on IS NULL OR ended_on >= started_on)
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
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    CHECK (reviewed_at IS NULL OR reviewed_by_account_id IS NOT NULL),
    CHECK (status NOT IN ('approved', 'rejected', 'onboarded') OR reviewed_by_account_id IS NOT NULL)
);

CREATE TABLE inquiry_messages (
    inquiry_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inquiry_id bigint NOT NULL REFERENCES inquiries(inquiry_id) ON UPDATE CASCADE ON DELETE CASCADE,
    sender_type text NOT NULL
        CHECK (sender_type IN ('potential_reseller', 'team_leader', 'chatbot', 'system')),
    sender_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    message text NOT NULL,
    ai_intent text,
    ai_confidence numeric(5,4) CHECK (ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)),
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
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    CHECK (approved_at IS NULL OR approved_by_account_id IS NOT NULL)
);

ALTER TABLE accounts
    ADD CONSTRAINT fk_accounts_reseller
    FOREIGN KEY (reseller_id)
    REFERENCES resellers(reseller_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT;

CREATE TABLE meat_types (
    meat_type_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE,
    description text
);

CREATE TABLE raw_materials (
    raw_material_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meat_type_id bigint NOT NULL REFERENCES meat_types(meat_type_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    name text NOT NULL UNIQUE,
    unit text NOT NULL,
    reorder_level numeric(12,3) NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE products (
    product_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meat_type_id bigint REFERENCES meat_types(meat_type_id) ON UPDATE CASCADE ON DELETE SET NULL,
    name text NOT NULL UNIQUE,
    description text,
    unit text NOT NULL DEFAULT 'kg',
    base_price numeric(12,2) NOT NULL CHECK (base_price >= 0),
    reorder_level numeric(12,3) NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE raw_material_batches (
    raw_material_batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_material_id bigint NOT NULL REFERENCES raw_materials(raw_material_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    batch_code text NOT NULL UNIQUE,
    quantity_received numeric(12,3) NOT NULL CHECK (quantity_received > 0),
    quantity_available numeric(12,3) NOT NULL CHECK (quantity_available >= 0),
    unit text NOT NULL,
    cost_per_unit numeric(12,2) NOT NULL CHECK (cost_per_unit >= 0),
    received_date date NOT NULL DEFAULT CURRENT_DATE,
    expiry_date date,
    quality_status text NOT NULL DEFAULT 'approved'
        CHECK (quality_status IN ('pending', 'approved', 'rejected', 'expired', 'spoiled')),
    received_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (quantity_available <= quantity_received),
    CHECK (expiry_date IS NULL OR expiry_date >= received_date),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE product_recipes (
    recipe_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE CASCADE,
    raw_material_id bigint NOT NULL REFERENCES raw_materials(raw_material_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity_required numeric(12,3) NOT NULL CHECK (quantity_required > 0),
    unit text NOT NULL,
    UNIQUE (product_id, raw_material_id),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE production_runs (
    production_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    created_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    quantity_planned numeric(12,3) CHECK (quantity_planned IS NULL OR quantity_planned > 0),
    quantity_produced numeric(12,3) CHECK (quantity_produced IS NULL OR quantity_produced >= 0),
    status text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'completed', 'cancelled')),
    production_date date NOT NULL DEFAULT CURRENT_DATE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'completed' OR quantity_produced IS NOT NULL)
);

CREATE TABLE production_usage (
    usage_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    production_id bigint NOT NULL REFERENCES production_runs(production_id) ON UPDATE CASCADE ON DELETE CASCADE,
    raw_material_batch_id bigint NOT NULL REFERENCES raw_material_batches(raw_material_batch_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity_used numeric(12,3) NOT NULL CHECK (quantity_used > 0),
    unit text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (production_id, raw_material_batch_id),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE product_batches (
    product_batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    production_id bigint REFERENCES production_runs(production_id) ON UPDATE CASCADE ON DELETE SET NULL,
    batch_code text NOT NULL UNIQUE,
    source_type text NOT NULL
        CHECK (source_type IN ('direct_received', 'production')),
    quantity_received numeric(12,3) NOT NULL CHECK (quantity_received > 0),
    quantity_available numeric(12,3) NOT NULL CHECK (quantity_available >= 0),
    unit text NOT NULL,
    production_date date,
    received_date date NOT NULL DEFAULT CURRENT_DATE,
    expiry_date date NOT NULL,
    quality_status text NOT NULL DEFAULT 'approved'
        CHECK (quality_status IN ('pending', 'approved', 'rejected', 'expired', 'spoiled')),
    received_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (quantity_available <= quantity_received),
    CHECK ((source_type = 'production' AND production_id IS NOT NULL) OR (source_type = 'direct_received' AND production_id IS NULL)),
    CHECK (production_date IS NULL OR production_date <= received_date),
    CHECK (expiry_date >= received_date),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE inventory_transactions (
    transaction_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inventory_scope text NOT NULL
        CHECK (inventory_scope IN ('raw_material', 'product')),
    raw_material_batch_id bigint REFERENCES raw_material_batches(raw_material_batch_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    product_batch_id bigint REFERENCES product_batches(product_batch_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    transaction_type text NOT NULL
        CHECK (transaction_type IN ('received', 'production_use', 'production_output', 'sale', 'adjustment', 'spoilage', 'return')),
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    unit text NOT NULL,
    reason text,
    performed_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (inventory_scope = 'raw_material' AND raw_material_batch_id IS NOT NULL AND product_batch_id IS NULL)
        OR
        (inventory_scope = 'product' AND product_batch_id IS NOT NULL AND raw_material_batch_id IS NULL)
    ),
    CHECK (btrim(unit) <> '')
);

CREATE TABLE product_price_history (
    price_history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id bigint NOT NULL REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE CASCADE,
    price numeric(12,2) NOT NULL CHECK (price >= 0),
    effective_from timestamptz NOT NULL DEFAULT now(),
    effective_to timestamptz,
    changed_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    reason text,
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE TABLE product_batch_price_adjustments (
    adjustment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_batch_id bigint NOT NULL REFERENCES product_batches(product_batch_id) ON UPDATE CASCADE ON DELETE CASCADE,
    adjustment_type text NOT NULL
        CHECK (adjustment_type IN ('discount_percent', 'fixed_price')),
    discount_percent numeric(5,2) CHECK (discount_percent IS NULL OR (discount_percent > 0 AND discount_percent <= 100)),
    adjusted_price numeric(12,2) CHECK (adjusted_price IS NULL OR adjusted_price >= 0),
    reason text NOT NULL,
    starts_at timestamptz NOT NULL DEFAULT now(),
    ends_at timestamptz,
    created_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (adjustment_type = 'discount_percent' AND discount_percent IS NOT NULL AND adjusted_price IS NULL)
        OR
        (adjustment_type = 'fixed_price' AND adjusted_price IS NOT NULL AND discount_percent IS NULL)
    ),
    CHECK (ends_at IS NULL OR ends_at > starts_at)
);

CREATE TABLE orders (
    order_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_type text NOT NULL
        CHECK (order_type IN ('walk_in', 'reseller')),
    reseller_id bigint REFERENCES resellers(reseller_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    created_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    approved_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    approved_at timestamptz,
    order_date timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'fulfilled', 'cancelled')),
    fulfilled_at timestamptz,
    total_amount numeric(12,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    notes text,
    CHECK ((order_type = 'reseller' AND reseller_id IS NOT NULL) OR (order_type = 'walk_in' AND reseller_id IS NULL)),
    CHECK (approved_at IS NULL OR approved_by_account_id IS NOT NULL),
    CHECK (fulfilled_at IS NULL OR fulfilled_at >= order_date)
);

CREATE TABLE order_items (
    order_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    unit text NOT NULL,
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total numeric(12,2) GENERATED ALWAYS AS (round(quantity * unit_price, 2)) STORED,
    CHECK (btrim(unit) <> '')
);

CREATE TABLE order_batch_allocations (
    allocation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_item_id bigint NOT NULL REFERENCES order_items(order_item_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_batch_id bigint NOT NULL REFERENCES product_batches(product_batch_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity_allocated numeric(12,3) NOT NULL CHECK (quantity_allocated > 0),
    allocated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (order_item_id, product_batch_id)
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
    CHECK (period_end >= period_start),
    CHECK (
        (report_source = 'team_leader' AND department_id IS NOT NULL AND reseller_id IS NULL)
        OR
        (report_source = 'reseller' AND reseller_id IS NOT NULL AND department_id IS NULL)
    )
);

CREATE TABLE employee_attendance (
    attendance_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id bigint NOT NULL REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    work_date date NOT NULL,
    status text NOT NULL
        CHECK (status IN ('present', 'absent', 'late', 'excused')),
    time_in time,
    time_out time,
    recorded_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (employee_id, work_date),
    CHECK (time_out IS NULL OR time_in IS NULL OR time_out >= time_in)
);

CREATE TABLE employee_tasks (
    task_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id bigint NOT NULL REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    assigned_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    title text NOT NULL,
    description text,
    due_date date,
    status text NOT NULL DEFAULT 'assigned'
        CHECK (status IN ('assigned', 'in_progress', 'completed', 'cancelled')),
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (status = 'completed' AND completed_at IS NOT NULL)
        OR
        (status <> 'completed' AND completed_at IS NULL)
    )
);

CREATE TABLE employee_merit_evaluations (
    evaluation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id bigint NOT NULL REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    evaluator_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    attendance_score integer NOT NULL CHECK (attendance_score BETWEEN 1 AND 5),
    task_score integer NOT NULL CHECK (task_score BETWEEN 1 AND 5),
    behavior_score integer NOT NULL CHECK (behavior_score BETWEEN 1 AND 5),
    overall_score numeric(4,2) NOT NULL CHECK (overall_score >= 1 AND overall_score <= 5),
    feedback text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (employee_id, period_start, period_end),
    CHECK (period_end >= period_start)
);

CREATE TABLE alerts (
    alert_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_type text NOT NULL
        CHECK (alert_type IN ('low_stock', 'near_expiry', 'expired_batch', 'forecast')),
    severity text NOT NULL DEFAULT 'warning'
        CHECK (severity IN ('info', 'warning', 'critical')),
    product_id bigint REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_batch_id bigint REFERENCES product_batches(product_batch_id) ON UPDATE CASCADE ON DELETE CASCADE,
    raw_material_id bigint REFERENCES raw_materials(raw_material_id) ON UPDATE CASCADE ON DELETE CASCADE,
    raw_material_batch_id bigint REFERENCES raw_material_batches(raw_material_batch_id) ON UPDATE CASCADE ON DELETE CASCADE,
    message text NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved')),
    triggered_at timestamptz NOT NULL DEFAULT now(),
    acknowledged_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    acknowledged_at timestamptz,
    resolved_at timestamptz,
    CHECK (
        product_id IS NOT NULL
        OR product_batch_id IS NOT NULL
        OR raw_material_id IS NOT NULL
        OR raw_material_batch_id IS NOT NULL
    ),
    CHECK (acknowledged_at IS NULL OR acknowledged_by_account_id IS NOT NULL),
    CHECK (resolved_at IS NULL OR resolved_at >= triggered_at)
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
    CHECK (input_period_end >= input_period_start),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE forecast_results (
    forecast_result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    forecast_run_id bigint NOT NULL REFERENCES forecast_runs(forecast_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE CASCADE,
    forecast_date date NOT NULL,
    predicted_quantity numeric(12,3) NOT NULL CHECK (predicted_quantity >= 0),
    confidence_lower numeric(12,3) CHECK (confidence_lower IS NULL OR confidence_lower >= 0),
    confidence_upper numeric(12,3) CHECK (confidence_upper IS NULL OR confidence_upper >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (forecast_run_id, product_id, forecast_date),
    CHECK (confidence_lower IS NULL OR confidence_upper IS NULL OR confidence_upper >= confidence_lower)
);

CREATE UNIQUE INDEX ux_accounts_email_lower ON accounts (lower(email));
CREATE UNIQUE INDEX ux_accounts_active_employee ON accounts (employee_id)
    WHERE employee_id IS NOT NULL AND is_active;
CREATE UNIQUE INDEX ux_accounts_active_reseller ON accounts (reseller_id)
    WHERE reseller_id IS NOT NULL AND is_active;
CREATE UNIQUE INDEX ux_department_leaders_active_department ON department_leaders (department_id)
    WHERE ended_on IS NULL;
CREATE UNIQUE INDEX ux_department_leaders_active_employee ON department_leaders (team_leader_employee_id)
    WHERE ended_on IS NULL;
CREATE UNIQUE INDEX ux_product_price_history_active_product ON product_price_history (product_id)
    WHERE effective_to IS NULL;
CREATE UNIQUE INDEX ux_resellers_email_lower ON resellers (lower(email));

CREATE INDEX ix_employees_department ON employees (department_id);
CREATE INDEX ix_inquiries_assigned_team_leader ON inquiries (assigned_team_leader_account_id);
CREATE INDEX ix_product_batches_fefo ON product_batches (product_id, expiry_date, quantity_available)
    WHERE quality_status = 'approved' AND quantity_available > 0;
CREATE INDEX ix_orders_reseller_status ON orders (reseller_id, status);
CREATE INDEX ix_activity_logs_account_created ON activity_logs (account_id, created_at DESC);
CREATE INDEX ix_alerts_status_type ON alerts (status, alert_type);

CREATE OR REPLACE FUNCTION require_account_type()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_type text := TG_ARGV[0];
    column_name text := TG_ARGV[1];
    account_id_value bigint;
    actual_type text;
BEGIN
    EXECUTE format('SELECT ($1).%I', column_name)
        INTO account_id_value
        USING NEW;

    IF account_id_value IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT account_type
    INTO actual_type
    FROM accounts
    WHERE account_id = account_id_value;

    IF actual_type IS DISTINCT FROM expected_type THEN
        RAISE EXCEPTION '% must reference an account of type %, got %',
            column_name, expected_type, COALESCE(actual_type, 'missing');
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_department_leader_account()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    linked_employee_id bigint;
    linked_account_type text;
BEGIN
    SELECT employee_id, account_type
    INTO linked_employee_id, linked_account_type
    FROM accounts
    WHERE account_id = NEW.team_leader_account_id;

    IF linked_account_type IS DISTINCT FROM 'team_leader'
       OR linked_employee_id IS DISTINCT FROM NEW.team_leader_employee_id THEN
        RAISE EXCEPTION 'department_leaders.team_leader_account_id must be a team leader account linked to team_leader_employee_id';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_inquiry_review()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    assigned_type text;
    reviewed_type text;
BEGIN
    IF NEW.assigned_team_leader_account_id IS NOT NULL THEN
        SELECT account_type INTO assigned_type
        FROM accounts
        WHERE account_id = NEW.assigned_team_leader_account_id;

        IF assigned_type IS DISTINCT FROM 'team_leader' THEN
            RAISE EXCEPTION 'inquiries.assigned_team_leader_account_id must reference a team leader account';
        END IF;
    END IF;

    IF NEW.reviewed_by_account_id IS NOT NULL THEN
        SELECT account_type INTO reviewed_type
        FROM accounts
        WHERE account_id = NEW.reviewed_by_account_id;

        IF reviewed_type IS DISTINCT FROM 'team_leader' THEN
            RAISE EXCEPTION 'inquiries.reviewed_by_account_id must reference a team leader account';
        END IF;

        IF NEW.assigned_team_leader_account_id IS DISTINCT FROM NEW.reviewed_by_account_id THEN
            RAISE EXCEPTION 'reseller inquiry must be reviewed by its assigned team leader';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_order_accounts()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    creator_type text;
    creator_reseller_id bigint;
    approver_type text;
BEGIN
    IF NEW.created_by_account_id IS NOT NULL THEN
        SELECT account_type, reseller_id
        INTO creator_type, creator_reseller_id
        FROM accounts
        WHERE account_id = NEW.created_by_account_id;

        IF NEW.order_type = 'reseller'
           AND (creator_type IS DISTINCT FROM 'reseller' OR creator_reseller_id IS DISTINCT FROM NEW.reseller_id) THEN
            RAISE EXCEPTION 'reseller orders must be created by the matching reseller account';
        END IF;

        IF NEW.order_type = 'walk_in'
           AND creator_type IS DISTINCT FROM 'team_leader' THEN
            RAISE EXCEPTION 'walk-in orders must be created by a team leader account';
        END IF;
    END IF;

    IF NEW.approved_by_account_id IS NOT NULL THEN
        SELECT account_type
        INTO approver_type
        FROM accounts
        WHERE account_id = NEW.approved_by_account_id;

        IF approver_type IS DISTINCT FROM 'team_leader' THEN
            RAISE EXCEPTION 'orders.approved_by_account_id must reference a team leader account';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_sales_report_submitter()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    submitter_type text;
    submitter_reseller_id bigint;
BEGIN
    IF NEW.submitted_by_account_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT account_type, reseller_id
    INTO submitter_type, submitter_reseller_id
    FROM accounts
    WHERE account_id = NEW.submitted_by_account_id;

    IF NEW.report_source = 'team_leader' AND submitter_type IS DISTINCT FROM 'team_leader' THEN
        RAISE EXCEPTION 'team leader sales reports must be submitted by a team leader account';
    END IF;

    IF NEW.report_source = 'reseller'
       AND (submitter_type IS DISTINCT FROM 'reseller' OR submitter_reseller_id IS DISTINCT FROM NEW.reseller_id) THEN
        RAISE EXCEPTION 'reseller sales reports must be submitted by the matching reseller account';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_product_batch_source()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    produced_product_id bigint;
BEGIN
    IF NEW.production_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT product_id
    INTO produced_product_id
    FROM production_runs
    WHERE production_id = NEW.production_id;

    IF produced_product_id IS DISTINCT FROM NEW.product_id THEN
        RAISE EXCEPTION 'product_batches.production_id must reference a production run for the same product';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_order_batch_allocation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    item_product_id bigint;
    item_quantity numeric(12,3);
    batch_product_id bigint;
    allocated_to_item numeric(12,3);
BEGIN
    SELECT product_id, quantity
    INTO item_product_id, item_quantity
    FROM order_items
    WHERE order_item_id = NEW.order_item_id;

    SELECT product_id
    INTO batch_product_id
    FROM product_batches
    WHERE product_batch_id = NEW.product_batch_id;

    IF item_product_id IS DISTINCT FROM batch_product_id THEN
        RAISE EXCEPTION 'order batch allocations must use batches for the ordered product';
    END IF;

    SELECT COALESCE(SUM(quantity_allocated), 0)
    INTO allocated_to_item
    FROM order_batch_allocations
    WHERE order_item_id = NEW.order_item_id
      AND allocation_id IS DISTINCT FROM NEW.allocation_id;

    IF allocated_to_item + NEW.quantity_allocated > item_quantity THEN
        RAISE EXCEPTION 'allocated quantity cannot exceed order item quantity';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_department_leaders_account
BEFORE INSERT OR UPDATE ON department_leaders
FOR EACH ROW EXECUTE FUNCTION enforce_department_leader_account();

CREATE TRIGGER trg_inquiries_review
BEFORE INSERT OR UPDATE ON inquiries
FOR EACH ROW EXECUTE FUNCTION enforce_inquiry_review();

CREATE TRIGGER trg_resellers_approved_by_team_leader
BEFORE INSERT OR UPDATE ON resellers
FOR EACH ROW EXECUTE FUNCTION require_account_type('team_leader', 'approved_by_account_id');

CREATE TRIGGER trg_price_history_changed_by_owner
BEFORE INSERT OR UPDATE ON product_price_history
FOR EACH ROW EXECUTE FUNCTION require_account_type('owner', 'changed_by_account_id');

CREATE TRIGGER trg_batch_price_adjustments_created_by_owner
BEFORE INSERT OR UPDATE ON product_batch_price_adjustments
FOR EACH ROW EXECUTE FUNCTION require_account_type('owner', 'created_by_account_id');

CREATE TRIGGER trg_employee_attendance_recorded_by_team_leader
BEFORE INSERT OR UPDATE ON employee_attendance
FOR EACH ROW EXECUTE FUNCTION require_account_type('team_leader', 'recorded_by_account_id');

CREATE TRIGGER trg_employee_tasks_assigned_by_team_leader
BEFORE INSERT OR UPDATE ON employee_tasks
FOR EACH ROW EXECUTE FUNCTION require_account_type('team_leader', 'assigned_by_account_id');

CREATE TRIGGER trg_employee_merit_evaluator_team_leader
BEFORE INSERT OR UPDATE ON employee_merit_evaluations
FOR EACH ROW EXECUTE FUNCTION require_account_type('team_leader', 'evaluator_account_id');

CREATE TRIGGER trg_orders_accounts
BEFORE INSERT OR UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION enforce_order_accounts();

CREATE TRIGGER trg_sales_reports_submitter
BEFORE INSERT OR UPDATE ON sales_reports
FOR EACH ROW EXECUTE FUNCTION enforce_sales_report_submitter();

CREATE TRIGGER trg_forecast_runs_by_owner
BEFORE INSERT OR UPDATE ON forecast_runs
FOR EACH ROW EXECUTE FUNCTION require_account_type('owner', 'run_by_account_id');

CREATE TRIGGER trg_product_batches_source
BEFORE INSERT OR UPDATE ON product_batches
FOR EACH ROW EXECUTE FUNCTION enforce_product_batch_source();

CREATE TRIGGER trg_order_batch_allocations_match
BEFORE INSERT OR UPDATE ON order_batch_allocations
FOR EACH ROW EXECUTE FUNCTION enforce_order_batch_allocation();

CREATE VIEW product_stock_summary AS
SELECT
    p.product_id,
    p.name AS product_name,
    p.unit,
    p.reorder_level,
    COALESCE(
        SUM(pb.quantity_available) FILTER (
            WHERE pb.quality_status = 'approved'
              AND pb.expiry_date >= CURRENT_DATE
        ),
        0
    ) AS available_quantity
FROM products p
LEFT JOIN product_batches pb ON pb.product_id = p.product_id
GROUP BY p.product_id, p.name, p.unit, p.reorder_level;

CREATE VIEW low_stock_products AS
SELECT *
FROM product_stock_summary
WHERE available_quantity <= reorder_level;

CREATE VIEW near_expiry_product_batches AS
SELECT
    pb.product_batch_id,
    pb.product_id,
    p.name AS product_name,
    pb.batch_code,
    pb.quantity_available,
    pb.unit,
    pb.expiry_date,
    pb.expiry_date - CURRENT_DATE AS days_until_expiry
FROM product_batches pb
JOIN products p ON p.product_id = pb.product_id
WHERE pb.quality_status = 'approved'
  AND pb.quantity_available > 0
  AND pb.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7;

CREATE VIEW raw_material_stock_summary AS
SELECT
    rm.raw_material_id,
    rm.name AS raw_material_name,
    rm.unit,
    rm.reorder_level,
    COALESCE(
        SUM(rmb.quantity_available) FILTER (
            WHERE rmb.quality_status = 'approved'
              AND (rmb.expiry_date IS NULL OR rmb.expiry_date >= CURRENT_DATE)
        ),
        0
    ) AS available_quantity
FROM raw_materials rm
LEFT JOIN raw_material_batches rmb ON rmb.raw_material_id = rm.raw_material_id
GROUP BY rm.raw_material_id, rm.name, rm.unit, rm.reorder_level;

COMMIT;
