-- Preserve submitted figures and order membership while exposing refund adjustments.
ALTER TABLE sales_reports ADD COLUMN IF NOT EXISTS refund_separation_version smallint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS sales_report_orders (
 sales_report_id bigint NOT NULL REFERENCES sales_reports ON DELETE RESTRICT,
 order_id bigint NOT NULL REFERENCES orders ON DELETE RESTRICT,
 gross_sales numeric(12,2) NOT NULL CHECK(gross_sales>=0),
 refunded_at_submission numeric(12,2) NOT NULL CHECK(refunded_at_submission>=0 AND refunded_at_submission<=gross_sales),
 PRIMARY KEY(sales_report_id,order_id)
);
CREATE TABLE IF NOT EXISTS sales_report_refunds (
 sales_report_id bigint NOT NULL REFERENCES sales_reports ON DELETE RESTRICT,
 order_refund_id bigint NOT NULL REFERENCES order_refunds ON DELETE RESTRICT,
 amount numeric(12,2) NOT NULL CHECK(amount>0),
 PRIMARY KEY(sales_report_id,order_refund_id)
);
CREATE INDEX IF NOT EXISTS ix_report_order_membership ON sales_report_orders(order_id);
-- Legacy membership is recovered only when its original gross and order count match.
WITH candidates AS (
 SELECT sr.sales_report_id,o.order_id,o.total_amount gross_sales,sr.total_sales,sr.total_orders,
 COALESCE((SELECT SUM(f.amount) FROM order_refunds f WHERE f.order_id=o.order_id AND f.status='completed' AND f.completed_at<=sr.submitted_at),0) refunded
 FROM sales_reports sr JOIN orders o ON
  ((o.order_type='walk_in' AND o.created_by_account_id=sr.submitted_by_account_id)
   OR (o.order_type='reseller' AND o.team_leader_account_id=sr.submitted_by_account_id))
 WHERE sr.report_source='team_leader' AND sr.refund_separation_version=0 AND o.status='fulfilled'
 AND (COALESCE(o.fulfilled_at,o.order_date) AT TIME ZONE 'Asia/Manila')::date BETWEEN sr.period_start AND sr.period_end
 AND COALESCE(o.fulfilled_at,o.order_date::timestamptz)<=sr.submitted_at
), proven AS (
 SELECT sales_report_id FROM candidates GROUP BY sales_report_id
 HAVING SUM(gross_sales)=MAX(total_sales) AND COUNT(*)=MAX(total_orders) AND bool_and(refunded<=gross_sales)
)
INSERT INTO sales_report_orders(sales_report_id,order_id,gross_sales,refunded_at_submission)
SELECT c.sales_report_id,c.order_id,c.gross_sales,c.refunded FROM candidates c JOIN proven USING(sales_report_id)
ON CONFLICT DO NOTHING;
INSERT INTO sales_report_refunds(sales_report_id,order_refund_id,amount)
SELECT s.sales_report_id,f.order_refund_id,f.amount FROM sales_report_orders s
JOIN sales_reports r USING(sales_report_id) JOIN order_refunds f ON f.order_id=s.order_id
WHERE f.status='completed' AND f.completed_at<=r.submitted_at AND r.refund_separation_version=0
ON CONFLICT DO NOTHING;

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
  SELECT SUM(amount) amount FROM order_refunds WHERE order_id=x.order_id AND status='completed'
 ) f ON true WHERE x.sales_report_id=sr.sales_report_id
) s ON true WHERE sr.report_source='team_leader';

DROP TRIGGER IF EXISTS immutable_sales_report_orders ON sales_report_orders;
CREATE TRIGGER immutable_sales_report_orders BEFORE UPDATE OR DELETE OR TRUNCATE ON sales_report_orders
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
DROP TRIGGER IF EXISTS immutable_sales_report_refunds ON sales_report_refunds;
CREATE TRIGGER immutable_sales_report_refunds BEFORE UPDATE OR DELETE OR TRUNCATE ON sales_report_refunds
 FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
