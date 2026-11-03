"""Local maintenance kernel; CLI requires a separately restored, verified backup.

Keep stock/custody rows and detach their archived transaction attribution. Never
reverse a physical movement or reset identity sequences. All DDL is transactional.
"""
from hashlib import sha256
import json

from psycopg2 import sql
from psycopg2.extras import RealDictCursor


TRANSACTIONS = set("""
orders order_items order_payment_proofs order_stock_reservations order_reservation_events order_system_messages
order_refunds order_refund_attempts order_refund_proofs order_refund_messages refund_message_reads refund_operations
fulfillment_allocations product_returns product_return_lines product_return_events product_return_receipts
product_return_receipt_lines replacement_quote_lines replacement_releases replacement_stock_reservations
return_attachments return_offer_snapshots return_customer_followups return_followup_messages
return_conversation_messages return_conversation_reads reseller_order_receipts
sales_reports sales_report_items sales_report_attachments sales_report_orders sales_report_refunds sales_report_activity_snapshots
forecast_runs forecast_results forecast_product_summaries
""".split())

DETACHED = {("inventory_movements", "order_id"), ("expired_holdings", "return_line_id"),
            ("reseller_messages", "order_id"), ("reseller_messages", "return_id")}
PRESERVED_TABLES = """accounts resellers inventory_items inventory_batches product_recipes business_settings
inventory_package_settings inventory_package_components inventory_tool_operations inventory_package_dispatches""".split()


def require_loopback(conn):
    host = conn.get_dsn_parameters().get("host", "")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Transaction reset requires a verified local loopback database target.")


def _digest(rows):
    return sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _rows(cur, query, params=None):
    cur.execute(query, params)
    return [dict(r) for r in cur.fetchall()]


def _triggers(cur):
    return _rows(cur, """SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY 1,2""")


def _preserved(cur):
    result = {table: _rows(cur, sql.SQL("SELECT * FROM {} ORDER BY to_jsonb({})::text").format(
        sql.Identifier(table), sql.Identifier(table))) for table in PRESERVED_TABLES}
    cur.execute("SELECT to_regclass('schema_migrations') present")
    if cur.fetchone()["present"]:
        result["schema_migrations"] = _rows(cur, "SELECT * FROM schema_migrations ORDER BY version")
    result["movements"] = _rows(cur, "SELECT to_jsonb(m)-'order_id' value FROM inventory_movements m ORDER BY movement_id")
    result["holdings"] = _rows(cur, "SELECT to_jsonb(h)-'return_line_id' value FROM expired_holdings h ORDER BY holding_id")
    result["custody"] = _rows(cur, "SELECT * FROM expired_stock_movements ORDER BY movement_id")
    result["messages"] = _rows(cur, "SELECT * FROM reseller_messages WHERE order_id IS NULL AND return_id IS NULL ORDER BY reseller_message_id")
    result["message_attachments"] = _rows(cur, """SELECT a.* FROM reseller_message_attachments a JOIN reseller_messages m USING(reseller_message_id)
        WHERE m.order_id IS NULL AND m.return_id IS NULL ORDER BY attachment_id""")
    return result


def reset_transactions(conn, *, apply=False, preflight=None):
    require_loopback(conn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL lock_timeout='10s'")
            cur.execute("SET LOCAL statement_timeout='120s'")
            tables = {r["tablename"] for r in _rows(cur, "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
            missing = TRANSACTIONS.difference(tables)
            if missing:
                raise RuntimeError(f"Schema needs review: missing transaction tables {sorted(missing)}")
            cur.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.SQL(',').join(
                sql.Identifier(table) for table in sorted(tables))))
            if preflight is not None:
                preflight(conn)
            edges = _rows(cur, """SELECT child.relname child,parent.relname parent,a.attname column_name
                FROM pg_constraint f JOIN pg_class child ON child.oid=f.conrelid
                JOIN pg_class parent ON parent.oid=f.confrelid JOIN pg_namespace n ON n.oid=child.relnamespace
                CROSS JOIN LATERAL unnest(f.conkey) k(attnum) JOIN pg_attribute a ON a.attrelid=child.oid AND a.attnum=k.attnum
                WHERE f.contype='f' AND n.nspname=current_schema()""")
            for edge in edges:
                if edge["parent"] in TRANSACTIONS and edge["child"] not in TRANSACTIONS and (edge["child"], edge["column_name"]) not in DETACHED:
                    raise RuntimeError("Unreviewed transaction dependency: " + edge["child"] + "." + edge["column_name"])
            preserved = _preserved(cur)
            triggers = _triggers(cur)
            changed = TRANSACTIONS | {t for t, _ in DETACHED} | {"reseller_message_attachments"}
            for trigger in triggers:
                if trigger["relname"] in changed:
                    cur.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                        sql.Identifier(trigger["relname"]), sql.Identifier(trigger["tgname"])))
            # Generic references have no FK, but must not continue pointing at deleted records.
            cur.execute("""DELETE FROM notification_outbox WHERE payload->>'notification_id' IN (
                SELECT notification_id::text FROM notifications WHERE source_type=ANY(%s)
                OR target_url ~ '([?&](order|case|return|refund|system_message)=|/orders|/order/|#order-)')""", (sorted(TRANSACTIONS),))
            cur.execute("""DELETE FROM notifications WHERE source_type=ANY(%s)
                OR target_url ~ '([?&](order|case|return|refund|system_message)=|/orders|/order/|#order-)'""", (sorted(TRANSACTIONS),))
            cur.execute("DELETE FROM activity_logs WHERE entity_type=ANY(%s)", (sorted(TRANSACTIONS),))
            # Follow-up bridges are removed before deleting only transaction-linked messages.
            cur.execute("DELETE FROM return_followup_messages")
            cur.execute("""DELETE FROM reseller_message_attachments WHERE reseller_message_id IN (
                SELECT reseller_message_id FROM reseller_messages WHERE order_id IS NOT NULL OR return_id IS NOT NULL)""")
            cur.execute("DELETE FROM reseller_messages WHERE order_id IS NOT NULL OR return_id IS NOT NULL")
            cur.execute("UPDATE inventory_movements SET order_id=NULL WHERE order_id IS NOT NULL")
            cur.execute("UPDATE expired_holdings SET return_line_id=NULL WHERE return_line_id IS NOT NULL")
            remaining = set(TRANSACTIONS)
            counts = {}
            while remaining:
                leaves = sorted(t for t in remaining if not any(
                    e["parent"] == t and e["child"] in remaining and e["child"] != t for e in edges))
                if not leaves:
                    raise RuntimeError("Transaction dependency cycle needs explicit review.")
                for table in leaves:
                    cur.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
                    counts[table] = cur.rowcount
                    remaining.remove(table)
            for trigger in triggers:
                if trigger["relname"] in changed:
                    mode = {"O": "ENABLE", "D": "DISABLE", "R": "ENABLE REPLICA", "A": "ENABLE ALWAYS"}[trigger["tgenabled"]]
                    cur.execute(sql.SQL("ALTER TABLE {} {} TRIGGER {}").format(
                        sql.Identifier(trigger["relname"]), sql.SQL(mode), sql.Identifier(trigger["tgname"])))
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            if preserved != _preserved(cur) or triggers != _triggers(cur):
                raise RuntimeError("Preserved data or trigger state changed; reset rolled back.")
            for table in sorted(TRANSACTIONS):
                cur.execute(sql.SQL("SELECT count(*) n FROM {}").format(sql.Identifier(table)))
                if cur.fetchone()["n"]:
                    raise RuntimeError("Transaction table did not empty: " + table)
            result = {"committed": bool(apply), "cleared": counts, "preserved_digest": _digest(preserved)}
        if apply:
            conn.commit()
        else:
            conn.rollback()
        return result
    except Exception:
        conn.rollback()
        raise
