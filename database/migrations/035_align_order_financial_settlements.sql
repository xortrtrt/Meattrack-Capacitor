-- One row per original order: never multiply money by items, receipts, or attempts.
CREATE INDEX IF NOT EXISTS ix_order_refunds_order ON order_refunds(order_id);
CREATE INDEX IF NOT EXISTS ix_orders_payment_key ON orders(payment_transaction_key) WHERE payment_transaction_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_return_payment_proof_checksum ON return_attachments(checksum_sha256) WHERE purpose='payment';
CREATE OR REPLACE VIEW order_financial_reconciliation AS
WITH balances AS (
 SELECT o.order_id,o.order_type,o.status,o.created_by_account_id,o.team_leader_account_id,
 o.rejection_payment_received,o.payment_amount,
 CASE WHEN o.status='fulfilled' THEN o.total_amount ELSE 0 END original_sales,
 CASE WHEN o.order_type='walk_in' AND o.status='fulfilled' THEN o.total_amount
      WHEN o.status IN ('approved','fulfilled') OR (o.status='rejected' AND o.rejection_payment_received)
      THEN COALESCE(o.payment_amount,0) ELSE 0 END original_collections,
 COALESCE(f.obligations,0) refund_obligations,COALESCE(f.pending,0) pending_refunds,
 COALESCE(f.completed,0) completed_refunds,
 COALESCE(f.cancellation,0) cancellation_refunds,COALESCE(f.returns,0) return_refunds,
 COALESCE(f.replacement,0) replacement_refunds,
 COALESCE(r.credits,0) return_credits,COALESCE(r.replacement,0) replacement_value,
 COALESCE(r.collections,0) additional_collections
 FROM orders o LEFT JOIN LATERAL (
  SELECT SUM(f.amount) obligations,SUM(f.amount) FILTER(WHERE f.status<>'completed') pending,
   SUM(f.amount) FILTER(WHERE f.status='completed') completed,
   SUM(f.amount) FILTER(WHERE f.status='completed' AND f.return_id IS NULL) cancellation,
   SUM(f.amount) FILTER(WHERE f.status='completed' AND r.resolution='refund') returns,
   SUM(f.amount) FILTER(WHERE f.status='completed' AND r.resolution='replace') replacement
  FROM order_refunds f LEFT JOIN product_returns r ON r.return_id=f.return_id WHERE f.order_id=o.order_id
 ) f ON true LEFT JOIN LATERAL (
  SELECT SUM(r.credit) FILTER(WHERE r.verified_at IS NOT NULL) credits,
   SUM(r.replacement_total) FILTER(WHERE r.replacement_released_at IS NOT NULL) replacement,
   SUM(r.payment_amount) FILTER(WHERE r.payment_verified_at IS NOT NULL) collections
  FROM product_returns r WHERE r.order_id=o.order_id
 ) r ON true
)
SELECT b.*,b.original_collections+b.additional_collections-b.completed_refunds net_collections,
 b.original_sales-b.return_refunds-b.replacement_refunds retained_sales,
 array_remove(ARRAY[
  CASE WHEN b.refund_obligations>b.original_collections
   THEN 'Refund obligations exceed confirmed original collections.' END,
  CASE WHEN b.status='rejected' AND b.rejection_payment_received AND NOT EXISTS(
   SELECT 1 FROM order_refunds f WHERE f.order_id=b.order_id AND f.return_id IS NULL
    AND f.amount=b.payment_amount)
   THEN 'Rejected paid order is missing its full-payment refund.' END,
  CASE WHEN EXISTS(SELECT 1 FROM order_refunds f LEFT JOIN product_returns r ON r.return_id=f.return_id
   WHERE f.order_id=b.order_id AND (
    (f.return_id IS NULL AND (b.status<>'rejected' OR b.rejection_payment_received IS DISTINCT FROM true
      OR f.amount IS DISTINCT FROM b.payment_amount))
    OR (f.return_id IS NOT NULL AND (r.order_id<>b.order_id OR b.status<>'fulfilled'
      OR r.verified_at IS NULL OR r.verified_by_account_id IS NULL OR r.approved_by_account_id IS NULL
      OR r.status NOT IN ('settlement_pending','completed')
      OR (r.resolution='replace' AND r.replacement_released_at IS NULL)))))
   THEN 'Refund source does not match the order and verified return.' END,
  CASE WHEN EXISTS(SELECT 1 FROM product_returns r JOIN product_return_reconciliation c USING(return_id)
   WHERE r.order_id=b.order_id AND cardinality(c.flags)>0)
   THEN 'Return custody or settlement records require reconciliation.' END,
  CASE WHEN EXISTS(SELECT 1 FROM product_returns r WHERE r.order_id=b.order_id
   AND r.verified_at IS NOT NULL AND r.verified_by_account_id IS NULL)
   THEN 'Final Inventory verification requires a named verifier.' END,
  CASE WHEN EXISTS(SELECT 1 FROM product_returns r WHERE r.order_id=b.order_id AND r.resolution='replace'
   AND r.approved_by_account_id IS NOT NULL AND (
    r.replacement_total IS DISTINCT FROM (SELECT SUM(q.line_total) FROM replacement_quote_lines q
     WHERE q.return_id=r.return_id AND q.revision=r.quote_revision)
    OR (r.payment_verified_at IS NOT NULL AND (r.payment_amount IS DISTINCT FROM r.replacement_total-r.credit
     OR r.payment_verified_by_account_id IS NULL OR r.payment_reference IS NULL
     OR r.accepted_revision IS DISTINCT FROM r.quote_revision
     OR NOT EXISTS(SELECT 1 FROM return_attachments a WHERE a.return_id=r.return_id
      AND a.purpose='payment' AND a.revision=r.quote_revision)))
    OR (r.replacement_released_at IS NOT NULL AND (r.accepted_revision IS DISTINCT FROM r.quote_revision
     OR (r.replacement_total>r.credit AND r.payment_verified_at IS NULL)
     OR EXISTS(SELECT 1 FROM (
      SELECT q.product_id,SUM(q.quantity) quantity FROM replacement_quote_lines q
      WHERE q.return_id=r.return_id AND q.revision=r.quote_revision GROUP BY q.product_id
     ) q FULL JOIN (
      SELECT b.item_id product_id,SUM(x.quantity) quantity FROM replacement_releases x
      JOIN inventory_batches b USING(batch_id) WHERE x.return_id=r.return_id GROUP BY b.item_id
     ) x USING(product_id) WHERE q.quantity IS DISTINCT FROM x.quantity)))))
   THEN 'Replacement quote, release, or additional collection does not reconcile.' END,
  CASE WHEN EXISTS(SELECT 1 FROM order_refunds f WHERE f.order_id=b.order_id AND f.status='completed'
   AND NOT EXISTS(SELECT 1 FROM order_refund_attempts a JOIN order_refund_proofs p USING(order_refund_attempt_id)
    WHERE a.order_refund_id=f.order_refund_id AND a.status='completed'))
   THEN 'Completed refund is missing a successful transfer and proof.' END
 ],NULL) flags
FROM balances b;

-- Surface legacy verifier gaps in the return workspace without obstructing
-- Inventory's ability to inspect custody and record a real re-verification.
CREATE OR REPLACE VIEW product_return_review_reconciliation AS
SELECT c.return_id,c.flags || array_remove(ARRAY[
 CASE WHEN r.verified_at IS NOT NULL AND r.verified_by_account_id IS NULL
 THEN 'Final Inventory verification requires a named verifier.' END
],NULL) flags FROM product_return_reconciliation c JOIN product_returns r USING(return_id);

-- Keep the return source as immutable as the original order and amount.
CREATE OR REPLACE FUNCTION protect_order_refund_audit_fields()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.order_id IS DISTINCT FROM OLD.order_id OR NEW.return_id IS DISTINCT FROM OLD.return_id
  OR NEW.amount IS DISTINCT FROM OLD.amount OR NEW.currency IS DISTINCT FROM OLD.currency
  OR NEW.requested_by_account_id IS DISTINCT FROM OLD.requested_by_account_id
  OR NEW.requested_at IS DISTINCT FROM OLD.requested_at THEN
  RAISE EXCEPTION 'Locked refund request fields cannot be changed.';
 END IF;
 RETURN NEW;
END $$;

-- Serialize incoming payment identities across the original-sale and top-up ledgers.
CREATE OR REPLACE FUNCTION prevent_cross_ledger_payment_reference()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE reference_key text;
BEGIN
 IF TG_TABLE_NAME='orders' THEN reference_key=NEW.payment_transaction_key;
 ELSE reference_key=NEW.payment_reference; END IF;
 IF reference_key IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('incoming-payment:' || reference_key,0));
 IF (TG_TABLE_NAME='orders' AND EXISTS(SELECT 1 FROM product_returns WHERE payment_reference=reference_key))
  OR (TG_TABLE_NAME='product_returns' AND EXISTS(SELECT 1 FROM orders WHERE payment_transaction_key=reference_key)) THEN
  RAISE EXCEPTION 'This payment reference has already been used in another collection ledger.' USING ERRCODE='23505';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cross_ledger_order_payment_reference ON orders;
CREATE TRIGGER cross_ledger_order_payment_reference BEFORE INSERT OR UPDATE OF payment_transaction_key ON orders
 FOR EACH ROW EXECUTE FUNCTION prevent_cross_ledger_payment_reference();
DROP TRIGGER IF EXISTS cross_ledger_return_payment_reference ON product_returns;
CREATE TRIGGER cross_ledger_return_payment_reference BEFORE INSERT OR UPDATE OF payment_reference ON product_returns
 FOR EACH ROW EXECUTE FUNCTION prevent_cross_ledger_payment_reference();

CREATE OR REPLACE FUNCTION prevent_cross_ledger_payment_proof()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='return_attachments' THEN
  IF NEW.purpose<>'payment' THEN RETURN NEW; END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('incoming-proof:' || NEW.checksum_sha256,0));
 IF TG_TABLE_NAME='order_payment_proofs' THEN
  IF EXISTS(SELECT 1 FROM return_attachments WHERE purpose='payment' AND checksum_sha256=NEW.checksum_sha256) THEN
   RAISE EXCEPTION 'This payment screenshot has already been used for a replacement collection.' USING ERRCODE='23505';
  END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM order_payment_proofs WHERE checksum_sha256=NEW.checksum_sha256)
   OR EXISTS(SELECT 1 FROM return_attachments WHERE purpose='payment' AND checksum_sha256=NEW.checksum_sha256
    AND return_id<>NEW.return_id) THEN
   RAISE EXCEPTION 'This payment screenshot has already been used for another collection.' USING ERRCODE='23505';
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cross_ledger_order_payment_proof ON order_payment_proofs;
CREATE TRIGGER cross_ledger_order_payment_proof BEFORE INSERT ON order_payment_proofs
 FOR EACH ROW EXECUTE FUNCTION prevent_cross_ledger_payment_proof();
DROP TRIGGER IF EXISTS cross_ledger_return_payment_proof ON return_attachments;
CREATE TRIGGER cross_ledger_return_payment_proof BEFORE INSERT ON return_attachments
 FOR EACH ROW EXECUTE FUNCTION prevent_cross_ledger_payment_proof();
