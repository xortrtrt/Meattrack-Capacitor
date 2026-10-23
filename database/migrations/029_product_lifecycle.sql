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
