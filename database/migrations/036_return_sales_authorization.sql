-- Preserve historical settlements; unfinished returns require an explicit Sales handoff.
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS settlement_authorized_by_account_id bigint REFERENCES accounts;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS settlement_authorized_at timestamptz;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS settlement_authorized_quote_revision integer;
ALTER TABLE product_returns DROP CONSTRAINT IF EXISTS product_returns_settlement_authorization_check;
ALTER TABLE product_returns ADD CONSTRAINT product_returns_settlement_authorization_check CHECK (
 (settlement_authorized_by_account_id IS NULL AND settlement_authorized_at IS NULL AND settlement_authorized_quote_revision IS NULL)
 OR (settlement_authorized_by_account_id IS NOT NULL AND settlement_authorized_at IS NOT NULL
     AND settlement_authorized_quote_revision IS NOT NULL AND settlement_authorized_quote_revision>=0)
);
CREATE INDEX IF NOT EXISTS ix_returns_sales_confirmation ON product_returns(order_id,updated_at)
 WHERE verified_at IS NOT NULL AND status='settlement_pending' AND settlement_authorized_at IS NULL;

-- New releases link directly to their movement; historical release quantities stay untouched.
ALTER TABLE replacement_releases ADD COLUMN IF NOT EXISTS inventory_movement_id bigint REFERENCES inventory_movements ON DELETE RESTRICT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_replacement_release_movement ON replacement_releases(inventory_movement_id)
 WHERE inventory_movement_id IS NOT NULL;
