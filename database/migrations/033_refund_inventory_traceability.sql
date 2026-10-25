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
