ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS operation_key uuid;
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_request_operation ON product_returns(operation_key) WHERE operation_key IS NOT NULL;
ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS operation_key uuid;
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_event_operation ON product_return_events(operation_key) WHERE operation_key IS NOT NULL;
ALTER TABLE replacement_quote_lines ADD COLUMN IF NOT EXISTS line_total numeric(12,2);
UPDATE replacement_quote_lines SET line_total=round(quantity*unit_price,2) WHERE line_total IS NULL;
ALTER TABLE replacement_quote_lines ALTER COLUMN line_total SET NOT NULL;
ALTER TABLE replacement_quote_lines DROP CONSTRAINT IF EXISTS replacement_quote_lines_total_check;
ALTER TABLE replacement_quote_lines ADD CONSTRAINT replacement_quote_lines_total_check CHECK(line_total>=0);
