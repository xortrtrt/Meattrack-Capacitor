from __future__ import annotations
from datetime import date, datetime, timedelta
from app.database import fetch_all, fetch_one, execute_write, clean_row

today = date.today()

roles = {
    "owner": {
        "label": "Owner",
        "account_type": "owner",
        "name": "Patricia Manalo",
        "email": "owner@batangaspremium.test",
        "default_section": "dashboard",
    },
    "team-leader": {
        "label": "Team Leader",
        "account_type": "team_leader",
        "name": "Maria Santos",
        "email": "leader@batangaspremium.test",
        "default_section": "dashboard",
    },
    "reseller": {
        "label": "Reseller",
        "account_type": "reseller",
        "name": "Lipa Fresh Mart",
        "email": "reseller@lipafresh.test",
        "default_section": "dashboard",
    },
}

portal_nav = {
    "reseller": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("order", "Place Order", "shopping-basket"),
        ("history", "Order History", "clipboard-list"),
        ("reports", "Sales Reports", "file-up"),
        ("messages", "Messages", "messages-square"),
    ],
    "team-leader": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("sales", "Walk-in Sales", "shopping-cart"),
        ("inventory", "Inventory", "boxes"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("employees", "Employees", "users"),
        ("reports", "Reports", "file-text"),
    ],
    "owner": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("products", "Products & Prices", "tags"),
        ("reports", "Reports", "bar-chart-3"),
        ("forecasts", "Forecasts", "line-chart"),
        ("accounts", "Accounts", "shield-check"),
        ("logs", "Audit Logs", "scroll-text"),
    ],
}

# ----------------- Dynamic Attribute Getters -----------------

def __getattr__(name: str):
    if name == "departments":
        return clean_row(fetch_all("""
            SELECT d.department_id, d.department_name, e.name AS leader
            FROM departments d
            LEFT JOIN department_leaders dl ON dl.department_id = d.department_id AND dl.ended_on IS NULL
            LEFT JOIN employees e ON e.employee_id = dl.team_leader_employee_id
            ORDER BY d.department_id;
        """))
    elif name == "employees":
        return clean_row(fetch_all("""
            SELECT e.employee_id, e.department_id, e.name, e.position, e.employment_status,
                   COALESCE(ea.status, 'present') AS attendance,
                   COALESCE(ROUND(AVG(eme.overall_score), 2), 4.5) AS task_score
            FROM employees e
            LEFT JOIN employee_attendance ea ON ea.employee_id = e.employee_id AND ea.work_date = CURRENT_DATE
            LEFT JOIN employee_merit_evaluations eme ON eme.employee_id = e.employee_id
            GROUP BY e.employee_id, e.department_id, e.name, e.position, e.employment_status, ea.status
            ORDER BY e.employee_id;
        """))
    elif name == "resellers":
        return clean_row(fetch_all("""
            SELECT r.reseller_id, r.business_name, r.contact_person, r.email, r.contact_number, r.address, r.reseller_status,
                   a.name AS approved_by, r.created_at
            FROM resellers r
            LEFT JOIN accounts a ON a.account_id = r.approved_by_account_id
            ORDER BY r.reseller_id DESC;
        """))
    elif name == "products":
        return clean_row(fetch_all("""
            SELECT p.product_id, p.name, p.description, p.unit, p.base_price, p.reorder_level, p.is_active,
                   mt.name AS category,
                   COALESCE(SUM(pb.quantity_available) FILTER (WHERE pb.quality_status = 'approved' AND pb.expiry_date >= CURRENT_DATE), 0) AS available
            FROM products p
            LEFT JOIN meat_types mt ON mt.meat_type_id = p.meat_type_id
            LEFT JOIN product_batches pb ON pb.product_id = p.product_id
            GROUP BY p.product_id, p.name, p.description, p.unit, p.base_price, p.reorder_level, p.is_active, mt.name
            ORDER BY p.product_id;
        """))
    elif name == "product_batches":
        return clean_row(fetch_all("""
            SELECT pb.product_batch_id, pb.product_id, pb.batch_code, pb.source_type,
                   pb.quantity_received, pb.quantity_available, pb.unit, pb.received_date, pb.expiry_date, pb.quality_status
            FROM product_batches pb
            ORDER BY pb.product_batch_id DESC;
        """))
    elif name == "inquiries":
        return clean_row(fetch_all("""
            SELECT i.inquiry_id, i.name, i.contact_number, i.email, i.business_name, i.message, i.status,
                   a.name AS assigned_to, i.created_at
            FROM inquiries i
            LEFT JOIN accounts a ON a.account_id = i.assigned_team_leader_account_id
            ORDER BY i.inquiry_id DESC;
        """))
    elif name == "inquiry_messages":
        return clean_row(fetch_all("""
            SELECT im.inquiry_message_id, im.inquiry_id, im.sender_type,
                   (CASE 
                       WHEN im.sender_type = 'chatbot' THEN 'MEATTRACK Assistant'
                       WHEN im.sender_type = 'potential_reseller' THEN inq.name
                       ELSE COALESCE(a.name, 'MEATTRACK Assistant')
                    END) AS sender,
                   im.message, im.created_at
            FROM inquiry_messages im
            LEFT JOIN accounts a ON a.account_id = im.sender_account_id
            LEFT JOIN inquiries inq ON inq.inquiry_id = im.inquiry_id
            ORDER BY im.inquiry_message_id DESC;
        """))
    elif name == "orders":
        orders = fetch_all("""
            SELECT o.order_id, o.order_type, o.reseller_id,
                   COALESCE(r.business_name, 'Retail counter') AS reseller,
                   o.status, o.order_date, o.total_amount, o.notes
            FROM orders o
            LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
            ORDER BY o.order_id DESC;
        """)
        orders = clean_row(orders)
        if orders:
            items = fetch_all("""
                SELECT oi.order_id, oi.product_id, p.name, oi.quantity, oi.unit_price, oi.line_total
                FROM order_items oi
                JOIN products p ON p.product_id = oi.product_id;
            """)
            items = clean_row(items)
            items_by_order = {}
            for item in items:
                order_id = item.pop("order_id")
                items_by_order.setdefault(order_id, []).append(item)
            for o in orders:
                o["items"] = items_by_order.get(o["order_id"], [])
        return orders
    elif name == "sales_reports":
        return clean_row(fetch_all("""
            SELECT sr.sales_report_id, sr.report_source,
                   COALESCE(a.name, 'System') AS submitted_by,
                   sr.period_start, sr.period_end, sr.total_sales, sr.total_orders, sr.notes
            FROM sales_reports sr
            LEFT JOIN accounts a ON a.account_id = sr.submitted_by_account_id
            ORDER BY sr.sales_report_id DESC;
        """))
    elif name == "alerts":
        return clean_row(fetch_all("""
            SELECT al.alert_id, al.alert_type, al.severity, al.message, al.status, al.triggered_at,
                   (CASE 
                       WHEN al.product_batch_id IS NOT NULL THEN (SELECT p.name || ' batch ' || pb.batch_code FROM product_batches pb JOIN products p ON p.product_id = pb.product_id WHERE pb.product_batch_id = al.product_batch_id)
                       WHEN al.product_id IS NOT NULL THEN (SELECT name FROM products WHERE product_id = al.product_id)
                       WHEN al.raw_material_batch_id IS NOT NULL THEN (SELECT rm.name || ' batch ' || rmb.batch_code FROM raw_material_batches rmb JOIN raw_materials rm ON rm.raw_material_id = rmb.raw_material_id WHERE rmb.raw_material_batch_id = al.raw_material_batch_id)
                       WHEN al.raw_material_id IS NOT NULL THEN (SELECT name FROM raw_materials WHERE raw_material_id = al.raw_material_id)
                       ELSE 'System Alert'
                    END) AS subject
            FROM alerts al
            ORDER BY al.alert_id DESC;
        """))
    elif name == "tasks":
        return clean_row(fetch_all("""
            SELECT t.task_id, e.name AS employee, t.title, t.status, t.due_date
            FROM employee_tasks t
            JOIN employees e ON e.employee_id = t.employee_id
            ORDER BY t.task_id DESC;
        """))
    elif name == "attendance":
        return clean_row(fetch_all("""
            SELECT att.attendance_id, e.name AS employee, att.work_date, att.status,
                   COALESCE(to_char(att.time_in, 'HH24:MI'), '') AS time_in,
                   COALESCE(to_char(att.time_out, 'HH24:MI'), '') AS time_out
            FROM employee_attendance att
            JOIN employees e ON e.employee_id = att.employee_id
            ORDER BY att.attendance_id DESC;
        """))
    elif name == "evaluations":
        return clean_row(fetch_all("""
            SELECT ev.evaluation_id, e.name AS employee,
                   ('Period ' || to_char(ev.period_start, 'Mon DD') || ' - ' || to_char(ev.period_end, 'Mon DD')) AS period,
                   ev.attendance_score, ev.task_score, ev.behavior_score, ev.overall_score, ev.feedback
            FROM employee_merit_evaluations ev
            JOIN employees e ON e.employee_id = ev.employee_id
            ORDER BY ev.evaluation_id DESC;
        """))
    elif name == "forecasts":
        return clean_row(fetch_all("""
            SELECT fr.forecast_result_id, p.name AS product, fr.forecast_date, fr.predicted_quantity,
                   ('85%% - 95%% range') AS confidence
            FROM forecast_results fr
            JOIN products p ON p.product_id = fr.product_id
            ORDER BY fr.forecast_result_id DESC;
        """))
    elif name == "accounts":
        return clean_row(fetch_all("""
            SELECT a.account_id, a.account_type, a.name, a.email,
                   (CASE WHEN a.is_active THEN 'active' ELSE 'inactive' END) AS status
            FROM accounts a
            ORDER BY a.account_id;
        """))
    elif name == "activity_logs":
        return clean_row(fetch_all("""
            SELECT al.activity_log_id,
                   COALESCE(a.name, 'MEATTRACK') AS actor,
                   al.action,
                   (CASE 
                       WHEN al.entity_type = 'orders' THEN 'Order #' || al.entity_id
                       WHEN al.entity_type = 'products' THEN (SELECT name FROM products WHERE product_id = al.entity_id)
                       WHEN al.entity_type = 'sales_reports' THEN 'Report #' || al.entity_id
                       WHEN al.entity_type = 'departments' THEN (SELECT department_name FROM departments WHERE department_id = al.entity_id)
                       WHEN al.entity_type = 'forecast_runs' THEN 'Daily product demand'
                       ELSE COALESCE(al.entity_type, 'System')
                    END) AS entity,
                   al.created_at
            FROM activity_logs al
            LEFT JOIN accounts a ON a.account_id = al.account_id
            ORDER BY al.activity_log_id DESC;
        """))
    elif name == "price_adjustments":
        return clean_row(fetch_all("""
            SELECT pa.adjustment_id, pb.batch_code, pa.adjustment_type,
                   (CASE 
                       WHEN pa.adjustment_type = 'discount_percent' THEN pa.discount_percent || '%%'
                       WHEN pa.adjustment_type = 'fixed_price' THEN 'PHP ' || pa.adjusted_price || '/pack'
                    END) AS value,
                   pa.reason, pa.starts_at
            FROM product_batch_price_adjustments pa
            JOIN product_batches pb ON pb.product_batch_id = pa.product_batch_id
            ORDER BY pa.adjustment_id DESC;
        """))
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

def __dir__():
    return [
        "departments", "employees", "resellers", "products", "product_batches",
        "inquiries", "inquiry_messages", "orders", "sales_reports", "alerts",
        "tasks", "attendance", "evaluations", "forecasts", "accounts",
        "activity_logs", "price_adjustments", "roles", "portal_nav", "today"
    ]

# ----------------- Write operations / Database modifiers -----------------

def product_by_id(product_id: int) -> dict | None:
    res = fetch_one("""
        SELECT p.product_id, p.name, p.description, p.unit, p.base_price, p.reorder_level, p.is_active,
               mt.name AS category,
               COALESCE(SUM(pb.quantity_available) FILTER (WHERE pb.quality_status = 'approved' AND pb.expiry_date >= CURRENT_DATE), 0) AS available
        FROM products p
        LEFT JOIN meat_types mt ON mt.meat_type_id = p.meat_type_id
        LEFT JOIN product_batches pb ON pb.product_id = p.product_id
        WHERE p.product_id = %s
        GROUP BY p.product_id, p.name, p.description, p.unit, p.base_price, p.reorder_level, p.is_active, mt.name;
    """, (product_id,))
    return clean_row(res)

def current_metrics() -> dict:
    sales_res = fetch_one("SELECT COALESCE(SUM(total_amount), 0) AS val FROM orders WHERE status = 'fulfilled';")
    fulfilled_sales = float(sales_res["val"])
    
    pending_res = fetch_one("SELECT COUNT(*) AS val FROM orders WHERE order_type = 'reseller' AND status = 'pending';")
    pending_reseller_orders = int(pending_res["val"])
    
    alerts_res = fetch_one("SELECT COUNT(*) AS val FROM alerts WHERE status = 'open';")
    open_alerts = int(alerts_res["val"])
    
    resellers_res = fetch_one("SELECT COUNT(*) AS val FROM resellers WHERE reseller_status = 'active';")
    active_resellers = int(resellers_res["val"])
    
    available_res = fetch_one("SELECT COALESCE(SUM(quantity_available), 0) AS val FROM product_batches WHERE quality_status = 'approved' AND expiry_date >= CURRENT_DATE;")
    total_available = float(available_res["val"])
    
    eval_res = fetch_one("SELECT COALESCE(AVG(overall_score), 4.5) AS val FROM employee_merit_evaluations;")
    employee_average = round(float(eval_res["val"]), 2)
    
    return {
        "fulfilled_sales": fulfilled_sales,
        "pending_reseller_orders": pending_reseller_orders,
        "open_alerts": open_alerts,
        "active_resellers": active_resellers,
        "total_available": total_available,
        "employee_average": employee_average,
    }

def add_log(actor_name: str, action: str, entity_name: str) -> None:
    acc = fetch_one("SELECT account_id FROM accounts WHERE name = %s LIMIT 1;", (actor_name,))
    acc_id = acc["account_id"] if acc else None
    if not acc_id:
        acc = fetch_one("SELECT account_id FROM accounts WHERE name = (SELECT business_name FROM resellers WHERE business_name = %s LIMIT 1) LIMIT 1;", (actor_name,))
        acc_id = acc["account_id"] if acc else None
    
    execute_write("""
        INSERT INTO activity_logs (account_id, action, entity_type, entity_id, created_at)
        VALUES (%s, %s, %s, %s, %s);
    """, (acc_id, action, 'custom', 0, datetime.now()))

def add_inquiry(name: str, business_name: str, email: str, contact_number: str, message: str) -> dict:
    leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
    leader_id = leader["account_id"] if leader else None
    
    inq = execute_write("""
        INSERT INTO inquiries (name, contact_number, email, business_name, message, status, assigned_team_leader_account_id)
        VALUES (%s, %s, %s, %s, %s, 'assigned', %s)
        RETURNING inquiry_id, name, contact_number, email, business_name, message, status, created_at;
    """, (name, contact_number, email, business_name, message, leader_id), returning=True)
    
    inq = clean_row(inq)
    
    execute_write("""
        INSERT INTO inquiry_messages (inquiry_id, sender_type, sender_account_id, message, created_at)
        VALUES (%s, 'chatbot', NULL, 'Inquiry received. Maria Santos will review this reseller application.', %s);
    """, (inq['inquiry_id'], datetime.now()))
    
    add_log("MEATTRACK", "created_inquiry", f"Inquiry #{inq['inquiry_id']}")
    return inq

def add_reseller_from_inquiry(inquiry_id: int) -> dict | None:
    inq = fetch_one("SELECT * FROM inquiries WHERE inquiry_id = %s;", (inquiry_id,))
    if not inq:
        return None
    
    execute_write("""
        UPDATE inquiries 
        SET status = 'approved', reviewed_by_account_id = assigned_team_leader_account_id, reviewed_at = %s 
        WHERE inquiry_id = %s;
    """, (datetime.now(), inquiry_id))
    
    leader_id = inq["assigned_team_leader_account_id"]
    
    res = execute_write("""
        INSERT INTO resellers (inquiry_id, business_name, contact_person, email, contact_number, address, reseller_status, approved_by_account_id, approved_at)
        VALUES (%s, %s, %s, %s, %s, 'Pending onboarding details', 'active', %s, %s)
        RETURNING reseller_id, business_name, contact_person, email, contact_number, address, reseller_status, approved_by_account_id, created_at;
    """, (inquiry_id, inq["business_name"], inq["name"], inq["email"], inq["contact_number"], leader_id, datetime.now()), returning=True)
    
    res = clean_row(res)
    
    execute_write("""
        INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
        VALUES ('reseller', %s, %s, %s, 'demo1234', true);
    """, (res["reseller_id"], res["business_name"], res["email"]))
    
    execute_write("""
        INSERT INTO inquiry_messages (inquiry_id, sender_type, sender_account_id, message, created_at)
        VALUES (%s, 'team_leader', %s, 'Your reseller application is approved. Account onboarding is ready.', %s);
    """, (inquiry_id, leader_id, datetime.now()))
    
    add_log("Maria Santos", "approved_reseller_inquiry", f"Inquiry #{inquiry_id}")
    return res

def reject_inquiry(inquiry_id: int) -> bool:
    inq = fetch_one("SELECT * FROM inquiries WHERE inquiry_id = %s;", (inquiry_id,))
    if not inq:
        return False
    
    execute_write("""
        UPDATE inquiries 
        SET status = 'rejected', reviewed_by_account_id = assigned_team_leader_account_id, reviewed_at = %s 
        WHERE inquiry_id = %s;
    """, (datetime.now(), inquiry_id))
    
    execute_write("""
        INSERT INTO inquiry_messages (inquiry_id, sender_type, sender_account_id, message, created_at)
        VALUES (%s, 'team_leader', %s, 'Inquiry reviewed and rejected for now. Applicant may contact the team for clarification.', %s);
    """, (inquiry_id, inq["assigned_team_leader_account_id"], datetime.now()))
    
    add_log("Maria Santos", "rejected_reseller_inquiry", f"Inquiry #{inquiry_id}")
    return True

def allocate_stock_fefo(order_item_id: int, product_id: int, quantity: float):
    batches = fetch_all("""
        SELECT product_batch_id, quantity_available
        FROM product_batches
        WHERE product_id = %s AND quality_status = 'approved' AND quantity_available > 0 AND expiry_date >= CURRENT_DATE
        ORDER BY expiry_date ASC, product_batch_id ASC;
    """, (product_id,))
    
    allocated = 0.0
    for b in batches:
        if allocated >= quantity:
            break
        b_id = b["product_batch_id"]
        b_avail = float(b["quantity_available"])
        needed = quantity - allocated
        take = min(b_avail, needed)
        
        execute_write("""
            UPDATE product_batches
            SET quantity_available = quantity_available - %s
            WHERE product_batch_id = %s;
        """, (take, b_id))
        
        execute_write("""
            INSERT INTO order_batch_allocations (order_item_id, product_batch_id, quantity_allocated)
            VALUES (%s, %s, %s);
        """, (order_item_id, b_id, take))
        
        allocated += take

def create_order(role: str, product_id: int, quantity: float, notes: str = "") -> dict:
    product = product_by_id(product_id)
    if product is None:
        raise ValueError("Unknown product")
    
    order_type = "reseller" if role == "reseller" else "walk_in"
    reseller_id = None
    created_by_name = "Maria Santos"
    
    if role == "reseller":
        res = fetch_one("SELECT reseller_id, business_name FROM resellers WHERE email = 'reseller@lipafresh.test' LIMIT 1;")
        reseller_id = res["reseller_id"] if res else 1
        created_by_name = res["business_name"] if res else "Lipa Fresh Mart"
    
    acc = fetch_one("SELECT account_id FROM accounts WHERE name = %s LIMIT 1;", (created_by_name,))
    creator_id = acc["account_id"] if acc else None
    
    status = "pending" if role == "reseller" else "fulfilled"
    total = round(product["base_price"] * quantity, 2)
    
    ord_res = execute_write("""
        INSERT INTO orders (order_type, reseller_id, created_by_account_id, approved_by_account_id, approved_at, status, order_date, fulfilled_at, total_amount, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING order_id, order_type, reseller_id, status, order_date, total_amount, notes;
    """, (
        order_type, reseller_id, creator_id,
        creator_id if status == "fulfilled" else None,
        datetime.now() if status == "fulfilled" else None,
        status, date.today(),
        datetime.now() if status == "fulfilled" else None,
        total, notes
    ), returning=True)
    
    order_id = ord_res["order_id"]
    
    oi = execute_write("""
        INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING order_item_id;
    """, (order_id, product_id, quantity, product["unit"], product["base_price"]), returning=True)
    
    order_item_id = oi["order_item_id"]
    
    if status == "fulfilled":
        allocate_stock_fefo(order_item_id, product_id, quantity)
        add_log("Maria Santos", "created_walk_in_sale", f"Order #{order_id}")
    else:
        add_log(created_by_name, "created_reseller_order", f"Order #{order_id}")
    
    return clean_row(ord_res)

def decide_order(order_id: int, decision: str) -> bool:
    ord_res = fetch_one("SELECT * FROM orders WHERE order_id = %s;", (order_id,))
    if not ord_res or ord_res["order_type"] != "reseller":
        return False
    
    leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
    leader_id = leader["account_id"] if leader else None
    
    if decision == "approve":
        execute_write("""
            UPDATE orders 
            SET status = 'approved', approved_by_account_id = %s, approved_at = %s 
            WHERE order_id = %s;
        """, (leader_id, datetime.now(), order_id))
        add_log("Maria Santos", "approved_reseller_order", f"Order #{order_id}")
        
    elif decision == "reject":
        execute_write("""
            UPDATE orders 
            SET status = 'rejected', approved_by_account_id = %s, approved_at = %s 
            WHERE order_id = %s;
        """, (leader_id, datetime.now(), order_id))
        add_log("Maria Santos", "rejected_reseller_order", f"Order #{order_id}")
        
    elif decision == "fulfill":
        execute_write("""
            UPDATE orders 
            SET status = 'fulfilled', fulfilled_at = %s 
            WHERE order_id = %s;
        """, (datetime.now(), order_id))
        
        items = fetch_all("SELECT order_item_id, product_id, quantity FROM order_items WHERE order_id = %s;", (order_id,))
        for item in items:
            allocate_stock_fefo(item["order_item_id"], item["product_id"], float(item["quantity"]))
            
        add_log("Maria Santos", "fulfilled_reseller_order", f"Order #{order_id}")
    else:
        return False
    return True

def add_sales_report(source: str, submitted_by: str, period_start: date, period_end: date, total_sales: float, total_orders: int, notes: str) -> dict:
    acc = fetch_one("SELECT account_id FROM accounts WHERE name = %s LIMIT 1;", (submitted_by,))
    acc_id = acc["account_id"] if acc else None
    
    reseller_id = None
    department_id = None
    if source == "reseller":
        res = fetch_one("SELECT reseller_id FROM resellers WHERE email = 'reseller@lipafresh.test' LIMIT 1;")
        reseller_id = res["reseller_id"] if res else 1
    else:
        dep = fetch_one("SELECT department_id FROM departments LIMIT 1;")
        department_id = dep["department_id"] if dep else 1
        
    rep = execute_write("""
        INSERT INTO sales_reports (report_source, submitted_by_account_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING sales_report_id, report_source, period_start, period_end, total_sales, total_orders, notes;
    """, (source, acc_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes), returning=True)
    
    add_log(submitted_by, "submitted_sales_report", f"Report #{rep['sales_report_id']}")
    return clean_row(rep)

def add_product_batch(product_id: int, batch_code: str, quantity: float, expiry_date: date, source_type: str) -> dict:
    product = product_by_id(product_id)
    if product is None:
        raise ValueError("Unknown product")
        
    leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
    leader_id = leader["account_id"] if leader else None
    
    prod_run_id = None
    if source_type == "production":
        run = execute_write("""
            INSERT INTO production_runs (product_id, created_by_account_id, quantity_planned, quantity_produced, status, production_date)
            VALUES (%s, %s, %s, %s, 'completed', %s)
            RETURNING production_id;
        """, (product_id, leader_id, quantity, quantity, date.today()), returning=True)
        prod_run_id = run["production_id"]
        
    batch = execute_write("""
        INSERT INTO product_batches (product_id, production_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status, received_by_account_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved', %s)
        RETURNING product_batch_id, product_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status;
    """, (product_id, prod_run_id, batch_code, source_type, quantity, quantity, product["unit"], date.today(), expiry_date, leader_id), returning=True)
    
    batch = clean_row(batch)
    
    days = (expiry_date - date.today()).days
    if days <= 7:
        execute_write("""
            INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message, status, triggered_at)
            VALUES ('near_expiry', %s, %s, %s, %s, 'open', %s);
        """, (
            'warning' if days > 2 else 'critical',
            product_id, batch["product_batch_id"],
            f"{quantity:g} {product['unit']} expires in {days} day(s).",
            datetime.now()
        ))
        
    add_log("Maria Santos", "registered_product_batch", batch_code)
    return batch

# ----------------- Write helpers for direct insertions -----------------

def add_inquiry_message(inquiry_id: int, sender_type: str, sender: str, message: str) -> None:
    acc = fetch_one("SELECT account_id FROM accounts WHERE name = %s LIMIT 1;", (sender,))
    acc_id = acc["account_id"] if acc else None
    
    execute_write("""
        INSERT INTO inquiry_messages (inquiry_id, sender_type, sender_account_id, message, created_at)
        VALUES (%s, %s, %s, %s, %s);
    """, (inquiry_id, sender_type, acc_id, message, datetime.now()))

def add_attendance(employee_name: str, work_date: date, status: str, time_in: str) -> None:
    emp = fetch_one("SELECT employee_id FROM employees WHERE name = %s LIMIT 1;", (employee_name,))
    if not emp:
        raise ValueError(f"Unknown employee {employee_name}")
        
    leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
    leader_id = leader["account_id"] if leader else None
    
    t_in = time_in + ":00" if time_in else None
    
    execute_write("""
        INSERT INTO employee_attendance (employee_id, work_date, status, time_in, recorded_by_account_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (employee_id, work_date) 
        DO UPDATE SET status = EXCLUDED.status, time_in = EXCLUDED.time_in;
    """, (emp["employee_id"], work_date, status, t_in, leader_id))
    add_log("Maria Santos", "recorded_attendance", employee_name)

def add_task(employee_name: str, title: str, due_date: date, status: str = "assigned") -> None:
    emp = fetch_one("SELECT employee_id FROM employees WHERE name = %s LIMIT 1;", (employee_name,))
    if not emp:
        raise ValueError(f"Unknown employee {employee_name}")
        
    leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
    leader_id = leader["account_id"] if leader else None
    
    execute_write("""
        INSERT INTO employee_tasks (employee_id, assigned_by_account_id, title, status, due_date)
        VALUES (%s, %s, %s, %s, %s);
    """, (emp["employee_id"], leader_id, title, status, due_date))
    add_log("Maria Santos", "assigned_employee_task", employee_name)

def add_evaluation(employee_name: str, period_name: str, attendance_score: int, task_score: int, behavior_score: int, feedback: str) -> None:
    emp = fetch_one("SELECT employee_id FROM employees WHERE name = %s LIMIT 1;", (employee_name,))
    if not emp:
        raise ValueError(f"Unknown employee {employee_name}")
        
    leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
    leader_id = leader["account_id"] if leader else None
    
    period_start = date.today() - timedelta(days=7)
    period_end = date.today() - timedelta(days=1)
    
    overall = round((attendance_score + task_score + behavior_score) / 3.0, 2)
    
    execute_write("""
        INSERT INTO employee_merit_evaluations (employee_id, evaluator_account_id, period_start, period_end, attendance_score, task_score, behavior_score, overall_score, feedback)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (employee_id, period_start, period_end)
        DO UPDATE SET attendance_score = EXCLUDED.attendance_score, task_score = EXCLUDED.task_score, behavior_score = EXCLUDED.behavior_score, overall_score = EXCLUDED.overall_score, feedback = EXCLUDED.feedback;
    """, (emp["employee_id"], leader_id, period_start, period_end, attendance_score, task_score, behavior_score, overall, feedback))
    add_log("Maria Santos", "submitted_merit_evaluation", employee_name)

def add_price_adjustment(batch_code: str, adjustment_type: str, value: str, reason: str) -> None:
    pb = fetch_one("SELECT product_batch_id FROM product_batches WHERE batch_code = %s LIMIT 1;", (batch_code,))
    if not pb:
        raise ValueError(f"Unknown batch {batch_code}")
        
    owner = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'owner' LIMIT 1;")
    owner_id = owner["account_id"] if owner else None
    
    discount_percent = None
    adjusted_price = None
    
    if adjustment_type == "discount_percent":
        clean_val = "".join(c for c in value if c.isdigit() or c == ".")
        discount_percent = float(clean_val) if clean_val else 10.0
    else:
        clean_val = "".join(c for c in value if c.isdigit() or c == ".")
        adjusted_price = float(clean_val) if clean_val else 50.0
        
    execute_write("""
        INSERT INTO product_batch_price_adjustments (product_batch_id, adjustment_type, discount_percent, adjusted_price, reason, starts_at, created_by_account_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s);
    """, (pb["product_batch_id"], adjustment_type, discount_percent, adjusted_price, reason, date.today(), owner_id))
    add_log("Owner", "created_batch_price_adjustment", batch_code)

def add_account(account_type: str, name: str, email: str) -> None:
    reseller_id = None
    employee_id = None
    
    if account_type == "reseller":
        res = fetch_one("SELECT reseller_id FROM resellers WHERE email = %s LIMIT 1;", (email,))
        if res:
            reseller_id = res["reseller_id"]
    elif account_type == "team_leader":
        emp = fetch_one("SELECT employee_id FROM employees WHERE name = %s LIMIT 1;", (name,))
        if emp:
            employee_id = emp["employee_id"]
            
    execute_write("""
        INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active)
        VALUES (%s, %s, %s, %s, %s, 'demo1234', true);
    """, (account_type, employee_id, reseller_id, name, email))
    add_log("Owner", "created_account", email)

def add_forecast(model_name: str, forecast_horizon_days: int) -> None:
    owner = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'owner' LIMIT 1;")
    owner_id = owner["account_id"] if owner else None
    
    run = execute_write("""
        INSERT INTO forecast_runs (run_by_account_id, model_name, input_period_start, input_period_end, forecast_horizon_days, status, started_at, completed_at, notes)
        VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, 'Forecast run generated from dashboard.')
        RETURNING forecast_run_id;
    """, (owner_id, model_name, date.today() - timedelta(days=30), date.today(), forecast_horizon_days, datetime.now(), datetime.now()), returning=True)
    
    run_id = run["forecast_run_id"]
    
    prods = fetch_all("SELECT product_id, name FROM products;")
    for p in prods:
        avail_res = fetch_one("SELECT COALESCE(SUM(quantity_available), 0) AS val FROM product_batches WHERE product_id = %s AND quality_status = 'approved' AND expiry_date >= CURRENT_DATE;", (p["product_id"],))
        avail = float(avail_res["val"])
        pred = max(1, round(avail * 0.42, 1))
        
        execute_write("""
            INSERT INTO forecast_results (forecast_run_id, product_id, forecast_date, predicted_quantity, confidence_lower, confidence_upper)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (run_id, p["product_id"], date.today() + timedelta(days=forecast_horizon_days), pred, max(0, pred - 5), pred + 5))
        
    add_log("Owner", "forecast_completed", model_name)

def update_product_price(product_id: int, base_price: float, reorder_level: float) -> None:
    execute_write("""
        UPDATE products
        SET base_price = %s, reorder_level = %s
        WHERE product_id = %s;
    """, (base_price, reorder_level, product_id))
    
    prod = fetch_one("SELECT name FROM products WHERE product_id = %s;", (product_id,))
    add_log("Owner", "updated_product_price", prod["name"] if prod else f"Product #{product_id}")
