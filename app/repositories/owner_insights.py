"""Business-wide Owner reads. Queue counts overlap and are never added together."""

from app.database import clean_row, fetch_all, fetch_one
from .superuser import business_setting
from .coverage_filters import INQUIRY_COVERAGE, RESELLER_COVERAGE, REFUND_COVERAGE


def owner_business_snapshot() -> dict:
    row = fetch_one(f"""SELECT
        (SELECT COUNT(*) FROM orders WHERE order_type='reseller' AND status='to_pay') unpaid_orders,
        (SELECT COUNT(*) FROM orders WHERE order_type='reseller' AND status='pending') paid_review,
        (SELECT COUNT(*) FROM orders WHERE order_type='reseller' AND status='approved') ready_fulfillment,
        (SELECT COUNT(*) FROM inquiries WHERE status IN ('pending','assigned','contacted')) open_inquiries,
        (SELECT COUNT(*) FROM inquiries i WHERE {INQUIRY_COVERAGE}) unassigned_inquiries,
        (SELECT COUNT(*) FROM resellers WHERE reseller_status='active') active_resellers,
        (SELECT COUNT(*) FROM resellers r WHERE r.reseller_status='active'
          AND {RESELLER_COVERAGE}) unassigned_resellers,
        (SELECT COUNT(*) FROM product_returns WHERE status='submitted') new_returns,
        (SELECT COUNT(*) FROM product_returns WHERE status='discrepancy') return_mismatches,
        (SELECT COUNT(*) FROM product_returns r LEFT JOIN accounts a ON a.account_id=r.processing_supervisor_account_id
          WHERE r.handed_off_at IS NOT NULL AND (r.status NOT IN ('completed','rejected') OR EXISTS(
            SELECT 1 FROM return_customer_followups f WHERE f.return_id=r.return_id AND f.status='open'))
          AND (a.account_id IS NULL OR NOT a.is_active OR a.team_leader_role IS DISTINCT FROM 'sales-supervisor')) owner_returns,
        (SELECT COUNT(*) FROM return_customer_followups WHERE status='open') open_followups,
        (SELECT COUNT(*) FROM order_refunds WHERE status='pending') pending_refunds,
        (SELECT COUNT(*) FROM order_refunds WHERE status='processing') processing_refunds,
        (SELECT COUNT(*) FROM order_refunds WHERE status='failed') failed_refunds,
        (SELECT COUNT(*) FROM order_refunds r WHERE {REFUND_COVERAGE}) owner_refunds,
        (SELECT COUNT(*) FROM alerts WHERE status IN ('open','acknowledged')) open_alerts,
        (SELECT COUNT(*) FROM accounts WHERE activation_status='pending' AND is_active) pending_activations
    """)
    return {key: int(value or 0) for key, value in row.items()}


def owner_stock_snapshot(batch_limit: int | None = 6) -> dict:
    near_days = int(business_setting("inventory.near_expiry_days"))
    counts = fetch_one("""WITH stock AS (
        SELECT p.item_id,
          COALESCE(SUM(b.quantity_available),0) physical,
          COALESCE(SUM(b.quantity_available) FILTER(WHERE b.quality_status='approved'
            AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date),0) eligible,
          COALESCE(SUM(b.quantity_available) FILTER(WHERE b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date),0) expired,
          COALESCE(SUM(b.quantity_available) FILTER(WHERE b.quality_status='approved'
            AND b.expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date
            AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+%s),0) expiring,
          COALESCE((SELECT SUM(r.quantity) FROM all_stock_reservations r WHERE r.product_id=p.item_id),0) reserved,
          EXISTS(SELECT 1 FROM product_recipes pr WHERE pr.product_item_id=p.item_id) has_recipe
        FROM inventory_items p LEFT JOIN inventory_batches b ON b.item_id=p.item_id
        WHERE p.item_type='finished_product' AND p.is_active GROUP BY p.item_id
    ) SELECT COUNT(*) products,COALESCE(SUM(physical),0) physical,
      COALESCE(SUM(GREATEST(eligible-reserved,0)),0) available,
      COALESCE(SUM(reserved),0) reserved,COALESCE(SUM(expired),0) expired,
      COALESCE(SUM(expiring),0) expiring,
      COUNT(*) FILTER(WHERE eligible<=reserved) out_of_stock,
      COUNT(*) FILTER(WHERE NOT has_recipe) missing_recipes
    FROM stock""", (near_days,))
    batches = fetch_all("""SELECT b.batch_id,b.item_id product_id,p.name,b.batch_code,b.expiry_date,
        b.quantity_available quantity,
        b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date expired
        FROM inventory_batches b JOIN inventory_items p ON p.item_id=b.item_id
        WHERE p.item_type='finished_product' AND p.is_active AND b.quantity_available>0
          AND b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date+%s
          AND (b.quality_status='approved' OR b.expiry_date<=(now() AT TIME ZONE 'Asia/Manila')::date)
        ORDER BY b.expiry_date,b.batch_id LIMIT %s""", (near_days, batch_limit))
    return {"counts": clean_row(counts), "batches": clean_row(batches), "near_days": near_days}


__all__ = ["owner_business_snapshot", "owner_stock_snapshot"]


def owner_low_stock(threshold: int = 10) -> list[dict]:
    """Finished packs available to promise, after all active reservations."""
    return clean_row(fetch_all("""SELECT item_id,name,available FROM finished_stock_availability
        WHERE is_active AND available<=%s ORDER BY available,name,item_id""", (threshold,)))
