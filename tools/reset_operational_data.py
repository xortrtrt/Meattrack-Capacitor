"""Explicit maintenance reset; stop the app and worker before --apply.

Keeps account records, linked reseller profiles, activation tokens, finished
product definitions, and migration history. No backup is made by this tool.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import psycopg2
from psycopg2 import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.business_time import business_today
from app.config import database_dsn


PRESERVED = {
    "accounts", "resellers", "account_activation_tokens", "inventory_items",
    "schema_migrations",
}
# These use DELETE because tables retained in place reference them.
DELETED = {"inquiries", "chat_conversations"}
CLEARED = set("""
account_login_otps account_password_otps activity_logs alerts business_settings
chat_messages chat_service_ratings departments expired_holdings
expired_pullouts expired_stock_movements forecast_product_summaries forecast_results
forecast_runs fulfillment_allocations inventory_batches inventory_movements
notification_outbox notification_recipients notifications order_items
order_payment_proofs order_refund_attempts order_refund_messages order_refund_proofs
order_refunds order_stock_reservations order_system_messages orders
owner_step_up_challenges product_recipes product_return_events product_return_lines
product_return_receipt_lines product_return_receipts product_returns rate_limit_buckets
refund_operations replacement_quote_lines replacement_releases
replacement_stock_reservations reseller_cart_items reseller_conversations
reseller_messages return_attachments return_customer_followups return_followup_messages
return_offer_snapshots sales_report_activity_snapshots sales_report_attachments
sales_report_items sales_report_orders sales_report_refunds sales_reports
security_events system_status_reports team_leader_presence user_consents web_sessions
""".split())


def table_name(name):
    return sql.Identifier("public", name)


def snapshot(cur, query):
    cur.execute(query)
    return cur.fetchall()


def reset(conn, expiry: date, *, apply: bool = False):
    today = business_today()
    if expiry <= today:
        raise ValueError("Starting stock expiry must be after today's Manila date.")
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '10s'")
        cur.execute("SET LOCAL statement_timeout = '120s'")
        cur.execute("SET LOCAL TIME ZONE 'Asia/Manila'")
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        tables = {row[0] for row in cur.fetchall()}
        expected = PRESERVED | DELETED | CLEARED
        if tables != expected:
            raise RuntimeError(f"Schema needs review: extra={tables-expected}, missing={expected-tables}")
        cur.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
            sql.SQL(", ").join(table_name(t) for t in sorted(tables))))

        accounts = snapshot(cur, "SELECT * FROM accounts ORDER BY account_id")
        products = snapshot(cur, "SELECT * FROM inventory_items WHERE item_type='finished_product' ORDER BY item_id")
        migrations = snapshot(cur, "SELECT * FROM schema_migrations ORDER BY version")
        activation = snapshot(cur, "SELECT * FROM account_activation_tokens ORDER BY activation_token_id")
        resellers = snapshot(cur, """SELECT to_jsonb(r)-'inquiry_id' FROM resellers r
            WHERE EXISTS (SELECT 1 FROM accounts a WHERE a.reseller_id=r.reseller_id)
            ORDER BY reseller_id""")
        if not accounts or not products:
            raise RuntimeError("Expected existing accounts and finished products.")
        triggers = snapshot(cur, """SELECT c.relname,t.tgname,t.tgenabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2""")

        # Disable only the explicit immutable-ledger TRUNCATE guards. Foreign keys
        # and all INSERT validation remain active; DDL rolls back on any failure.
        cur.execute("""SELECT c.relname,t.tgname,t.tgenabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal
              AND (t.tgtype::int & 32)<>0 AND c.relname=ANY(%s)""", (sorted(CLEARED),))
        guards = cur.fetchall()
        for table, trigger, _ in guards:
            cur.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                table_name(table), sql.Identifier(trigger)))
        cur.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY RESTRICT").format(
            sql.SQL(", ").join(table_name(t) for t in sorted(CLEARED))))
        for table, trigger, enabled in guards:
            mode = {"O": "ENABLE", "D": "DISABLE", "R": "ENABLE REPLICA", "A": "ENABLE ALWAYS"}[enabled]
            cur.execute(sql.SQL("ALTER TABLE {} {} TRIGGER {}").format(
                table_name(table), sql.SQL(mode), sql.Identifier(trigger)))
        cur.execute("DELETE FROM inquiries")
        cur.execute("DELETE FROM chat_conversations")
        cur.execute("DELETE FROM resellers r WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.reseller_id=r.reseller_id)")
        cur.execute("DELETE FROM inventory_items WHERE item_type<>'finished_product'")
        cur.execute("""INSERT INTO inventory_batches
            (item_id,batch_code,source_type,quantity_received,quantity_available,
             unit,received_date,expiry_date,quality_status)
            SELECT item_id,'OPENING-' || %s || '-' || item_id || '-' || batch_number,
                   'direct_received',10,10,unit,%s,%s::date+(batch_number-1)*15,'approved'
            FROM inventory_items CROSS JOIN generate_series(1,5) AS batch_number
            WHERE item_type='finished_product'""",
            (today.strftime("%Y%m%d"), today, expiry))
        cur.execute("""INSERT INTO inventory_movements
            (item_id,affected_batch_id,movement_type,quantity_delta,unit,
             balance_before,balance_after,actor_name,note)
            SELECT item_id,batch_id,'opening_balance',10,unit,0,10,'MEATTRACK',
                   'User-authorized reset: 50 starting packs per product in five 10-pack batches'
            FROM inventory_batches""")

        checks = [
            (accounts, "SELECT * FROM accounts ORDER BY account_id"),
            (products, "SELECT * FROM inventory_items WHERE item_type='finished_product' ORDER BY item_id"),
            (migrations, "SELECT * FROM schema_migrations ORDER BY version"),
            (activation, "SELECT * FROM account_activation_tokens ORDER BY activation_token_id"),
            (resellers, """SELECT to_jsonb(r)-'inquiry_id' FROM resellers r
                WHERE EXISTS (SELECT 1 FROM accounts a WHERE a.reseller_id=r.reseller_id)
                ORDER BY reseller_id"""),
            (triggers, """SELECT c.relname,t.tgname,t.tgenabled
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2"""),
        ]
        for before, query in checks:
            if snapshot(cur, query) != before:
                raise RuntimeError("Preserved data or trigger configuration changed; rolling back.")
        for table in sorted((CLEARED | DELETED)-{"inventory_batches", "inventory_movements"}):
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(table_name(table)))
            if cur.fetchone()[0]:
                raise RuntimeError(f"Table {table} was not emptied.")
        cur.execute("""SELECT i.item_id FROM inventory_items i
            LEFT JOIN inventory_batches b USING(item_id)
            LEFT JOIN inventory_movements m ON m.affected_batch_id=b.batch_id
            GROUP BY i.item_id HAVING count(b.batch_id)<>5 OR count(m.movement_id)<>5
              OR count(DISTINCT b.expiry_date)<>5
              OR sum(b.quantity_available)<>50 OR sum(m.quantity_delta)<>50
              OR bool_or(b.quantity_received<>10 OR b.quantity_available<>10
                         OR m.quantity_delta<>10 OR m.balance_before<>0 OR m.balance_after<>10)
              OR bool_or(b.quality_status<>'approved' OR b.expiry_date<=%s)""", (today,))
        if cur.fetchall():
            raise RuntimeError("Starting stock validation failed.")
        result = {"accounts_preserved": len(accounts), "reseller_profiles_preserved": len(resellers),
                  "finished_products_preserved": len(products), "packs_each": 50,
                  "total_packs": len(products)*50, "batches_each": 5, "packs_per_batch": 10,
                  "expiry_dates": [(expiry+timedelta(days=15*n)).isoformat() for n in range(5)],
                  "committed": apply}
    if apply:
        conn.commit()
    else:
        conn.rollback()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expiry-date", type=date.fromisoformat, required=True,
                        help="First expiry date; the next four are each 15 days apart")
    parser.add_argument("--apply", action="store_true", help="Commit the permanent reset; default is a full rollback rehearsal")
    args = parser.parse_args()
    conn = psycopg2.connect(database_dsn(), connect_timeout=10)
    try:
        print(json.dumps(reset(conn, args.expiry_date, apply=args.apply), indent=2))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
