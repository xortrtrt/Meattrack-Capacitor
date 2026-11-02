ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_source text
    CHECK (resolution_source IN ('automatic', 'owner'));
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_reason text;

CREATE TABLE IF NOT EXISTS owner_alert_reviews (
    alert_id bigint NOT NULL REFERENCES alerts ON DELETE CASCADE,
    account_id bigint NOT NULL REFERENCES accounts ON DELETE CASCADE,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (alert_id, account_id)
);
CREATE INDEX IF NOT EXISTS ix_owner_alert_reviews_account ON owner_alert_reviews(account_id, alert_id);

-- Keep the oldest episode; retain duplicate records and their original batch links.
WITH duplicates AS (
    SELECT alert_id, row_number() OVER (PARTITION BY product_id ORDER BY alert_id) position
    FROM alerts WHERE alert_type='low_stock' AND product_id IS NOT NULL
      AND raw_material_id IS NULL AND status IN ('open','acknowledged')
)
UPDATE alerts SET status='resolved', resolved_at=now(), resolution_source='automatic',
    resolution_reason='Consolidated duplicate active low-stock alert during migration 045.'
WHERE alert_id IN (SELECT alert_id FROM duplicates WHERE position>1);

CREATE UNIQUE INDEX IF NOT EXISTS ux_active_product_low_stock_alert
ON alerts(product_id) WHERE alert_type='low_stock' AND product_id IS NOT NULL
    AND raw_material_id IS NULL AND status IN ('open','acknowledged');

INSERT INTO business_settings(setting_key,setting_value)
VALUES ('inventory.low_stock_packs','10'::jsonb) ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW finished_stock_availability AS
SELECT p.item_id, p.name, p.is_active,
    GREATEST(COALESCE(SUM(b.quantity_available) FILTER (
        WHERE b.quality_status='approved'
          AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date),0)
        - COALESCE((SELECT SUM(r.quantity) FROM all_stock_reservations r
                    WHERE r.product_id=p.item_id),0),0) AS available
FROM inventory_items p LEFT JOIN inventory_batches b ON b.item_id=p.item_id
WHERE p.item_type='finished_product' GROUP BY p.item_id;
