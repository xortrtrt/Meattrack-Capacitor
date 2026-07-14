from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.database import fetch_all, fetch_one, execute_write, clean_row, get_transaction_cursor
from app.config import DEFAULT_ACCOUNT_PASSWORD, RESELLER_PASSWORD
from app.security import hash_password, password_needs_rehash, verify_password

today = date.today()


def normalize_inventory_text(value: str, label: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError(f"{label} is required.")
    return cleaned


def normalize_inventory_name(value: str, label: str = "Name") -> str:
    return normalize_inventory_text(value, label).title()


def parse_inventory_decimal(value: object, label: str, places: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a number.")


def display_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


UNIT_ALIASES = {
    "kilogram": "kg",
    "kilograms": "kg",
    "kgs": "kg",
    "gram": "g",
    "grams": "g",
    "gms": "g",
    "milligram": "mg",
    "milligrams": "mg",
    "mgs": "mg",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "ltr": "l",
    "ltrs": "l",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
}

UNIT_FACTORS = {
    "mg": ("weight", Decimal("0.001")),
    "g": ("weight", Decimal("1")),
    "kg": ("weight", Decimal("1000")),
    "ml": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
}

raw_material_categories = ["meat", "raw_material"]
product_categories = ["Chicken", "Pork", "Beef"]
stock_units = ["kg", "g", "ml"]
content_units = ["g", "kg", "ml"]
recipe_units = ["g", "kg", "ml"]

MEDIA_ASSETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS media_assets (
    media_asset_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0),
    checksum_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\\\/]'),
    CHECK (btrim(content_type) <> ''),
    CHECK (length(checksum_sha256) = 64)
);
"""


def ensure_media_assets_table() -> None:
    execute_write(MEDIA_ASSETS_TABLE_SQL)


def media_asset_by_filename(filename: str):
    ensure_media_assets_table()
    row = fetch_one("""
        SELECT filename, content_type, content, size_bytes, checksum_sha256, updated_at
        FROM media_assets
        WHERE filename = %s;
    """, (filename,))
    if row is None:
        return None
    cleaned = clean_row(row)
    cleaned["content"] = bytes(cleaned["content"])
    return cleaned


def require_inventory_choice(value: object, choices: list[str], label: str) -> str:
    cleaned = normalize_inventory_text(str(value), label)
    for choice in choices:
        if cleaned.lower() == choice.lower():
            return choice
    raise ValueError(f"Choose a valid {label.lower()}.")


def canonical_inventory_unit(unit: str, label: str = "Unit") -> str:
    cleaned = normalize_inventory_text(unit, label).lower().replace(".", "")
    return UNIT_ALIASES.get(cleaned, cleaned)


def convert_inventory_quantity(quantity: Decimal, from_unit: str, to_unit: str, item_name: str) -> Decimal:
    from_key = canonical_inventory_unit(from_unit, "Ingredient unit")
    to_key = canonical_inventory_unit(to_unit, "Stock unit")
    if from_key == to_key:
        return quantity.quantize(Decimal("0.001"))
    if from_key not in UNIT_FACTORS or to_key not in UNIT_FACTORS:
        raise ValueError(f"{item_name} uses {to_unit}. Enter the recipe in {to_unit} or a compatible unit.")

    from_family, from_factor = UNIT_FACTORS[from_key]
    to_family, to_factor = UNIT_FACTORS[to_key]
    if from_family != to_family:
        raise ValueError(f"{item_name} uses {to_unit}. {from_unit} cannot be converted to {to_unit}.")

    converted = ((quantity * from_factor) / to_factor).quantize(Decimal("0.001"))
    if converted <= 0:
        raise ValueError(f"{item_name} amount is too small for {to_unit} stock unit.")
    return converted


roles = {
    "owner": {
        "label": "Owner",
        "account_type": "owner",
        "name": "Patric Mapa",
        "email": "patric.mapa@gmail.com",
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
    ],
    "team-leader": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("sales", "Walk-in Sales", "shopping-cart"),
        ("inventory", "Inventory", "boxes"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Reseller Orders", "clipboard-check"),
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
            SELECT d.department_id, d.department_name, NULL::text AS leader
            FROM departments d
            ORDER BY d.department_id;
        """))
    elif name == "resellers":
        return clean_row(fetch_all("""
            SELECT r.reseller_id, r.business_name, r.contact_person, r.email, r.contact_number, r.address, r.reseller_status,
                   a.name AS approved_by, r.created_at
            FROM resellers r
            LEFT JOIN accounts a ON a.account_id = r.approved_by_account_id
            ORDER BY r.reseller_id DESC;
        """))
    elif name == "inventory_items":
        return clean_row(fetch_all("""
            SELECT ii.item_id, ii.item_type,
                   CASE ii.item_type
                       WHEN 'raw_material' THEN 'Raw material'
                       WHEN 'finished_product' THEN 'Finished product'
                   END AS item_type_label,
                   ii.category, ii.name, ii.unit, ii.base_price, ii.is_active,
                   CASE
                       WHEN ii.item_type = 'raw_material' THEN ii.quantity_available
                       ELSE COALESCE(SUM(ib.quantity_available) FILTER (
                           WHERE ib.quality_status = 'approved'
                             AND ib.expiry_date >= CURRENT_DATE
                       ), 0)
                   END AS available
            FROM inventory_items ii
            LEFT JOIN inventory_batches ib ON ib.item_id = ii.item_id
            WHERE ii.item_type IN ('raw_material', 'finished_product')
            GROUP BY ii.item_id, ii.item_type, ii.category, ii.name, ii.unit, ii.base_price, ii.quantity_available, ii.is_active
            ORDER BY CASE ii.item_type WHEN 'raw_material' THEN 1 ELSE 2 END, ii.name;
        """))
    elif name == "inventory_batches":
        return clean_row(fetch_all("""
            SELECT ib.batch_id, ib.item_id, ii.item_type,
                   CASE ii.item_type
                       WHEN 'raw_material' THEN 'Raw material'
                       WHEN 'finished_product' THEN 'Finished product'
                   END AS item_type_label,
                   ii.name AS item_name, ib.batch_code, ib.source_type,
                   ib.quantity_received, ib.quantity_available, ib.unit,
                   ib.received_date, ib.expiry_date, ib.quality_status
            FROM inventory_batches ib
            JOIN inventory_items ii ON ii.item_id = ib.item_id
            WHERE ii.item_type = 'finished_product'
            ORDER BY ib.batch_id DESC;
        """))
    elif name == "products":
        return clean_row(fetch_all("""
            SELECT p.item_id AS product_id, p.name, p.description, p.unit, p.base_price, p.is_active,
                   p.category,
                   (
                       SELECT COUNT(*)
                       FROM product_recipes pr
                       JOIN inventory_items rm ON rm.item_id = pr.material_item_id
                       WHERE pr.product_item_id = p.item_id
                         AND rm.item_type = 'raw_material'
                   ) AS recipe_count,
                   COALESCE(SUM(pb.quantity_available) FILTER (
                       WHERE pb.quality_status = 'approved'
                         AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
                   ), 0) AS available
            FROM inventory_items p
            LEFT JOIN inventory_batches pb ON pb.item_id = p.item_id
            WHERE p.item_type = 'finished_product'
            GROUP BY p.item_id, p.name, p.description, p.unit, p.base_price, p.is_active, p.category
            ORDER BY p.item_id;
        """))
    elif name == "product_batches":
        return clean_row(fetch_all("""
            SELECT pb.batch_id AS product_batch_id, pb.item_id AS product_id, pb.batch_code, pb.source_type,
                   pb.quantity_received, pb.quantity_available, pb.unit, pb.received_date, pb.expiry_date, pb.quality_status
            FROM inventory_batches pb
            JOIN inventory_items p ON p.item_id = pb.item_id
            WHERE p.item_type = 'finished_product'
            ORDER BY pb.batch_id DESC;
        """))
    elif name == "raw_materials":
        return clean_row(fetch_all("""
            SELECT item_id AS raw_material_id, category, name, unit,
                   quantity_available AS available
            FROM inventory_items
            WHERE item_type = 'raw_material'
              AND is_active = true
            ORDER BY category, name;
        """))
    elif name == "product_recipes":
        return clean_row(fetch_all("""
            SELECT pr.recipe_id, pr.product_item_id AS product_id, p.name AS product_name,
                   pr.material_item_id AS raw_material_id, rm.name AS raw_material_name,
                   rm.category AS raw_material_category,
                   pr.quantity_required, pr.unit
            FROM product_recipes pr
            JOIN inventory_items p ON p.item_id = pr.product_item_id
            JOIN inventory_items rm ON rm.item_id = pr.material_item_id
            WHERE p.item_type = 'finished_product'
              AND rm.item_type = 'raw_material'
            ORDER BY p.name, rm.category, rm.name;
        """))
    elif name == "inquiries":
        return clean_row(fetch_all("""
            SELECT i.inquiry_id, i.name, i.contact_number, i.email, i.business_name, i.message, i.status,
                   a.name AS assigned_to, i.created_at
            FROM inquiries i
            LEFT JOIN accounts a ON a.account_id = i.assigned_team_leader_account_id
            ORDER BY i.inquiry_id DESC;
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
                JOIN inventory_items p ON p.item_id = oi.product_id
                WHERE p.item_type = 'finished_product';
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
                       WHEN al.product_batch_id IS NOT NULL THEN (SELECT p.name || ' batch ' || pb.batch_code FROM inventory_batches pb JOIN inventory_items p ON p.item_id = pb.item_id WHERE pb.batch_id = al.product_batch_id)
                       WHEN al.product_id IS NOT NULL THEN (SELECT name FROM inventory_items WHERE item_id = al.product_id)
                       WHEN al.raw_material_id IS NOT NULL THEN (SELECT name FROM inventory_items WHERE item_id = al.raw_material_id)
                       ELSE 'System Alert'
                    END) AS subject
            FROM alerts al
            ORDER BY al.alert_id DESC;
        """))
    elif name == "forecasts":
        return clean_row(fetch_all("""
            SELECT fr.forecast_result_id, p.name AS product, fr.forecast_date, fr.predicted_quantity,
                   ('85%% - 95%% range') AS confidence
            FROM forecast_results fr
            JOIN inventory_items p ON p.item_id = fr.product_id
            WHERE p.item_type = 'finished_product'
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
                       WHEN al.entity_type = 'products' THEN (SELECT name FROM inventory_items WHERE item_id = al.entity_id)
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

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

def __dir__():
    return [
        "departments", "resellers", "inventory_items", "inventory_batches",
        "products", "product_batches",
        "raw_materials", "product_recipes",
        "inquiries", "orders", "sales_reports", "alerts",
        "forecasts", "accounts",
        "activity_logs", "roles", "portal_nav", "today"
    ]

# ----------------- Write operations / Database modifiers -----------------

def product_by_id(product_id: int) -> dict | None:
    res = fetch_one("""
        SELECT p.item_id AS product_id, p.name, p.description, p.unit, p.base_price, p.is_active,
               p.category,
               (
                   SELECT COUNT(*)
                   FROM product_recipes pr
                   JOIN inventory_items rm ON rm.item_id = pr.material_item_id
                   WHERE pr.product_item_id = p.item_id
                     AND rm.item_type = 'raw_material'
               ) AS recipe_count,
               COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
               ), 0) AS available
        FROM inventory_items p
        LEFT JOIN inventory_batches pb ON pb.item_id = p.item_id
        WHERE p.item_id = %s
          AND p.item_type = 'finished_product'
        GROUP BY p.item_id, p.name, p.description, p.unit, p.base_price, p.is_active, p.category;
    """, (product_id,))
    return clean_row(res)


def role_key_for_account_type(account_type: str) -> str | None:
    if account_type == "team_leader":
        return "team-leader"
    if account_type in {"owner", "reseller"}:
        return account_type
    return None


def authenticate_account(email: str, password: str) -> dict | None:
    account = fetch_one(
        """
        SELECT account_id, account_type, name, email, password_hash, is_active
        FROM accounts
        WHERE lower(email) = lower(%s)
        LIMIT 1;
        """,
        (email,),
    )
    if account is None or not account["is_active"]:
        return None

    if not verify_password(password, account["password_hash"]):
        return None

    if password_needs_rehash(account["password_hash"]):
        execute_write(
            "UPDATE accounts SET password_hash = %s WHERE account_id = %s;",
            (hash_password(password), account["account_id"]),
        )

    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"])
    return clean if clean["role_key"] else None

def current_metrics() -> dict:
    sales_res = fetch_one("SELECT COALESCE(SUM(total_amount), 0) AS val FROM orders WHERE status = 'fulfilled';")
    fulfilled_sales = float(sales_res["val"])
    
    pending_res = fetch_one("SELECT COUNT(*) AS val FROM orders WHERE order_type = 'reseller' AND status = 'pending';")
    pending_reseller_orders = int(pending_res["val"])
    
    alerts_res = fetch_one("SELECT COUNT(*) AS val FROM alerts WHERE status = 'open';")
    open_alerts = int(alerts_res["val"])
    
    resellers_res = fetch_one("SELECT COUNT(*) AS val FROM resellers WHERE reseller_status = 'active';")
    active_resellers = int(resellers_res["val"])
    
    available_res = fetch_one("""
        SELECT COALESCE(SUM(ib.quantity_available), 0) AS val
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE ii.item_type = 'finished_product'
          AND ib.quality_status = 'approved'
          AND (ib.expiry_date IS NULL OR ib.expiry_date >= CURRENT_DATE);
    """)
    total_available = float(available_res["val"])
    
    return {
        "fulfilled_sales": fulfilled_sales,
        "pending_reseller_orders": pending_reseller_orders,
        "open_alerts": open_alerts,
        "active_resellers": active_resellers,
        "total_available": total_available,
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
        VALUES ('reseller', %s, %s, %s, %s, true);
    """, (res["reseller_id"], res["business_name"], res["email"], hash_password(RESELLER_PASSWORD)))
    
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
    

    
    add_log("Maria Santos", "rejected_reseller_inquiry", f"Inquiry #{inquiry_id}")
    return True

def deduct_stock_fefo(product_id: int, quantity: float):
    batches = fetch_all("""
        SELECT ib.batch_id AS product_batch_id, ib.quantity_available
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE ib.item_id = %s
          AND ii.item_type = 'finished_product'
          AND ib.quality_status = 'approved'
          AND ib.quantity_available > 0
          AND (ib.expiry_date IS NULL OR ib.expiry_date >= CURRENT_DATE)
        ORDER BY ib.expiry_date ASC NULLS LAST, ib.batch_id ASC;
    """, (product_id,))
    
    remaining = quantity
    for b in batches:
        if remaining <= 0:
            break
        b_id = b["product_batch_id"]
        b_avail = float(b["quantity_available"])
        take = min(b_avail, remaining)
        
        execute_write("""
            UPDATE inventory_batches
            SET quantity_available = quantity_available - %s
            WHERE batch_id = %s;
        """, (take, b_id))
        
        remaining -= take

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
        deduct_stock_fefo(product_id, quantity)
        add_log("Maria Santos", "created_walk_in_sale", f"Order #{order_id}")
    else:
        add_log(created_by_name, "created_reseller_order", f"Order #{order_id}")
    
    return clean_row(ord_res)

def decide_order(order_id: int, decision: str) -> bool:
    ord_res = fetch_one("SELECT * FROM orders WHERE order_id = %s;", (order_id,))
    if not ord_res or ord_res["order_type"] != "reseller":
        return False
    if ord_res["status"] in {"fulfilled", "rejected"}:
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
        
        items = fetch_all("SELECT product_id, quantity FROM order_items WHERE order_id = %s;", (order_id,))
        for item in items:
            deduct_stock_fefo(item["product_id"], float(item["quantity"]))
            
        add_log("Maria Santos", "fulfilled_reseller_order", f"Order #{order_id}")
    else:
        return False
    return True


def team_sales_report_totals(period_start: date, period_end: date) -> dict:
    totals = fetch_one("""
        SELECT COALESCE(SUM(oi.line_total), 0) AS total_sales,
               COUNT(DISTINCT o.order_id) AS total_orders
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE o.status = 'fulfilled'
          AND p.item_type = 'finished_product'
          AND COALESCE(o.fulfilled_at, o.order_date)::date BETWEEN %s AND %s;
    """, (period_start, period_end))
    totals = clean_row(totals)
    return {
        "total_sales": float(totals["total_sales"] or 0),
        "total_orders": int(totals["total_orders"] or 0),
    }


def team_sales_report_entries() -> list[dict]:
    entries = clean_row(fetch_all("""
        SELECT COALESCE(o.fulfilled_at, o.order_date)::date AS sale_date,
               o.order_id,
               COALESCE(r.business_name, 'Retail counter') AS customer,
               p.name AS product,
               oi.quantity,
               oi.unit,
               oi.line_total AS total_sales
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        WHERE o.status = 'fulfilled'
          AND p.item_type = 'finished_product'
        ORDER BY sale_date DESC, o.order_id DESC, p.name;
    """))
    for entry in entries:
        entry["sale_date"] = entry["sale_date"].isoformat()
    return entries


def team_rejected_order_entries() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT o.order_id,
               COALESCE(r.business_name, 'Retail counter') AS reseller,
               COALESCE(o.approved_at, o.order_date) AS rejected_at,
               o.total_amount,
               o.notes,
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
        GROUP BY o.order_id, r.business_name, o.approved_at, o.order_date, o.total_amount, o.notes
        ORDER BY rejected_at DESC, o.order_id DESC;
    """))


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


def add_raw_inventory_item(name: str, category: str, unit: str, quantity: float) -> dict:
    item_name = normalize_inventory_name(name, "Item name")
    item_category = require_inventory_choice(category, raw_material_categories, "Category")
    item_unit = require_inventory_choice(unit, stock_units, "Unit")
    amount = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if amount <= 0:
        raise ValueError("Quantity must be greater than zero.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id, name, unit
            FROM inventory_items
            WHERE item_type = 'raw_material'
              AND lower(name) = lower(%s)
            ORDER BY item_id
            FOR UPDATE;
        """, (item_name,))
        matches = cur.fetchall()

        if matches:
            existing = matches[0]
            mismatched = next(
                (item for item in matches if canonical_inventory_unit(item["unit"]) != canonical_inventory_unit(item_unit)),
                None,
            )
            if mismatched or canonical_inventory_unit(existing["unit"]) != canonical_inventory_unit(item_unit):
                raise ValueError(f"{existing['name']} is already tracked in {existing['unit']}. Use the same unit.")
            raise ValueError(f"{existing['name']} already exists. Use Add quantity instead.")

        cur.execute("""
            INSERT INTO inventory_items (item_type, name, category, unit, quantity_available, base_price, is_active)
            VALUES ('raw_material', %s, %s, %s, %s, 0, true)
            RETURNING item_id AS raw_material_id, category, name, unit, quantity_available AS available;
        """, (item_name, item_category, item_unit, amount))
        item = cur.fetchone()

    add_log("Maria Santos", "updated_raw_inventory", item_name)
    return clean_row(item)


def add_raw_inventory_quantity(raw_material_id: int, quantity: float) -> dict:
    amount = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if amount <= 0:
        raise ValueError("Quantity must be greater than zero.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id, name
            FROM inventory_items
            WHERE item_id = %s
              AND item_type = 'raw_material'
              AND is_active = true
            FOR UPDATE;
        """, (raw_material_id,))
        item = cur.fetchone()
        if item is None:
            raise ValueError("Choose a valid raw inventory item.")

        cur.execute("""
            UPDATE inventory_items
            SET quantity_available = quantity_available + %s
            WHERE item_id = %s
            RETURNING item_id AS raw_material_id, category, name, unit, quantity_available AS available;
        """, (amount, raw_material_id))
        updated_item = cur.fetchone()

    add_log("Maria Santos", "added_raw_inventory_quantity", item["name"])
    return clean_row(updated_item)


def create_product_with_recipe(
    name: str,
    category: str,
    base_price: object,
    material_item_ids: list[object],
    quantity_required: list[object],
    quantity_required_units: list[object],
    pack_size: object = "",
    pack_size_unit: str = "",
) -> dict:
    product_name = normalize_inventory_name(name, "Product name")
    product_category = require_inventory_choice(category, product_categories, "Category")
    product_unit = "pack"
    price = parse_inventory_decimal(base_price or 0, "Base price", "0.01")
    if price < 0:
        raise ValueError("Base price cannot be negative.")

    if not str(pack_size).strip():
        raise ValueError("Contents per pack is required.")
    size = parse_inventory_decimal(pack_size, "Contents per pack", "0.001")
    if size <= 0:
        raise ValueError("Contents per pack must be greater than zero.")
    size_unit = require_inventory_choice(pack_size_unit, content_units, "Contents unit")
    product_description = f"{product_name} - {display_decimal(size)} {size_unit} per pack."

    if len(material_item_ids) != len(quantity_required) or len(material_item_ids) != len(quantity_required_units):
        raise ValueError("Ingredient rows are incomplete.")
    if not material_item_ids:
        raise ValueError("Add at least one ingredient.")

    requirements = []
    seen_material_ids = set()
    for material_item_id, required_quantity, required_unit in zip(material_item_ids, quantity_required, quantity_required_units):
        try:
            material_id = int(material_item_id)
        except (TypeError, ValueError):
            raise ValueError("Choose valid raw inventory items.")
        if material_id in seen_material_ids:
            raise ValueError("Each ingredient can only be selected once.")
        seen_material_ids.add(material_id)

        required = parse_inventory_decimal(required_quantity, "Ingredient quantity", "0.001")
        if required <= 0:
            raise ValueError("Ingredient quantity must be greater than zero.")
        requirements.append((material_id, required, require_inventory_choice(required_unit, recipe_units, "Ingredient unit")))

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id
            FROM inventory_items
            WHERE item_type = 'finished_product'
              AND lower(name) = lower(%s)
            LIMIT 1;
        """, (product_name,))
        if cur.fetchone() is not None:
            raise ValueError("Product already exists.")

        material_ids = [material_id for material_id, _, _ in requirements]
        cur.execute("""
            SELECT item_id, name, unit
            FROM inventory_items
            WHERE item_type = 'raw_material'
              AND is_active = true
              AND item_id = ANY(%s);
        """, (material_ids,))
        materials = {row["item_id"]: row for row in cur.fetchall()}
        if len(materials) != len(material_ids):
            raise ValueError("Choose valid raw inventory items.")

        cur.execute("""
            INSERT INTO inventory_items (item_type, name, category, description, unit, base_price, quantity_available, is_active)
            VALUES ('finished_product', %s, %s, %s, %s, %s, 0, true)
            RETURNING item_id AS product_id, name, category, unit, base_price;
        """, (
            product_name,
            product_category,
            product_description,
            product_unit,
            price,
        ))
        product = cur.fetchone()

        for material_id, required, required_unit in requirements:
            material = materials[material_id]
            converted_required = convert_inventory_quantity(
                required,
                required_unit,
                material["unit"],
                material["name"],
            )
            cur.execute("""
                INSERT INTO product_recipes (product_item_id, material_item_id, quantity_required, unit)
                VALUES (%s, %s, %s, %s);
            """, (product["product_id"], material_id, converted_required, material["unit"]))

    add_log("Maria Santos", "created_product_recipe", product_name)
    return clean_row(product)


def produce_product(product_id: int, batch_code: str, quantity: float, expiry_date: date) -> dict:
    produced_quantity = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if produced_quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id AS product_id, name, unit
            FROM inventory_items
            WHERE item_id = %s
              AND item_type = 'finished_product'
              AND is_active = true;
        """, (product_id,))
        product = cur.fetchone()
        if product is None:
            raise ValueError("Unknown product")

        cur.execute("SELECT batch_id FROM inventory_batches WHERE batch_code = %s;", (batch_code,))
        if cur.fetchone() is not None:
            raise ValueError("Batch code already exists.")

        cur.execute("""
            SELECT pr.material_item_id AS raw_material_id, pr.quantity_required, pr.unit AS recipe_unit,
                   rm.name AS raw_material_name, rm.unit AS material_unit,
                   rm.quantity_available
            FROM product_recipes pr
            JOIN inventory_items rm ON rm.item_id = pr.material_item_id
            WHERE pr.product_item_id = %s
              AND rm.item_type = 'raw_material'
            ORDER BY pr.recipe_id
            FOR UPDATE OF rm;
        """, (product_id,))
        recipe_rows = cur.fetchall()
        if not recipe_rows:
            raise ValueError("No recipe is configured for this product.")

        requirements = []
        for row in recipe_rows:
            if row["recipe_unit"] != row["material_unit"]:
                raise ValueError(f"{row['raw_material_name']} recipe unit must match its stock unit.")
            required = (Decimal(row["quantity_required"]) * produced_quantity).quantize(Decimal("0.001"))
            available = Decimal(row["quantity_available"])
            if required > available:
                raise ValueError(
                    f"Not enough {row['raw_material_name']}. "
                    f"Required {required:g} {row['material_unit']}, "
                    f"available {available:g} {row['material_unit']}."
                )
            requirements.append({
                "raw_material_id": row["raw_material_id"],
                "name": row["raw_material_name"],
                "required": required,
            })

        for requirement in requirements:
            cur.execute("""
                UPDATE inventory_items
                SET quantity_available = quantity_available - %s
                WHERE item_id = %s;
            """, (requirement["required"], requirement["raw_material_id"]))

        cur.execute("""
            INSERT INTO inventory_batches (item_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status)
            VALUES (%s, %s, 'production', %s, %s, %s, %s, %s, 'approved')
            RETURNING batch_id AS product_batch_id, item_id AS product_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status;
        """, (product_id, batch_code, produced_quantity, produced_quantity, product["unit"], date.today(), expiry_date))
        batch = cur.fetchone()

        days = (expiry_date - date.today()).days
        if days <= 7:
            cur.execute("""
                INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, message, status, triggered_at)
                VALUES ('near_expiry', %s, %s, %s, %s, 'open', %s);
            """, (
                'warning' if days > 2 else 'critical',
                product_id, batch["product_batch_id"],
                f"{quantity:g} {product['unit']} expires in {days} day(s).",
                datetime.now()
            ))

    add_log("Maria Santos", "produced_product_batch", batch_code)
    return clean_row(batch)


def add_product_batch(product_id: int, batch_code: str, quantity: float, expiry_date: date, source_type: str) -> dict:
    product = product_by_id(product_id)
    if product is None:
        raise ValueError("Unknown product")

    batch = execute_write("""
        INSERT INTO inventory_batches (item_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'approved')
        RETURNING batch_id AS product_batch_id, item_id AS product_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status;
    """, (product_id, batch_code, source_type, quantity, quantity, product["unit"], date.today(), expiry_date), returning=True)
    
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

def add_account(account_type: str, name: str, email: str) -> None:
    reseller_id = None
    
    if account_type == "reseller":
        res = fetch_one("SELECT reseller_id FROM resellers WHERE email = %s LIMIT 1;", (email,))
        if res:
            reseller_id = res["reseller_id"]
            
    execute_write("""
        INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
        VALUES (%s, %s, %s, %s, %s, true);
    """, (account_type, reseller_id, name, email, hash_password(DEFAULT_ACCOUNT_PASSWORD)))
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
    
    prods = fetch_all("""
        SELECT item_id AS product_id, name
        FROM inventory_items
        WHERE item_type = 'finished_product';
    """)
    for p in prods:
        avail_res = fetch_one("""
            SELECT COALESCE(SUM(quantity_available), 0) AS val
            FROM inventory_batches
            WHERE item_id = %s
              AND quality_status = 'approved'
              AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE);
        """, (p["product_id"],))
        avail = float(avail_res["val"])
        pred = max(1, round(avail * 0.42, 1))
        
        execute_write("""
            INSERT INTO forecast_results (forecast_run_id, product_id, forecast_date, predicted_quantity, confidence_lower, confidence_upper)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (run_id, p["product_id"], date.today() + timedelta(days=forecast_horizon_days), pred, max(0, pred - 5), pred + 5))
        
    add_log("Owner", "forecast_completed", model_name)

def update_product_price(product_id: int, base_price: float) -> None:
    execute_write("""
        UPDATE inventory_items
        SET base_price = %s
        WHERE item_id = %s
          AND item_type = 'finished_product';
    """, (base_price, product_id))
    
    prod = fetch_one("SELECT name FROM inventory_items WHERE item_id = %s AND item_type = 'finished_product';", (product_id,))
    add_log("Owner", "updated_product_price", prod["name"] if prod else f"Product #{product_id}")
