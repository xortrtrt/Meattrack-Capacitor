CREATE OR REPLACE VIEW finished_stock_availability AS
SELECT p.item_id,p.name,p.is_active,
 GREATEST(COALESCE(SUM(b.quantity_available) FILTER(WHERE b.quality_status='approved'
 AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date),0)
 -COALESCE((SELECT SUM(r.quantity) FROM all_stock_reservations r WHERE r.product_id=p.item_id),0),0) AS available
FROM inventory_items p LEFT JOIN inventory_batches b ON b.item_id=p.item_id
WHERE p.item_type='finished_product' AND p.category IS DISTINCT FROM 'Package' GROUP BY p.item_id;
-- Bundle availability uses component stock; old package batches remain historical.
CREATE OR REPLACE VIEW catalog_stock_availability AS
SELECT i.item_id,
 CASE WHEN i.category IS DISTINCT FROM 'Package' THEN COALESCE(s.available,0)
 ELSE COALESCE((SELECT CASE WHEN bool_and(p.is_active AND p.category IS DISTINCT FROM 'Package')
                           AND count(*)>0 AND cfg.standard_locked_at IS NOT NULL
                      THEN GREATEST(MIN(floor(COALESCE(cs.available,0)/c.quantity_per_package)),0) ELSE 0 END
      FROM inventory_package_components c
      JOIN inventory_items p ON p.item_id=c.product_item_id
      LEFT JOIN finished_stock_availability cs ON cs.item_id=p.item_id
      JOIN inventory_package_settings cfg ON cfg.package_item_id=c.package_item_id
      WHERE c.package_item_id=i.item_id GROUP BY cfg.standard_locked_at),0) END AS available
FROM inventory_items i LEFT JOIN finished_stock_availability s ON s.item_id=i.item_id
WHERE i.item_type='finished_product';

CREATE TABLE IF NOT EXISTS order_packages (
 order_package_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 order_id bigint NOT NULL REFERENCES orders(order_id),
 package_item_id bigint NOT NULL REFERENCES inventory_items(item_id),
 package_name text NOT NULL,
 quantity numeric(12,3) NOT NULL CHECK(quantity>0 AND quantity=trunc(quantity)),
 unit_price numeric(12,2) NOT NULL CHECK(unit_price>=0),
 contents jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_packages_order_idx ON order_packages(order_id);
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS order_package_id bigint REFERENCES order_packages(order_package_id);
CREATE INDEX IF NOT EXISTS order_items_package_idx ON order_items(order_package_id) WHERE order_package_id IS NOT NULL;
CREATE OR REPLACE TRIGGER order_packages_immutable BEFORE UPDATE OR DELETE ON order_packages
 FOR EACH ROW EXECUTE FUNCTION reject_inventory_movement_mutation();
CREATE OR REPLACE TRIGGER order_packages_no_truncate BEFORE TRUNCATE ON order_packages
 FOR EACH STATEMENT EXECUTE FUNCTION reject_inventory_movement_mutation();

CREATE OR REPLACE FUNCTION guard_component_package_batch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM inventory_items WHERE item_id=NEW.item_id AND category='Package') THEN
  IF TG_OP='INSERT' THEN RAISE EXCEPTION 'Packages use included product stock and cannot receive standalone batches.'; END IF;
  IF NEW.quantity_available>OLD.quantity_available THEN RAISE EXCEPTION 'Packages use included product stock.'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER component_package_batch_guard BEFORE INSERT OR UPDATE ON inventory_batches
 FOR EACH ROW EXECUTE FUNCTION guard_component_package_batch();
