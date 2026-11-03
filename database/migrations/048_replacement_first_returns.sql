-- New submissions use a separate policy version; historical workflows stay intact.
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS return_policy_version smallint NOT NULL DEFAULT 1;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS refund_converted_at timestamptz;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS refund_converted_by_account_id bigint REFERENCES accounts;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS refund_conversion_reason text;
ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS original_credit_amount numeric(12,2);
ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS topup_amount numeric(12,2) NOT NULL DEFAULT 0;
UPDATE order_refunds SET original_credit_amount=amount WHERE original_credit_amount IS NULL;
ALTER TABLE order_refunds ALTER COLUMN original_credit_amount SET NOT NULL;

DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='product_returns'::regclass AND conname='return_policy_version_valid') THEN
  ALTER TABLE product_returns ADD CONSTRAINT return_policy_version_valid CHECK(return_policy_version IN (1,2));
  ALTER TABLE product_returns ADD CONSTRAINT refund_conversion_metadata_valid CHECK(
   (refund_converted_at IS NULL AND refund_converted_by_account_id IS NULL AND refund_conversion_reason IS NULL)
   OR (refund_converted_at IS NOT NULL AND refund_converted_by_account_id IS NOT NULL AND resolution='refund'
       AND replacement_released_at IS NULL AND char_length(btrim(refund_conversion_reason)) BETWEEN 5 AND 1000));
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='order_refunds'::regclass AND conname='refund_component_totals_valid') THEN
  ALTER TABLE order_refunds ADD CONSTRAINT refund_component_totals_valid CHECK(
   original_credit_amount>=0 AND topup_amount>=0 AND amount=original_credit_amount+topup_amount);
 END IF;
END $$;

CREATE OR REPLACE FUNCTION validate_refund_components() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE collected numeric; existing_credit numeric; c product_returns%ROWTYPE;
BEGIN
 SELECT payment_amount INTO collected FROM orders WHERE order_id=NEW.order_id FOR UPDATE;
 IF NEW.original_credit_amount IS NULL THEN NEW.original_credit_amount:=NEW.amount-NEW.topup_amount; END IF;
 SELECT COALESCE(SUM(original_credit_amount),0) INTO existing_credit FROM order_refunds WHERE order_id=NEW.order_id;
 IF existing_credit+NEW.original_credit_amount>COALESCE(collected,0) THEN
  RAISE EXCEPTION 'Refund obligations exceed confirmed original collections.';
 END IF;
 IF NEW.topup_amount>0 THEN
  SELECT * INTO c FROM product_returns WHERE return_id=NEW.return_id;
  IF c.return_id IS NULL OR c.order_id<>NEW.order_id OR c.resolution<>'refund' OR c.refund_converted_at IS NULL
   OR c.payment_verified_at IS NULL OR c.payment_verified_by_account_id IS NULL OR c.payment_reference IS NULL
   OR NEW.topup_amount IS DISTINCT FROM c.payment_amount OR NEW.original_credit_amount IS DISTINCT FROM c.credit THEN
   RAISE EXCEPTION 'Refunded extra payment must match the converted case and verified collection.';
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS validate_refund_components ON order_refunds;
CREATE TRIGGER validate_refund_components BEFORE INSERT ON order_refunds FOR EACH ROW EXECUTE FUNCTION validate_refund_components();

CREATE OR REPLACE FUNCTION protect_return_policy_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.return_policy_version IS DISTINCT FROM OLD.return_policy_version THEN
  RAISE EXCEPTION 'A submitted return policy version cannot be changed.';
 END IF;
 IF OLD.refund_converted_at IS NOT NULL AND (NEW.resolution IS DISTINCT FROM OLD.resolution
  OR NEW.refund_converted_at IS DISTINCT FROM OLD.refund_converted_at
  OR NEW.refund_converted_by_account_id IS DISTINCT FROM OLD.refund_converted_by_account_id
  OR NEW.refund_conversion_reason IS DISTINCT FROM OLD.refund_conversion_reason) THEN
  RAISE EXCEPTION 'An authorized refund conversion cannot be undone or rewritten.';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_return_policy_version ON product_returns;
CREATE TRIGGER protect_return_policy_version BEFORE UPDATE ON product_returns FOR EACH ROW EXECUTE FUNCTION protect_return_policy_version();

CREATE OR REPLACE FUNCTION validate_replacement_first_policy() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c product_returns%ROWTYPE;
BEGIN
 SELECT * INTO c FROM product_returns WHERE return_id=NEW.return_id;
 IF c.return_policy_version=2 AND (c.replacement_workflow_version<>2
  OR (c.resolution='replace' AND (c.replacement_total<c.credit OR c.quote_revision<=0))
  OR (c.resolution='refund' AND c.refund_converted_at IS NULL)) THEN
  RAISE EXCEPTION 'New return requires the approved replacement policy or an authorized refund conversion.';
 END IF;
 RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS validate_replacement_first_policy ON product_returns;
CREATE CONSTRAINT TRIGGER validate_replacement_first_policy AFTER INSERT OR UPDATE ON product_returns
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_replacement_first_policy();


CREATE OR REPLACE FUNCTION protect_order_refund_audit_fields()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.order_id IS DISTINCT FROM OLD.order_id OR NEW.return_id IS DISTINCT FROM OLD.return_id
  OR NEW.original_credit_amount IS DISTINCT FROM OLD.original_credit_amount OR NEW.topup_amount IS DISTINCT FROM OLD.topup_amount
  OR NEW.amount IS DISTINCT FROM OLD.amount OR NEW.currency IS DISTINCT FROM OLD.currency
  OR NEW.requested_by_account_id IS DISTINCT FROM OLD.requested_by_account_id
  OR NEW.requested_at IS DISTINCT FROM OLD.requested_at THEN
  RAISE EXCEPTION 'Locked refund request fields cannot be changed.';
 END IF;
 RETURN NEW;
END $$;

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
 CASE WHEN f.amount IS NOT NULL AND f.amount<>CASE WHEN r.resolution='refund' THEN r.credit+CASE WHEN r.refund_converted_at IS NOT NULL AND r.payment_verified_at IS NOT NULL THEN COALESCE(r.payment_amount,0) ELSE 0 END ELSE GREATEST(0,r.credit-r.replacement_total) END
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

CREATE OR REPLACE VIEW sales_report_balances AS
SELECT sr.sales_report_id,COALESCE(s.gross_sales,0) gross_sales,
 COALESCE(s.completed_refunds,0) refunded_sales,COALESCE(s.retained_sales,0) retained_sales,
 COALESCE(s.refunded_at_submission,0) refunded_at_submission,
 GREATEST(0,COALESCE(s.completed_refunds,0)-COALESCE(s.refunded_at_submission,0)) late_refund_adjustment,
 COALESCE(s.retained_orders,0) retained_orders,
 CASE WHEN sr.refund_separation_version=0 THEN
  COALESCE(s.gross_sales,0)<>sr.total_sales OR COALESCE(s.mapped_orders,0)<>sr.total_orders
 ELSE COALESCE(s.gross_sales,0)-COALESCE(s.refunded_at_submission,0)<>sr.total_sales
 END OR COALESCE(s.excess_refund,false) reconciliation_required
FROM sales_reports sr LEFT JOIN LATERAL (
 SELECT SUM(x.gross_sales) gross_sales,SUM(x.refunded_at_submission) refunded_at_submission,
 SUM(COALESCE(f.amount,0)) completed_refunds,
 SUM(GREATEST(0,x.gross_sales-COALESCE(f.amount,0))) retained_sales,
 COUNT(*) mapped_orders,COUNT(*) FILTER(WHERE x.gross_sales>COALESCE(f.amount,0)) retained_orders,
 bool_or(COALESCE(f.amount,0)>x.gross_sales) excess_refund
 FROM sales_report_orders x LEFT JOIN LATERAL (
  SELECT SUM(original_credit_amount) amount FROM order_refunds WHERE order_id=x.order_id AND status='completed'
 ) f ON true WHERE x.sales_report_id=sr.sales_report_id
) s ON true WHERE sr.report_source='team_leader';

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
   SUM(f.original_credit_amount) FILTER(WHERE f.status='completed' AND r.resolution='refund') returns,
   SUM(f.original_credit_amount) FILTER(WHERE f.status='completed' AND r.resolution='replace') replacement
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
  CASE WHEN (SELECT COALESCE(SUM(original_credit_amount),0) FROM order_refunds WHERE order_id=b.order_id)>b.original_collections
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
