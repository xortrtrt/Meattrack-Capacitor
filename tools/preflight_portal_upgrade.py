"""Read-only payment preflight for migration 040. Does not reconcile or alter orders."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import fetch_all


def main() -> int:
    orders = fetch_all("""SELECT order_id FROM orders WHERE order_type='reseller'
        AND status IN ('pending','approved') AND (payment_amount IS NULL OR payment_amount<total_amount)
        ORDER BY order_id""")
    if orders:
        print(f"Migration 040 blocked: {len(orders)} active reseller order(s) need payment reconciliation.")
        print("Order IDs: " + ", ".join(str(row["order_id"]) for row in orders))
        print("An Owner must reconcile the original payment and order before the upgrade. No records were changed.")
        return 1
    print("Payment preflight passed. No active underpaid reseller orders found. No records were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
