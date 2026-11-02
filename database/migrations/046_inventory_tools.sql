-- Additive inventory tools; historical stock and order records are preserved.
ALTER TABLE inventory_movements DROP CONSTRAINT IF EXISTS inventory_movements_movement_type_check;
ALTER TABLE inventory_movements ADD CONSTRAINT inventory_movements_movement_type_check CHECK(movement_type IN (
    'opening_balance','raw_receipt','production_consumption','production_output','direct_finished_receipt',
    'sale_fulfillment','owner_correction','expired_pullout','replacement_release','raw_deduction','stock_dispatch'));
ALTER TABLE inventory_batches ADD COLUMN IF NOT EXISTS removed_at timestamptz,
    ADD COLUMN IF NOT EXISTS removed_by_account_id bigint REFERENCES accounts(account_id),
    ADD COLUMN IF NOT EXISTS removal_reason text,
    DROP CONSTRAINT IF EXISTS archived_batch_empty,
    ADD CONSTRAINT archived_batch_empty CHECK (removed_at IS NULL OR quantity_available=0);

CREATE TABLE IF NOT EXISTS inventory_tool_operations (
    reference uuid PRIMARY KEY,
    actor_account_id bigint NOT NULL REFERENCES accounts(account_id),
    action text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS inventory_tool_operations_immutable ON inventory_tool_operations;
CREATE TRIGGER inventory_tool_operations_immutable BEFORE UPDATE OR DELETE ON inventory_tool_operations
    FOR EACH ROW EXECUTE FUNCTION reject_inventory_movement_mutation();
DROP TRIGGER IF EXISTS inventory_tool_operations_no_truncate ON inventory_tool_operations;
CREATE TRIGGER inventory_tool_operations_no_truncate BEFORE TRUNCATE ON inventory_tool_operations
    FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();

CREATE TABLE IF NOT EXISTS inventory_package_dispatches (
    reference uuid PRIMARY KEY REFERENCES inventory_tool_operations(reference),
    package_item_id bigint NOT NULL REFERENCES inventory_items(item_id),
    package_name text NOT NULL,
    quantity numeric(12,3) NOT NULL CHECK(quantity>0 AND quantity=trunc(quantity)),
    mode text NOT NULL CHECK(mode IN ('standard','customized')),
    contents jsonb NOT NULL,
    actor_name text NOT NULL
);
DROP TRIGGER IF EXISTS inventory_package_dispatches_immutable ON inventory_package_dispatches;
CREATE TRIGGER inventory_package_dispatches_immutable BEFORE UPDATE OR DELETE ON inventory_package_dispatches
    FOR EACH ROW EXECUTE FUNCTION reject_inventory_movement_mutation();
DROP TRIGGER IF EXISTS inventory_package_dispatches_no_truncate ON inventory_package_dispatches;
CREATE TRIGGER inventory_package_dispatches_no_truncate BEFORE TRUNCATE ON inventory_package_dispatches
    FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();

CREATE TABLE IF NOT EXISTS inventory_package_settings (
    package_item_id bigint PRIMARY KEY REFERENCES inventory_items(item_id),
    revision integer NOT NULL DEFAULT 1,
    standard_locked_at timestamptz
);
CREATE TABLE IF NOT EXISTS inventory_package_components (
    package_item_id bigint NOT NULL REFERENCES inventory_package_settings(package_item_id),
    product_item_id bigint NOT NULL REFERENCES inventory_items(item_id),
    quantity_per_package numeric(12,3) NOT NULL CHECK(quantity_per_package>0 AND quantity_per_package=trunc(quantity_per_package)),
    PRIMARY KEY(package_item_id,product_item_id), CHECK(package_item_id<>product_item_id)
);
CREATE OR REPLACE FUNCTION protect_inventory_package_standard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE package_id bigint;
BEGIN
    IF TG_TABLE_NAME='inventory_package_settings' THEN
        IF OLD.standard_locked_at IS NOT NULL THEN
            RAISE EXCEPTION 'Saved standard package contents cannot be changed.';
        END IF;
    ELSE
        -- Lock the parent even for direct SQL, serializing edits with standard locking.
        IF TG_OP<>'INSERT' THEN
            PERFORM 1 FROM inventory_package_settings WHERE package_item_id=OLD.package_item_id FOR UPDATE;
            IF EXISTS(SELECT 1 FROM inventory_package_settings WHERE package_item_id=OLD.package_item_id AND standard_locked_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Saved standard package contents cannot be changed.';
            END IF;
        END IF;
        IF TG_OP<>'DELETE' THEN
            package_id:=NEW.package_item_id;
            PERFORM 1 FROM inventory_package_settings WHERE package_item_id=package_id FOR UPDATE;
            IF EXISTS(SELECT 1 FROM inventory_package_settings WHERE package_item_id=package_id AND standard_locked_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Saved standard package contents cannot be changed.';
            END IF;
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS inventory_package_settings_lock ON inventory_package_settings;
CREATE TRIGGER inventory_package_settings_lock BEFORE UPDATE OR DELETE ON inventory_package_settings
    FOR EACH ROW EXECUTE FUNCTION protect_inventory_package_standard();
DROP TRIGGER IF EXISTS inventory_package_components_lock ON inventory_package_components;
CREATE TRIGGER inventory_package_components_lock BEFORE INSERT OR UPDATE OR DELETE ON inventory_package_components
    FOR EACH ROW EXECUTE FUNCTION protect_inventory_package_standard();
DROP TRIGGER IF EXISTS inventory_package_settings_no_truncate ON inventory_package_settings;
CREATE TRIGGER inventory_package_settings_no_truncate BEFORE TRUNCATE ON inventory_package_settings
    FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();
DROP TRIGGER IF EXISTS inventory_package_components_no_truncate ON inventory_package_components;
CREATE TRIGGER inventory_package_components_no_truncate BEFORE TRUNCATE ON inventory_package_components
    FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();

CREATE TABLE IF NOT EXISTS inventory_product_images (
    product_item_id bigint PRIMARY KEY REFERENCES inventory_items(item_id),
    content_type text NOT NULL CHECK(content_type IN ('image/jpeg','image/png','image/webp')),
    image_data bytea NOT NULL CHECK(octet_length(image_data) BETWEEN 1 AND 2097152),
    updated_at timestamptz NOT NULL DEFAULT now()
);
