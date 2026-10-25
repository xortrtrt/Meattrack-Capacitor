from __future__ import annotations


from ._shared import *
from .accounts import require_reseller_team_leader, reseller_account_profile
from .notifications import _create_notification_cursor
from .report_settlements import report_order_rows, report_totals, snapshot_report_orders


def settlement_summary(actor_id: int) -> dict:
    from .product_returns import return_settlement_totals
    return return_settlement_totals(actor_id)


def list_sales_reports(
    report_source: str | None = None,
    limit: int | None = None,
    q: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
    page: int | None = None,
    page_size: int = 10,
    sort: str = "",
) -> list[dict]:
    if report_source not in {None, "reseller", "team_leader"}:
        raise ValueError("report_source must be reseller, team_leader, or None")
    query = """
        SELECT sr.sales_report_id, sr.report_source,
               COALESCE(a.name, 'System') AS submitted_by,
               COALESCE(r.business_name, '') AS reseller,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               sr.period_start, sr.period_end, sr.notes, sr.submitted_at,
               sr.total_sales submitted_total_sales,sr.total_orders submitted_total_orders,
               sr.refund_separation_version,
               CASE WHEN sr.report_source='team_leader' THEN b.retained_sales ELSE sr.total_sales END total_sales,
               CASE WHEN sr.report_source='team_leader' THEN b.retained_orders ELSE sr.total_orders END total_orders,
               b.gross_sales,b.refunded_sales,b.refunded_at_submission,b.late_refund_adjustment,b.reconciliation_required
        FROM sales_reports sr
        LEFT JOIN sales_report_balances b USING(sales_report_id)
        LEFT JOIN accounts a ON a.account_id = sr.submitted_by_account_id
        LEFT JOIN resellers r ON r.reseller_id = sr.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
    """
    where = []
    params: list[object] = []
    if report_source:
        where.append("sr.report_source = %s")
        params.append(report_source)
    if team_leader_account_id is not None:
        if report_source == "team_leader":
            where.append("sr.submitted_by_account_id = %s")
            params.append(team_leader_account_id)
        elif report_source == "reseller":
            where.append("r.team_leader_account_id = %s")
            params.append(team_leader_account_id)
        else:
            where.append("(sr.submitted_by_account_id = %s OR r.team_leader_account_id = %s)")
            params.extend([team_leader_account_id, team_leader_account_id])
    if reseller_account_id is not None:
        where.append("""sr.reseller_id = (
            SELECT a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
        )""")
        params.append(reseller_account_id)
    q = q.strip()
    if q:
        where.append("(COALESCE(a.name, 'System') ILIKE %s OR sr.notes ILIKE %s OR CAST(sr.sales_report_id AS text) ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if where:
        query += " WHERE " + " AND ".join(where)
    sorts={**REPORT_SORTS,"sales_desc":"total_sales DESC NULLS LAST, sr.sales_report_id DESC","orders_desc":"total_orders DESC, sr.sales_report_id DESC"}
    query += f" ORDER BY {safe_sort_sql(sort, sorts, sorts['newest'])}"
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        query += " LIMIT %s"
        params.append(limit)
    elif page is not None:
        if page < 1:
            raise ValueError("page must be positive")
        query += " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    reports = clean_row(fetch_all(query + ";", tuple(params) or None))
    if not reports:
        return reports

    report_ids = [report["sales_report_id"] for report in reports]
    items = clean_row(fetch_all("""
        SELECT sri.sales_report_id,
               p.name,
               sri.quantity_sold,
               sri.unit,
               sri.unit_price,
               sri.line_total
        FROM sales_report_items sri
        JOIN inventory_items p ON p.item_id = sri.product_id
        WHERE sri.sales_report_id = ANY(%s)
        ORDER BY p.name;
    """, (report_ids,)))
    items_by_report: dict[int, list[dict]] = {}
    for item in items:
        report_id = item.pop("sales_report_id")
        items_by_report.setdefault(report_id, []).append(item)
    for report in reports:
        report["items"] = items_by_report.get(report["sales_report_id"], [])
    attachments = clean_row(fetch_all("""
        SELECT sales_report_id,
               filename,
               content_type,
               size_bytes,
               checksum_sha256,
               uploaded_at
        FROM sales_report_attachments
        WHERE sales_report_id = ANY(%s)
        ORDER BY uploaded_at DESC, sales_report_attachment_id DESC;
    """, (report_ids,)))
    attachments_by_report: dict[int, list[dict]] = {}
    for attachment in attachments:
        report_id = attachment.pop("sales_report_id")
        attachments_by_report.setdefault(report_id, []).append(attachment)
    for report in reports:
        report["attachments"] = attachments_by_report.get(report["sales_report_id"], [])
    order_rows=clean_row(fetch_all("""SELECT s.sales_report_id,s.order_id,s.gross_sales,
        COALESCE(f.amount,0) refunded_sales,GREATEST(0,s.gross_sales-COALESCE(f.amount,0)) retained_sales
        FROM sales_report_orders s LEFT JOIN LATERAL(SELECT SUM(amount) amount FROM order_refunds
          WHERE order_id=s.order_id AND status='completed') f ON true
        WHERE s.sales_report_id=ANY(%s) ORDER BY s.order_id;""",(report_ids,)))
    refunds=clean_row(fetch_all("""SELECT s.sales_report_id,f.order_refund_id,f.order_id,f.return_id,f.amount,f.completed_at,
        NOT EXISTS(SELECT 1 FROM sales_report_refunds x WHERE x.sales_report_id=s.sales_report_id AND x.order_refund_id=f.order_refund_id) late_adjustment
        FROM sales_report_orders s JOIN order_refunds f ON f.order_id=s.order_id
        WHERE s.sales_report_id=ANY(%s) AND f.status='completed' ORDER BY f.completed_at,f.order_refund_id;""",(report_ids,)))
    for report in reports:
        report["order_rows"]=[row for row in order_rows if row["sales_report_id"]==report["sales_report_id"]]
        report["refund_rows"]=[row for row in refunds if row["sales_report_id"]==report["sales_report_id"]]
    return reports


def count_sales_reports(
    report_source: str | None = None,
    q: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> int:
    if report_source not in {None, "reseller", "team_leader"}:
        raise ValueError("report_source must be reseller, team_leader, or None")
    query = """
        SELECT COUNT(*) AS total
        FROM sales_reports sr
        LEFT JOIN accounts a ON a.account_id = sr.submitted_by_account_id
        LEFT JOIN resellers r ON r.reseller_id = sr.reseller_id
    """
    where = []
    params: list[object] = []
    if report_source:
        where.append("sr.report_source = %s")
        params.append(report_source)
    if team_leader_account_id is not None:
        if report_source == "team_leader":
            where.append("sr.submitted_by_account_id = %s")
            params.append(team_leader_account_id)
        elif report_source == "reseller":
            where.append("r.team_leader_account_id = %s")
            params.append(team_leader_account_id)
        else:
            where.append("(sr.submitted_by_account_id = %s OR r.team_leader_account_id = %s)")
            params.extend([team_leader_account_id, team_leader_account_id])
    if reseller_account_id is not None:
        where.append("""sr.reseller_id = (
            SELECT a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
        )""")
        params.append(reseller_account_id)
    q = q.strip()
    if q:
        where.append("(COALESCE(a.name, 'System') ILIKE %s OR sr.notes ILIKE %s OR CAST(sr.sales_report_id AS text) ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def _activity_log_filters(
    q: str,
    inventory_only: bool,
    actor_role: str,
    module: str,
    action: str,
    outcome: str,
    date_from: str,
    date_to: str,
) -> tuple[list[str], list[object]]:
    where = []
    params: list[object] = []
    q = q.strip()
    if inventory_only:
        where.append("al.action = ANY(%s)")
        params.append(list(INVENTORY_LOG_ACTIONS))
    if q:
        where.append("(COALESCE(a.name, 'MEATTRACK') ILIKE %s OR al.action ILIKE %s OR al.entity_type ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if actor_role.strip():
        where.append("al.actor_role = %s")
        params.append(actor_role.strip())
    if module.strip():
        where.append("al.entity_type = %s")
        params.append(module.strip())
    if action.strip():
        where.append("al.action = %s")
        params.append(action.strip())
    if outcome in {"success", "failure"}:
        where.append("al.outcome = %s")
        params.append(outcome)
    if date_from.strip():
        where.append("al.created_at >= %s::date")
        params.append(date_from.strip())
    if date_to.strip():
        where.append("al.created_at < %s::date + interval '1 day'")
        params.append(date_to.strip())
    return where, params


def list_activity_logs(
    q: str = "",
    page: int | None = None,
    page_size: int = 10,
    inventory_only: bool = False,
    sort: str = "",
    actor_role: str = "",
    module: str = "",
    action: str = "",
    outcome: str = "",
    date_from: str = "",
    date_to: str = "",
) -> list[dict]:
    where, params = _activity_log_filters(
        q, inventory_only, actor_role, module, action, outcome, date_from, date_to
    )
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, LOG_SORTS, LOG_SORTS["newest"])
    return clean_row(fetch_all("""
        SELECT al.activity_log_id,
               COALESCE(a.name, 'MEATTRACK') AS actor,
               al.action,
               al.actor_role,
               al.reason,
               al.outcome,
               al.before_state,
               al.after_state,
               al.request_id,
               (CASE
                    WHEN al.entity_type = 'orders' THEN 'Order #' || al.entity_id
                    WHEN al.entity_type = 'products' THEN (SELECT name FROM inventory_items WHERE item_id = al.entity_id)
                    WHEN al.entity_type = 'sales_reports' THEN 'Report #' || al.entity_id
                    WHEN al.entity_type = 'departments' THEN (SELECT department_name FROM departments WHERE department_id = al.entity_id)
                    WHEN al.entity_type = 'forecast_runs' THEN 'Daily product demand'
                    ELSE COALESCE(al.entity_type, 'System')
                END) AS entity,
               al.created_at
        FROM activity_logs al
        LEFT JOIN accounts a ON a.account_id = al.account_id
        {where_sql}
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=where_sql, order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


def count_activity_logs(
    q: str = "",
    inventory_only: bool = False,
    actor_role: str = "",
    module: str = "",
    action: str = "",
    outcome: str = "",
    date_from: str = "",
    date_to: str = "",
) -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM activity_logs al
        LEFT JOIN accounts a ON a.account_id = al.account_id
    """
    where, params = _activity_log_filters(
        q, inventory_only, actor_role, module, action, outcome, date_from, date_to
    )
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def current_metrics(team_leader_account_id: int | None = None, reseller_account_id: int | None = None) -> dict:
    order_scope = ""
    pending_scope = ""
    active_reseller_scope = ""
    params: list[object] = []
    if reseller_account_id is not None:
        profile = reseller_account_profile(reseller_account_id)
        order_scope = " AND reseller_id = %s"
        pending_scope = " AND reseller_id = %s"
        active_reseller_scope = " AND reseller_id = %s"
        params.extend([profile["reseller_id"], profile["reseller_id"], profile["reseller_id"]])
    elif team_leader_account_id is not None:
        order_scope = """
                AND (
                    created_by_account_id = %s
                    OR team_leader_account_id = %s
                )
        """
        pending_scope = " AND team_leader_account_id = %s"
        active_reseller_scope = " AND team_leader_account_id = %s"
        params.extend([team_leader_account_id, team_leader_account_id, team_leader_account_id, team_leader_account_id])

    row = fetch_one(f"""
        SELECT
            COALESCE((
                SELECT SUM(total_amount)
                FROM orders
                WHERE status = 'fulfilled'
                {order_scope}
            ), 0) AS fulfilled_sales,
            (
                SELECT COUNT(*)
                FROM orders
                WHERE order_type = 'reseller' AND status = 'pending'
                {pending_scope}
            ) AS pending_reseller_orders,
            (
                SELECT COUNT(*)
                FROM alerts
                WHERE status = 'open'
            ) AS open_alerts,
            (
                SELECT COUNT(*)
                FROM resellers
                WHERE reseller_status = 'active'
                {active_reseller_scope}
            ) AS active_resellers,
            GREATEST(COALESCE((
                SELECT SUM(ib.quantity_available)
                FROM inventory_batches ib
                JOIN inventory_items ii ON ii.item_id = ib.item_id
                WHERE ii.item_type = 'finished_product'
                  AND ib.quality_status = 'approved'
                  AND (ib.expiry_date IS NULL OR ib.expiry_date > (now() AT TIME ZONE 'Asia/Manila')::date)
            ), 0) - COALESCE((SELECT SUM(quantity) FROM all_stock_reservations),0),0) AS total_available;
    """, tuple(params) or None)

    return {
        "fulfilled_sales": float(row["fulfilled_sales"]),
        "pending_reseller_orders": int(row["pending_reseller_orders"]),
        "open_alerts": int(row["open_alerts"]),
        "active_resellers": int(row["active_resellers"]),
        "total_available": float(row["total_available"]),
    }


def inventory_product_movement_analytics(days: int = 30, limit: int = 8) -> list[dict]:
    clean_days = max(1, min(int(days), 365))
    clean_limit = max(1, min(int(limit), 20))
    return clean_row(fetch_all("""
        SELECT p.item_id AS product_id,
               p.name,
               p.category,
               p.unit,
               GREATEST(COALESCE(stock.total_available, 0) - COALESCE((SELECT SUM(r.quantity) FROM all_stock_reservations r WHERE r.product_id=p.item_id),0),0) AS total_available,
               COALESCE(product_in.total_in, 0) AS total_in,
               COALESCE(product_out.total_out, 0) AS total_out
        FROM inventory_items p
        LEFT JOIN (
            SELECT item_id, SUM(quantity_available) AS total_available
            FROM inventory_batches
            WHERE quality_status = 'approved'
              AND (expiry_date IS NULL OR expiry_date > (now() AT TIME ZONE 'Asia/Manila')::date)
            GROUP BY item_id
        ) stock ON stock.item_id = p.item_id
        LEFT JOIN (
            SELECT item_id, SUM(quantity_received) AS total_in
            FROM inventory_batches
            WHERE received_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
            GROUP BY item_id
        ) product_in ON product_in.item_id = p.item_id
        LEFT JOIN (
            SELECT oi.product_id, SUM(oi.quantity) AS total_out
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            WHERE o.status = 'fulfilled'
              AND COALESCE(o.fulfilled_at::date, o.order_date) >= CURRENT_DATE - (%s * INTERVAL '1 day')
            GROUP BY oi.product_id
        ) product_out ON product_out.product_id = p.item_id
        WHERE p.item_type = 'finished_product'
          AND p.is_active = true
        ORDER BY (COALESCE(product_in.total_in, 0) + COALESCE(product_out.total_out, 0)) DESC,
                 p.name
        LIMIT %s;
    """, (clean_days, clean_days, clean_limit)))


def team_sales_report_totals(period_start: date, period_end: date, team_leader_account_id: int | None = None) -> dict:
    with get_transaction_cursor() as cur:
        return clean_row(report_totals(report_order_rows(cur,period_start,period_end,team_leader_account_id)))


def team_sales_report_entries(team_leader_account_id: int | None = None) -> list[dict]:
    scope_sql = ""
    params: list[object] = []
    if team_leader_account_id is not None:
        scope_sql = """
          AND (
              (o.order_type = 'walk_in' AND o.created_by_account_id = %s)
              OR (o.order_type = 'reseller' AND o.team_leader_account_id = %s)
          )
        """
        params.extend([team_leader_account_id, team_leader_account_id])
    entries = clean_row(fetch_all(f"""
        SELECT (COALESCE(o.fulfilled_at,o.order_date) AT TIME ZONE 'Asia/Manila')::date AS sale_date,
               o.order_id,
               COALESCE(r.business_name, 'Retail counter') AS customer,
               p.name AS product,
               oi.quantity,
               oi.unit,
               oi.line_total AS total_sales, o.total_amount order_gross_sales,
               COALESCE(refunds.amount,0) order_refunded_sales,
               GREATEST(0,o.total_amount-COALESCE(refunds.amount,0)) order_retained_sales
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        LEFT JOIN LATERAL(SELECT SUM(amount) amount FROM order_refunds f WHERE f.order_id=o.order_id AND f.status='completed') refunds ON true
        WHERE o.status = 'fulfilled'
          AND p.item_type = 'finished_product'
          {scope_sql}
        ORDER BY sale_date DESC, o.order_id DESC, p.name;
    """, tuple(params) or None))
    for entry in entries:
        entry["sale_date"] = entry["sale_date"].isoformat()
    return entries


def team_sales_refund_entries(team_leader_account_id: int) -> list[dict]:
    rows=clean_row(fetch_all("""SELECT f.order_refund_id,f.order_id,f.return_id,f.amount,f.completed_at,
        (COALESCE(o.fulfilled_at,o.order_date) AT TIME ZONE 'Asia/Manila')::date sale_date,
        CASE WHEN r.resolution='replace' THEN 'Replacement refund difference' ELSE 'Returned products' END refund_type
        FROM order_refunds f JOIN orders o USING(order_id) LEFT JOIN product_returns r ON r.return_id=f.return_id
        WHERE f.status='completed' AND o.status='fulfilled'
        AND ((o.order_type='walk_in' AND o.created_by_account_id=%s) OR (o.order_type='reseller' AND o.team_leader_account_id=%s))
        ORDER BY f.completed_at DESC,f.order_refund_id DESC;""",(team_leader_account_id,team_leader_account_id)))
    for row in rows:
        row['sale_date']=row['sale_date'].isoformat()
        row['completed_at']=row['completed_at'].isoformat()
    return rows


def team_rejected_order_entries(team_leader_account_id: int | None = None) -> list[dict]:
    scope_sql = ""
    params: list[object] = []
    if team_leader_account_id is not None:
        scope_sql = "AND o.team_leader_account_id = %s"
        params.append(team_leader_account_id)
    return clean_row(fetch_all(f"""
        SELECT o.order_id,
               COALESCE(r.business_name, 'Retail counter') AS reseller,
               COALESCE(o.approved_at, o.order_date) AS rejected_at,
               o.total_amount,
               COALESCE(o.rejection_reason, o.notes) AS notes,
               STRING_AGG(
                   TRIM(TRAILING '.' FROM TRIM(TRAILING '0' FROM oi.quantity::text)) || ' ' || oi.unit || ' ' || p.name,
                   ', '
                   ORDER BY p.name
               ) AS items
        FROM orders o
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE o.order_type = 'reseller'
          AND o.status = 'rejected'
          AND p.item_type = 'finished_product'
          {scope_sql}
        GROUP BY o.order_id, r.business_name, o.approved_at, o.order_date, o.total_amount, o.rejection_reason, o.notes
        ORDER BY rejected_at DESC, o.order_id DESC;
    """, tuple(params) or None))


def team_reseller_purchase_summary(team_leader_account_id: int | None = None) -> list[dict]:
    """Summarize actual reseller purchases from approved/fulfilled system orders."""
    scope_sql = ""
    params: list[object] = []
    if team_leader_account_id is not None:
        scope_sql = "AND o.team_leader_account_id = %s"
        params.append(team_leader_account_id)
    return clean_row(fetch_all(f"""
        SELECT r.reseller_id,
               COALESCE(r.business_name, 'Reseller') AS reseller,
               p.item_id AS product_id,
               p.name AS product,
               oi.unit,
               COALESCE(SUM(oi.quantity), 0) AS total_quantity,
               COALESCE(SUM(oi.line_total), 0) AS total_amount,
               COUNT(DISTINCT o.order_id) AS order_count,
               MAX(COALESCE(o.fulfilled_at, o.approved_at, o.order_date)) AS latest_order_at
        FROM orders o
        JOIN resellers r ON r.reseller_id = o.reseller_id
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE o.order_type = 'reseller'
          AND o.status = 'fulfilled'
          AND p.item_type = 'finished_product'
          {scope_sql}
        GROUP BY r.reseller_id, r.business_name, p.item_id, p.name, oi.unit
        ORDER BY r.business_name ASC, total_quantity DESC, total_amount DESC, p.name ASC;
    """, tuple(params) or None))


def add_sales_report(
    source: str,
    submitted_by: str,
    period_start: date,
    period_end: date,
    total_sales: float,
    total_orders: int,
    notes: str,
    account_id: int | None = None,
) -> dict:
    clean_total_sales = parse_money(total_sales, "Report total")
    if not 0 <= total_orders <= 2147483647:
        raise ValueError("Report order count is outside the supported range.")
    if account_id is not None:
        acc = fetch_one("SELECT account_id, name FROM accounts WHERE account_id = %s LIMIT 1;", (account_id,))
    else:
        acc = fetch_one("SELECT account_id, name FROM accounts WHERE name = %s LIMIT 1;", (submitted_by,))
    acc_id = acc["account_id"] if acc else None
    reseller_id = None
    department_id = None
    if source == "reseller":
        if account_id is None:
            raise ValueError("Reseller reports require a signed-in reseller account.")
        profile = require_reseller_team_leader(account_id)
        reseller_id = profile["reseller_id"]
    else:
        dep = fetch_one("SELECT department_id FROM departments LIMIT 1;")
        department_id = dep["department_id"] if dep else None

    with get_transaction_cursor() as cur:
        order_rows=[]
        if source=="team_leader":
            if acc_id is None:
                raise ValueError("A signed-in Sales account is required to submit this report.")
            from .product_returns import _actor
            _actor(cur,acc_id,{"sales","sales-supervisor"})
            order_rows=report_order_rows(cur,period_start,period_end,acc_id,lock=True)
            totals=report_totals(order_rows)
            clean_total_sales=totals["total_sales"]
            total_orders=totals["total_orders"]
        cur.execute("""
            INSERT INTO sales_reports (report_source, submitted_by_account_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes,refund_separation_version,submitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,%s,clock_timestamp())
            RETURNING sales_report_id, report_source, period_start, period_end, total_sales, total_orders, notes;
        """, (source, acc_id, reseller_id, department_id, period_start, period_end, clean_total_sales, total_orders, notes,1 if source=="team_leader" else 0))
        rep = cur.fetchone()
        if source=="team_leader":
            snapshot_report_orders(cur,rep["sales_report_id"],order_rows)
        _add_log_cursor(cur, actor_account_id=acc_id, action="submitted_sales_report", entity_type="sales_reports", entity_id=rep["sales_report_id"])
    return clean_row(rep)


def reseller_month_orders(account_id: int, period_start: date, period_end: date) -> list[dict]:
    profile = reseller_account_profile(account_id)
    orders = clean_row(fetch_all("""
        SELECT o.order_id, o.order_type, o.reseller_id,
               o.status, o.order_date, o.fulfilled_at, o.total_amount, o.notes
        FROM orders o
        WHERE o.order_type = 'reseller'
          AND o.status = 'fulfilled'
          AND o.reseller_id = %s
          AND COALESCE(o.fulfilled_at::date, o.order_date) BETWEEN %s AND %s
        ORDER BY COALESCE(o.fulfilled_at::date, o.order_date) DESC, o.order_id DESC;
    """, (profile["reseller_id"], period_start, period_end)))
    if not orders:
        return orders
    order_ids = [order["order_id"] for order in orders]
    items = clean_row(fetch_all("""
        SELECT oi.order_id, oi.product_id, p.name, oi.quantity, oi.unit_price, oi.line_total, oi.unit
        FROM order_items oi
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE oi.order_id = ANY(%s)
        ORDER BY p.name;
    """, (order_ids,)))
    items_by_order: dict[int, list[dict]] = {}
    for item in items:
        order_id = item.pop("order_id")
        items_by_order.setdefault(order_id, []).append(item)
    for order in orders:
        order["items"] = items_by_order.get(order["order_id"], [])
    return orders


def reseller_reportable_products(account_id: int, period_start: date | None = None, period_end: date | None = None) -> list[dict]:
    profile = reseller_account_profile(account_id)
    purchase_filters = [
        "o.order_type = 'reseller'",
        "o.status = 'fulfilled'",
        "o.reseller_id = %s",
        "p.item_type = 'finished_product'",
    ]
    report_filters = [
        "sr.report_source = 'reseller'",
        "sr.reseller_id = %s",
    ]
    params: list[object] = [profile["reseller_id"]]
    report_params: list[object] = [profile["reseller_id"]]
    if period_start is not None and period_end is not None:
        purchase_filters.append("COALESCE(o.fulfilled_at::date, o.order_date) BETWEEN %s AND %s")
        params.extend([period_start, period_end])
        report_filters.append("sr.period_start = %s AND sr.period_end = %s")
        report_params.extend([period_start, period_end])

    rows = clean_row(fetch_all(f"""
        WITH purchased AS (
            SELECT oi.product_id,
                   p.name,
                   p.category,
                   oi.unit,
                   MAX(oi.unit_price) AS unit_price,
                   COALESCE(SUM(oi.quantity), 0) AS purchased_quantity
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.order_id
            JOIN inventory_items p ON p.item_id = oi.product_id
            WHERE {" AND ".join(purchase_filters)}
            GROUP BY oi.product_id, p.name, p.category, oi.unit
        ),
        reported AS (
            SELECT sri.product_id,
                   COALESCE(SUM(sri.quantity_sold), 0) AS reported_quantity
            FROM sales_report_items sri
            JOIN sales_reports sr ON sr.sales_report_id = sri.sales_report_id
            WHERE {" AND ".join(report_filters)}
            GROUP BY sri.product_id
        )
        SELECT purchased.product_id,
               purchased.name,
               purchased.category,
               purchased.unit,
               purchased.unit_price,
               purchased.purchased_quantity,
               COALESCE(reported.reported_quantity, 0) AS reported_quantity,
               GREATEST(purchased.purchased_quantity - COALESCE(reported.reported_quantity, 0), 0) AS reportable_quantity
        FROM purchased
        LEFT JOIN reported ON reported.product_id = purchased.product_id
        WHERE GREATEST(purchased.purchased_quantity - COALESCE(reported.reported_quantity, 0), 0) > 0
        ORDER BY purchased.name;
    """, tuple(params + report_params)))
    return rows


def add_reseller_sell_through_report(account_id: int, period_start: date, period_end: date, quantities: dict[int, object], notes: str, attachments: list[dict] | None = None) -> dict:
    profile = require_reseller_team_leader(account_id)
    reportable = {int(row["product_id"]): row for row in reseller_reportable_products(account_id, period_start, period_end)}
    clean_quantities: dict[int, Decimal] = {}

    for product_id, quantity in quantities.items():
        try:
            clean_product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Unknown product.")
        raw_quantity = str(quantity).strip()
        if not raw_quantity or raw_quantity == "0":
            continue
        clean_quantity = measurements.parse_whole_packs(raw_quantity, "Sold quantity")
        if clean_product_id not in reportable:
            raise ValueError("You can only report products from fulfilled orders that still have an unreported balance.")
        remaining = Decimal(str(reportable[clean_product_id]["reportable_quantity"]))
        if clean_quantity > remaining:
            raise ValueError(f"{reportable[clean_product_id]['name']} can only report up to {display_decimal(remaining)} {reportable[clean_product_id]['unit']}.")
        clean_quantities[clean_product_id] = clean_quantities.get(clean_product_id, Decimal("0")) + clean_quantity
        measurements.parse_whole_packs(clean_quantities[clean_product_id], "Sold quantity")

    if not clean_quantities:
        raise ValueError("Report at least one sold product quantity.")

    total_sales = Decimal("0.00")
    total_units = Decimal("0")
    lines = []
    for product_id, quantity in clean_quantities.items():
        product = reportable[product_id]
        unit_price = Decimal(str(product["unit_price"])).quantize(Decimal("0.01"))
        total_sales += (unit_price * quantity).quantize(Decimal("0.01"))
        measurements.require_capacity(unit_price * quantity, "Report line total", scale=2)
        measurements.require_capacity(total_sales, "Report total", scale=2)
        total_units += quantity
        if total_units > 2147483647:
            raise ValueError("Report quantity is outside the supported range.")
        lines.append((product_id, quantity, product["unit"], unit_price))

    with get_transaction_cursor() as cur:
        cur.execute("""
            INSERT INTO sales_reports (report_source, submitted_by_account_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes)
            VALUES ('reseller', %s, %s, NULL, %s, %s, %s, %s, %s)
            RETURNING sales_report_id, report_source, period_start, period_end, total_sales, total_orders, notes;
        """, (
            profile["account_id"],
            profile["reseller_id"],
            period_start,
            period_end,
            total_sales.quantize(Decimal("0.01")),
            int(total_units),
            notes,
        ))
        rep = cur.fetchone()
        for product_id, quantity, unit, unit_price in lines:
            cur.execute("""
                INSERT INTO sales_report_items (sales_report_id, product_id, quantity_sold, unit, unit_price)
                VALUES (%s, %s, %s, %s, %s);
            """, (rep["sales_report_id"], product_id, quantity, unit, unit_price))
        for attachment in attachments or []:
            cur.execute("""
                INSERT INTO sales_report_attachments (sales_report_id, filename, content_type, content, size_bytes, checksum_sha256)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (
                rep["sales_report_id"],
                attachment["filename"],
                attachment["content_type"],
                attachment["content"],
                attachment["size_bytes"],
                attachment["checksum_sha256"],
            ))
        _add_log_cursor(cur, actor_account_id=profile["account_id"], action="submitted_reseller_sell_through_report", entity_type="sales_reports", entity_id=rep["sales_report_id"])
        _create_notification_cursor(
            cur, recipient_account_id=profile["team_leader_account_id"], category="report", severity="info",
            title="Reseller sales report submitted",
            message=f"{profile['business_name']} submitted sell-through report #{rep['sales_report_id']}.",
            target_url="/portal/team-leader/reports", source_type="sales_reports",
            source_id=rep["sales_report_id"], dedupe_key=f"reseller-report-{rep['sales_report_id']}",
        )
    return clean_row(rep)


__all__ = ['team_sales_refund_entries', 'settlement_summary', 'list_sales_reports', 'count_sales_reports', 'list_activity_logs', 'count_activity_logs', 'current_metrics', 'inventory_product_movement_analytics', 'team_sales_report_totals', 'team_sales_report_entries', 'team_rejected_order_entries', 'team_reseller_purchase_summary', 'add_sales_report', 'reseller_month_orders', 'reseller_reportable_products', 'add_reseller_sell_through_report']
