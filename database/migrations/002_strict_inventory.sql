DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM inventory_items
        WHERE (item_type = 'raw_material' AND unit NOT IN ('kg', 'g', 'ml'))
           OR (item_type = 'finished_product' AND unit <> 'pack')
    ) THEN
        RAISE EXCEPTION 'Inventory migration blocked: unsupported inventory item units exist.';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM product_recipes pr
        JOIN inventory_items p ON p.item_id = pr.product_item_id
        JOIN inventory_items rm ON rm.item_id = pr.material_item_id
        WHERE p.item_type <> 'finished_product'
           OR rm.item_type <> 'raw_material'
           OR pr.unit <> rm.unit
    ) THEN
        RAISE EXCEPTION 'Inventory migration blocked: recipe item types or units are inconsistent.';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE ii.item_type <> 'finished_product' OR ib.unit <> 'pack'
    ) THEN
        RAISE EXCEPTION 'Inventory migration blocked: batch item types or units are inconsistent.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM order_items oi
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE p.item_type <> 'finished_product' OR oi.unit <> 'pack'
    ) OR EXISTS (
        SELECT 1 FROM reseller_cart_items rci
        JOIN inventory_items p ON p.item_id = rci.product_id
        WHERE p.item_type <> 'finished_product'
    ) OR EXISTS (
        SELECT 1 FROM sales_report_items sri
        JOIN inventory_items p ON p.item_id = sri.product_id
        WHERE p.item_type <> 'finished_product' OR sri.unit <> 'pack'
    ) THEN
        RAISE EXCEPTION 'Inventory migration blocked: cart, order, or report product units/types are inconsistent.';
    END IF;
    IF EXISTS (SELECT 1 FROM inventory_batches WHERE quantity_received <> trunc(quantity_received) OR quantity_available <> trunc(quantity_available))
       OR EXISTS (SELECT 1 FROM reseller_cart_items WHERE quantity <> trunc(quantity))
       OR EXISTS (SELECT 1 FROM order_items WHERE quantity <> trunc(quantity))
       OR EXISTS (SELECT 1 FROM sales_report_items WHERE quantity_sold <> trunc(quantity_sold)) THEN
        RAISE EXCEPTION 'Inventory migration blocked: fractional pack quantities exist.';
    END IF;
END $$;

ALTER TABLE inventory_items
    ADD COLUMN IF NOT EXISTS pack_size numeric(12,3),
    ADD COLUMN IF NOT EXISTS pack_size_unit text,
    ADD COLUMN IF NOT EXISTS pack_content_status text;

WITH parsed AS (
    SELECT item_id,
           regexp_match(description, '^.+ - ([0-9]+(?:\.[0-9]{1,3})?) (g|kg|ml) per pack\.$') AS parts
    FROM inventory_items
    WHERE item_type = 'finished_product'
)
UPDATE inventory_items ii
SET pack_size = (parsed.parts)[1]::numeric(12,3),
    pack_size_unit = (parsed.parts)[2],
    pack_content_status = 'declared'
FROM parsed
WHERE ii.item_id = parsed.item_id
  AND parsed.parts IS NOT NULL;

UPDATE inventory_items
SET pack_content_status = 'unknown_legacy'
WHERE item_type = 'finished_product'
  AND pack_content_status IS NULL;

ALTER TABLE inventory_items
    DROP CONSTRAINT IF EXISTS inventory_items_measurement_contract_check;
ALTER TABLE inventory_items
    ADD CONSTRAINT inventory_items_measurement_contract_check
    CHECK (
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
                (
                    pack_content_status = 'declared'
                    AND pack_size > 0
                    AND pack_size_unit IN ('g', 'kg', 'ml')
                )
                OR
                (
                    pack_content_status = 'unknown_legacy'
                    AND pack_size IS NULL
                    AND pack_size_unit IS NULL
                )
            )
        )
    );

ALTER TABLE inventory_batches
    DROP CONSTRAINT IF EXISTS inventory_batches_whole_pack_check;
ALTER TABLE inventory_batches
    ADD CONSTRAINT inventory_batches_whole_pack_check
    CHECK (quantity_received = trunc(quantity_received) AND quantity_available = trunc(quantity_available));

ALTER TABLE reseller_cart_items
    DROP CONSTRAINT IF EXISTS reseller_cart_items_whole_pack_check;
ALTER TABLE reseller_cart_items
    ADD CONSTRAINT reseller_cart_items_whole_pack_check
    CHECK (quantity = trunc(quantity));

ALTER TABLE order_items
    DROP CONSTRAINT IF EXISTS order_items_whole_pack_check;
ALTER TABLE order_items
    ADD CONSTRAINT order_items_whole_pack_check CHECK (quantity = trunc(quantity)),
    DROP CONSTRAINT IF EXISTS order_items_pack_unit_check;
ALTER TABLE order_items
    ADD CONSTRAINT order_items_pack_unit_check CHECK (unit = 'pack');

ALTER TABLE sales_report_items
    DROP CONSTRAINT IF EXISTS sales_report_items_whole_pack_check;
ALTER TABLE sales_report_items
    ADD CONSTRAINT sales_report_items_whole_pack_check CHECK (quantity_sold = trunc(quantity_sold)),
    DROP CONSTRAINT IF EXISTS sales_report_items_pack_unit_check;
ALTER TABLE sales_report_items
    ADD CONSTRAINT sales_report_items_pack_unit_check CHECK (unit = 'pack');

CREATE OR REPLACE FUNCTION validate_finished_product_reference()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    product_type text;
BEGIN
    SELECT item_type INTO product_type FROM inventory_items WHERE item_id = NEW.product_id;
    IF product_type IS DISTINCT FROM 'finished_product' THEN
        RAISE EXCEPTION 'Cart, order, and report lines must reference a finished product.';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_validate_cart_finished_product ON reseller_cart_items;
CREATE TRIGGER trg_validate_cart_finished_product
BEFORE INSERT OR UPDATE OF product_id ON reseller_cart_items
FOR EACH ROW EXECUTE FUNCTION validate_finished_product_reference();

DROP TRIGGER IF EXISTS trg_validate_order_item_finished_product ON order_items;
CREATE TRIGGER trg_validate_order_item_finished_product
BEFORE INSERT OR UPDATE OF product_id ON order_items
FOR EACH ROW EXECUTE FUNCTION validate_finished_product_reference();

DROP TRIGGER IF EXISTS trg_validate_report_item_finished_product ON sales_report_items;
CREATE TRIGGER trg_validate_report_item_finished_product
BEFORE INSERT OR UPDATE OF product_id ON sales_report_items
FOR EACH ROW EXECUTE FUNCTION validate_finished_product_reference();

CREATE OR REPLACE FUNCTION validate_product_recipe_measurements()
RETURNS trigger
LANGUAGE plpgsql
AS $$
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

DROP TRIGGER IF EXISTS trg_validate_product_recipe_measurements ON product_recipes;
CREATE TRIGGER trg_validate_product_recipe_measurements
BEFORE INSERT OR UPDATE ON product_recipes
FOR EACH ROW EXECUTE FUNCTION validate_product_recipe_measurements();

CREATE OR REPLACE FUNCTION validate_inventory_batch_measurements()
RETURNS trigger
LANGUAGE plpgsql
AS $$
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

DROP TRIGGER IF EXISTS trg_validate_inventory_batch_measurements ON inventory_batches;
CREATE TRIGGER trg_validate_inventory_batch_measurements
BEFORE INSERT OR UPDATE ON inventory_batches
FOR EACH ROW EXECUTE FUNCTION validate_inventory_batch_measurements();

CREATE OR REPLACE FUNCTION protect_inventory_item_measurements()
RETURNS trigger
LANGUAGE plpgsql
AS $$
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

DROP TRIGGER IF EXISTS trg_protect_inventory_item_measurements ON inventory_items;
CREATE TRIGGER trg_protect_inventory_item_measurements
BEFORE UPDATE OF item_type, unit ON inventory_items
FOR EACH ROW EXECUTE FUNCTION protect_inventory_item_measurements();

CREATE TABLE IF NOT EXISTS inventory_movements (
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_movements_sale_batch
    ON inventory_movements (order_id, affected_batch_id, movement_type)
    WHERE movement_type = 'sale_fulfillment';
CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_movements_production_output
    ON inventory_movements (affected_batch_id, movement_type)
    WHERE movement_type = 'production_output';
CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_movements_production_consumption
    ON inventory_movements (production_batch_id, item_id, movement_type)
    WHERE movement_type = 'production_consumption';
CREATE INDEX IF NOT EXISTS ix_inventory_movements_created ON inventory_movements (created_at DESC, movement_id DESC);
CREATE INDEX IF NOT EXISTS ix_inventory_movements_item_created ON inventory_movements (item_id, created_at DESC);

CREATE OR REPLACE FUNCTION validate_inventory_movement()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    affected_type text;
    affected_unit text;
    batch_item_id bigint;
    production_item_type text;
BEGIN
    SELECT item_type, unit INTO affected_type, affected_unit
    FROM inventory_items WHERE item_id = NEW.item_id;
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
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
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

DROP TRIGGER IF EXISTS trg_validate_inventory_movement ON inventory_movements;
CREATE TRIGGER trg_validate_inventory_movement
BEFORE INSERT ON inventory_movements
FOR EACH ROW EXECUTE FUNCTION validate_inventory_movement();

CREATE OR REPLACE FUNCTION reject_inventory_movement_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Inventory movements are immutable.';
END $$;

DROP TRIGGER IF EXISTS trg_inventory_movements_no_update_delete ON inventory_movements;
CREATE TRIGGER trg_inventory_movements_no_update_delete
BEFORE UPDATE OR DELETE ON inventory_movements
FOR EACH ROW EXECUTE FUNCTION reject_inventory_movement_mutation();

DROP TRIGGER IF EXISTS trg_inventory_movements_no_truncate ON inventory_movements;
CREATE TRIGGER trg_inventory_movements_no_truncate
BEFORE TRUNCATE ON inventory_movements
FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();

INSERT INTO inventory_movements (
    item_id, movement_type, quantity_delta, unit,
    balance_before, balance_after, actor_name, note
)
SELECT item_id, 'opening_balance', quantity_available, unit,
       0, quantity_available, 'MEATTRACK', 'Opening balance at strict inventory migration'
FROM inventory_items
WHERE item_type = 'raw_material'
  AND NOT EXISTS (
      SELECT 1 FROM inventory_movements im
      WHERE im.item_id = inventory_items.item_id
        AND im.movement_type = 'opening_balance'
        AND im.affected_batch_id IS NULL
  );

INSERT INTO inventory_movements (
    item_id, affected_batch_id, movement_type, quantity_delta, unit,
    balance_before, balance_after, actor_name, note
)
SELECT ib.item_id, ib.batch_id, 'opening_balance', ib.quantity_available, ib.unit,
       0, ib.quantity_available, 'MEATTRACK', 'Opening batch balance at strict inventory migration'
FROM inventory_batches ib
WHERE NOT EXISTS (
    SELECT 1 FROM inventory_movements im
    WHERE im.affected_batch_id = ib.batch_id
      AND im.movement_type = 'opening_balance'
);
