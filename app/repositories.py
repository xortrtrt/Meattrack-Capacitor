from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import secrets

from app.database import fetch_all, fetch_one, execute_write, clean_row, get_transaction_cursor
from app.config import DEFAULT_ACCOUNT_PASSWORD, RESELLER_PASSWORD
from app.security import hash_password, password_needs_rehash, verify_password

today = date.today()


def database_health() -> str:
    row = fetch_one("SELECT 1 AS ready")
    return "connected" if row and row["ready"] == 1 else "unavailable"


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

SYSTEM_TABLES_READY = False

SYSTEM_TABLES_SQL = """
ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS auth_user_id uuid,
    ADD COLUMN IF NOT EXISTS auth_provider text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_auth_user_id
    ON accounts (auth_user_id)
    WHERE auth_user_id IS NOT NULL;

ALTER TABLE resellers
    ADD COLUMN IF NOT EXISTS team_leader_account_id bigint;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_resellers_team_leader'
    ) THEN
        ALTER TABLE resellers
            ADD CONSTRAINT fk_resellers_team_leader
            FOREIGN KEY (team_leader_account_id)
            REFERENCES accounts(account_id)
            ON UPDATE CASCADE
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_resellers_team_leader
    ON resellers (team_leader_account_id);

UPDATE resellers
SET team_leader_account_id = (
    SELECT account_id
    FROM accounts
    WHERE account_type = 'team_leader'
      AND is_active = true
    ORDER BY account_id
    LIMIT 1
)
WHERE team_leader_account_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM accounts
      WHERE account_type = 'team_leader'
        AND is_active = true
  );

CREATE TABLE IF NOT EXISTS user_consents (
    user_consent_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    policy_version text NOT NULL,
    consent_source text NOT NULL,
    provider text,
    accepted_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(policy_version) <> ''),
    CHECK (btrim(consent_source) <> '')
);

CREATE INDEX IF NOT EXISTS ix_user_consents_account_accepted
    ON user_consents (account_id, accepted_at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipient_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    recipient_role text CHECK (recipient_role IN ('owner', 'team-leader', 'reseller')),
    category text NOT NULL,
    severity text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    title text NOT NULL,
    message text NOT NULL,
    target_url text,
    source_type text,
    source_id bigint,
    dedupe_key text UNIQUE,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (recipient_account_id IS NOT NULL OR recipient_role IS NOT NULL),
    CHECK (btrim(category) <> ''),
    CHECK (btrim(title) <> ''),
    CHECK (btrim(message) <> '')
);

CREATE INDEX IF NOT EXISTS ix_notifications_role_read_created
    ON notifications (recipient_role, read_at, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_notifications_account_read_created
    ON notifications (recipient_account_id, read_at, created_at DESC);

CREATE TABLE IF NOT EXISTS reseller_cart_items (
    cart_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, product_id)
);

CREATE INDEX IF NOT EXISTS ix_reseller_cart_items_account_updated
    ON reseller_cart_items (account_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_reseller_cart_items_product
    ON reseller_cart_items (product_id);

ALTER TABLE reseller_cart_items ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS sales_report_items (
    sales_report_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sales_report_id bigint NOT NULL REFERENCES sales_reports(sales_report_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    quantity_sold numeric(12,3) NOT NULL CHECK (quantity_sold > 0),
    unit text NOT NULL DEFAULT 'pack',
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total numeric(12,2) GENERATED ALWAYS AS (round(quantity_sold * unit_price, 2)) STORED
);

CREATE INDEX IF NOT EXISTS ix_sales_report_items_report
    ON sales_report_items (sales_report_id);

CREATE INDEX IF NOT EXISTS ix_sales_report_items_product
    ON sales_report_items (product_id);

ALTER TABLE sales_report_items ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS sales_report_attachments (
    sales_report_attachment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sales_report_id bigint NOT NULL REFERENCES sales_reports(sales_report_id) ON UPDATE CASCADE ON DELETE CASCADE,
    filename text NOT NULL,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 5242880),
    checksum_sha256 text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\/]'),
    CHECK (btrim(content_type) <> ''),
    CHECK (length(checksum_sha256) = 64)
);

CREATE INDEX IF NOT EXISTS ix_sales_report_attachments_report
    ON sales_report_attachments (sales_report_id);

ALTER TABLE sales_report_attachments ENABLE ROW LEVEL SECURITY;
"""


def ensure_system_tables() -> None:
    global SYSTEM_TABLES_READY
    if SYSTEM_TABLES_READY:
        return
    execute_write(SYSTEM_TABLES_SQL)
    SYSTEM_TABLES_READY = True


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
        ("order", "Products", "shopping-basket"),
        ("cart", "Cart", "shopping-cart"),
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


# ----------------- Explicit read operations -----------------

def list_inventory_items() -> list[dict]:
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
        GROUP BY ii.item_id, ii.item_type, ii.category, ii.name, ii.unit,
                 ii.base_price, ii.quantity_available, ii.is_active
        ORDER BY CASE ii.item_type WHEN 'raw_material' THEN 1 ELSE 2 END, ii.name;
    """))


def list_inventory_batches() -> list[dict]:
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


def list_products(q: str = "", category: str = "", page: int | None = None, page_size: int = 12) -> list[dict]:
    where_extra = []
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        where_extra.append("(p.name ILIKE %s OR p.description ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        where_extra.append("p.category = %s")
        params.append(category)
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
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
          {where_extra}
        GROUP BY p.item_id, p.name, p.description, p.unit, p.base_price, p.is_active, p.category
        ORDER BY p.item_id
        {paging_sql};
    """.format(
        where_extra=("AND " + " AND ".join(where_extra)) if where_extra else "",
        paging_sql=paging_sql,
    ), tuple(params) or None))


def count_products(q: str = "", category: str = "") -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM inventory_items p
        WHERE p.item_type = 'finished_product'
    """
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        query += " AND (p.name ILIKE %s OR p.description ILIKE %s)"
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        query += " AND p.category = %s"
        params.append(category)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_raw_materials() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT item_id AS raw_material_id, category, name, unit,
               quantity_available AS available
        FROM inventory_items
        WHERE item_type = 'raw_material'
          AND is_active = true
        ORDER BY category, name;
    """))


def list_product_recipes() -> list[dict]:
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


def pagination_meta(total: int, page: int, page_size: int) -> dict:
    page_size = max(1, min(page_size, 50))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }


def list_inquiries(
    limit: int | None = None,
    q: str = "",
    status: str = "",
    assigned_team_leader_account_id: int | None = None,
    page: int | None = None,
    page_size: int = 10,
) -> list[dict]:
    query = """
        SELECT i.inquiry_id, i.name, i.contact_number, i.email, i.business_name, i.message, i.status,
               a.name AS assigned_to, i.created_at
        FROM inquiries i
        LEFT JOIN accounts a ON a.account_id = i.assigned_team_leader_account_id
    """
    where = []
    params: list[object] = []
    q = q.strip()
    status = status.strip()
    if q:
        where.append("(i.name ILIKE %s OR i.email ILIKE %s OR i.business_name ILIKE %s OR i.contact_number ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search, search])
    if status:
        where.append("i.status = %s")
        params.append(status)
    if assigned_team_leader_account_id is not None:
        where.append("i.assigned_team_leader_account_id = %s")
        params.append(assigned_team_leader_account_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY i.inquiry_id DESC"
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        query += " LIMIT %s"
        params.append(limit)
    elif page is not None:
        if page < 1:
            raise ValueError("page must be positive")
        if page_size < 1:
            raise ValueError("page_size must be positive")
        query += " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    return clean_row(fetch_all(query + ";", tuple(params) or None))


def count_inquiries(q: str = "", status: str = "", assigned_team_leader_account_id: int | None = None) -> int:
    query = "SELECT COUNT(*) AS total FROM inquiries i"
    where = []
    params: list[object] = []
    q = q.strip()
    status = status.strip()
    if q:
        where.append("(i.name ILIKE %s OR i.email ILIKE %s OR i.business_name ILIKE %s OR i.contact_number ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search, search])
    if status:
        where.append("i.status = %s")
        params.append(status)
    if assigned_team_leader_account_id is not None:
        where.append("i.assigned_team_leader_account_id = %s")
        params.append(assigned_team_leader_account_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_orders(
    order_type: str | None = None,
    q: str = "",
    status: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
    limit: int | None = None,
    page: int | None = None,
    page_size: int = 10,
) -> list[dict]:
    ensure_system_tables()
    if order_type not in {None, "reseller", "walk_in"}:
        raise ValueError("order_type must be reseller, walk_in, or None")
    where = []
    params: list[object] = []
    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)
    q = q.strip()
    status = status.strip()
    if status:
        where.append("o.status = %s")
        params.append(status)
    if team_leader_account_id is not None:
        if order_type == "walk_in":
            where.append("o.created_by_account_id = %s")
            params.append(team_leader_account_id)
        elif order_type == "reseller":
            where.append("r.team_leader_account_id = %s")
            params.append(team_leader_account_id)
        else:
            where.append("(o.created_by_account_id = %s OR r.team_leader_account_id = %s)")
            params.extend([team_leader_account_id, team_leader_account_id])
    if reseller_account_id is not None:
        where.append("""o.reseller_id = (
            SELECT a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
        )""")
        params.append(reseller_account_id)
    if q:
        where.append("""(
            CAST(o.order_id AS text) ILIKE %s
            OR COALESCE(r.business_name, 'Retail counter') ILIKE %s
            OR EXISTS (
                SELECT 1
                FROM order_items oi_search
                JOIN inventory_items p_search ON p_search.item_id = oi_search.product_id
                WHERE oi_search.order_id = o.order_id
                  AND p_search.name ILIKE %s
            )
        )""")
        search = f"%{q}%"
        params.extend([search, search, search])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    orders = clean_row(fetch_all(f"""
        SELECT o.order_id, o.order_type, o.reseller_id,
               COALESCE(r.business_name, 'Retail counter') AS reseller,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               o.status, o.order_date, o.total_amount, o.notes
        FROM orders o
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        {where_sql}
        ORDER BY o.order_id DESC
        {"LIMIT %s" if limit is not None else ""}
        {"LIMIT %s OFFSET %s" if limit is None and page is not None else ""};
    """, tuple(params + ([limit] if limit is not None else ([page_size, (page - 1) * page_size] if page is not None else []))) or None))
    if not orders:
        return orders

    order_ids = [order["order_id"] for order in orders]
    items = clean_row(fetch_all("""
        SELECT oi.order_id, oi.product_id, p.name, oi.quantity, oi.unit_price, oi.line_total
        FROM order_items oi
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE p.item_type = 'finished_product'
          AND oi.order_id = ANY(%s);
    """, (order_ids,)))
    items_by_order: dict[int, list[dict]] = {}
    for item in items:
        order_id = item.pop("order_id")
        items_by_order.setdefault(order_id, []).append(item)
    for order in orders:
        order["items"] = items_by_order.get(order["order_id"], [])
    return orders


def reseller_most_bought_products(limit: int = 6, account_id: int | None = None) -> list[dict]:
    ensure_system_tables()
    if limit < 1:
        raise ValueError("limit must be positive")
    where = ["o.order_type = 'reseller'", "p.item_type = 'finished_product'"]
    params: list[object] = []
    if account_id is not None:
        profile = reseller_account_profile(account_id)
        where.append("o.reseller_id = %s")
        params.append(profile["reseller_id"])
    params.append(limit)
    return clean_row(fetch_all(f"""
        SELECT p.item_id AS product_id,
               p.name,
               p.category,
               p.unit,
               p.base_price,
               COALESCE(SUM(oi.quantity), 0) AS total_quantity,
               COALESCE(SUM(oi.line_total), 0) AS total_amount,
               COUNT(DISTINCT o.order_id) AS order_count
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE {" AND ".join(where)}
        GROUP BY p.item_id, p.name, p.category, p.unit, p.base_price
        ORDER BY total_quantity DESC, total_amount DESC, p.name ASC
        LIMIT %s;
    """, tuple(params)))


def reseller_sales_series(start_date: date, end_date: date, status: str = "fulfilled", account_id: int | None = None) -> list[dict]:
    ensure_system_tables()
    if start_date > end_date:
        raise ValueError("start_date must be before end_date")
    allowed_statuses = {"", "all", "pending", "approved", "fulfilled", "rejected", "cancelled"}
    if status not in allowed_statuses:
        raise ValueError("Unknown order status")

    where = [
        "o.order_type = 'reseller'",
        "COALESCE(o.fulfilled_at::date, o.order_date) BETWEEN %s AND %s",
    ]
    params: list[object] = [start_date, end_date]
    if account_id is not None:
        profile = reseller_account_profile(account_id)
        where.append("o.reseller_id = %s")
        params.append(profile["reseller_id"])
    if status and status != "all":
        where.append("o.status = %s")
        params.append(status)

    return clean_row(fetch_all(f"""
        SELECT COALESCE(o.fulfilled_at::date, o.order_date) AS sale_date,
               COALESCE(SUM(o.total_amount), 0) AS total_sales,
               COUNT(*) AS order_count
        FROM orders o
        WHERE {" AND ".join(where)}
        GROUP BY COALESCE(o.fulfilled_at::date, o.order_date)
        ORDER BY sale_date ASC;
    """, tuple(params)))


def count_orders(
    order_type: str | None = None,
    q: str = "",
    status: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> int:
    ensure_system_tables()
    if order_type not in {None, "reseller", "walk_in"}:
        raise ValueError("order_type must be reseller, walk_in, or None")
    query = """
        SELECT COUNT(*) AS total
        FROM orders o
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
    """
    where = []
    params: list[object] = []
    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)
    if status:
        where.append("o.status = %s")
        params.append(status)
    if team_leader_account_id is not None:
        if order_type == "walk_in":
            where.append("o.created_by_account_id = %s")
            params.append(team_leader_account_id)
        elif order_type == "reseller":
            where.append("r.team_leader_account_id = %s")
            params.append(team_leader_account_id)
        else:
            where.append("(o.created_by_account_id = %s OR r.team_leader_account_id = %s)")
            params.extend([team_leader_account_id, team_leader_account_id])
    if reseller_account_id is not None:
        where.append("""o.reseller_id = (
            SELECT a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
        )""")
        params.append(reseller_account_id)
    q = q.strip()
    if q:
        where.append("""(
            CAST(o.order_id AS text) ILIKE %s
            OR COALESCE(r.business_name, 'Retail counter') ILIKE %s
            OR EXISTS (
                SELECT 1
                FROM order_items oi_search
                JOIN inventory_items p_search ON p_search.item_id = oi_search.product_id
                WHERE oi_search.order_id = o.order_id
                  AND p_search.name ILIKE %s
            )
        )""")
        search = f"%{q}%"
        params.extend([search, search, search])
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_sales_reports(
    report_source: str | None = None,
    limit: int | None = None,
    q: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
    page: int | None = None,
    page_size: int = 10,
) -> list[dict]:
    ensure_system_tables()
    if report_source not in {None, "reseller", "team_leader"}:
        raise ValueError("report_source must be reseller, team_leader, or None")
    query = """
        SELECT sr.sales_report_id, sr.report_source,
               COALESCE(a.name, 'System') AS submitted_by,
               COALESCE(r.business_name, '') AS reseller,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               sr.period_start, sr.period_end, sr.total_sales, sr.total_orders, sr.notes
        FROM sales_reports sr
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
    query += " ORDER BY sr.sales_report_id DESC"
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

    ensure_system_tables()
    report_ids = [report["sales_report_id"] for report in reports]
    items = clean_row(fetch_all("""
        SELECT sri.sales_report_id,
               p.name,
               sri.quantity_sold,
               sri.unit,
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
    return reports


def count_sales_reports(
    report_source: str | None = None,
    q: str = "",
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> int:
    ensure_system_tables()
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


def list_alerts() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT al.alert_id, al.alert_type, al.severity, al.message, al.status, al.triggered_at,
               (CASE
                    WHEN al.product_batch_id IS NOT NULL THEN (
                        SELECT p.name || ' batch ' || pb.batch_code
                        FROM inventory_batches pb
                        JOIN inventory_items p ON p.item_id = pb.item_id
                        WHERE pb.batch_id = al.product_batch_id
                    )
                    WHEN al.product_id IS NOT NULL THEN (SELECT name FROM inventory_items WHERE item_id = al.product_id)
                    WHEN al.raw_material_id IS NOT NULL THEN (SELECT name FROM inventory_items WHERE item_id = al.raw_material_id)
                    ELSE 'System Alert'
                END) AS subject
        FROM alerts al
        ORDER BY al.alert_id DESC;
    """))


def list_forecasts(limit: int | None = None, q: str = "", page: int | None = None, page_size: int = 10) -> list[dict]:
    query = """
        SELECT fr.forecast_result_id, p.name AS product, fr.forecast_date, fr.predicted_quantity,
               ('85%% - 95%% range') AS confidence
        FROM forecast_results fr
        JOIN inventory_items p ON p.item_id = fr.product_id
        WHERE p.item_type = 'finished_product'
    """
    params: list[object] = []
    q = q.strip()
    if q:
        query += " AND p.name ILIKE %s"
        params.append(f"%{q}%")
    query += " ORDER BY fr.forecast_result_id DESC"
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
    return clean_row(fetch_all(query + ";", tuple(params) or None))


def count_forecasts(q: str = "") -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM forecast_results fr
        JOIN inventory_items p ON p.item_id = fr.product_id
        WHERE p.item_type = 'finished_product'
    """
    params: list[object] = []
    q = q.strip()
    if q:
        query += " AND p.name ILIKE %s"
        params.append(f"%{q}%")
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_accounts(q: str = "", account_type: str = "", page: int | None = None, page_size: int = 10) -> list[dict]:
    ensure_system_tables()
    where = []
    params: list[object] = []
    q = q.strip()
    account_type = account_type.strip()
    if q:
        where.append("(a.name ILIKE %s OR a.email ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if account_type:
        where.append("a.account_type = %s")
        params.append(account_type)
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    return clean_row(fetch_all("""
        SELECT a.account_id, a.account_type, a.reseller_id, a.name, a.email,
               (CASE WHEN a.is_active THEN 'active' ELSE 'inactive' END) AS status,
               a.auth_provider,
               r.team_leader_account_id,
               tl.name AS team_leader_name
        FROM accounts a
        LEFT JOIN resellers r ON r.reseller_id = a.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        {where_sql}
        ORDER BY a.account_id
        {paging_sql};
    """.format(where_sql=where_sql, paging_sql=paging_sql), tuple(params) or None))


def count_accounts(q: str = "", account_type: str = "") -> int:
    ensure_system_tables()
    query = "SELECT COUNT(*) AS total FROM accounts a"
    where = []
    params: list[object] = []
    q = q.strip()
    account_type = account_type.strip()
    if q:
        where.append("(a.name ILIKE %s OR a.email ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if account_type:
        where.append("a.account_type = %s")
        params.append(account_type)
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_team_leader_accounts() -> list[dict]:
    ensure_system_tables()
    return clean_row(fetch_all("""
        SELECT account_id, name, email
        FROM accounts
        WHERE account_type = 'team_leader'
          AND is_active = true
        ORDER BY name, account_id;
    """))


def list_reseller_assignments() -> list[dict]:
    ensure_system_tables()
    return clean_row(fetch_all("""
        SELECT r.reseller_id,
               r.business_name,
               r.contact_person,
               r.email,
               r.reseller_status,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               tl.email AS team_leader_email,
               a.account_id AS account_id
        FROM resellers r
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        LEFT JOIN accounts a ON a.reseller_id = r.reseller_id
        ORDER BY r.business_name, r.reseller_id;
    """))


def set_reseller_team_leader(reseller_id: int, team_leader_account_id: int) -> dict:
    ensure_system_tables()
    leader = fetch_one("""
        SELECT account_id, name
        FROM accounts
        WHERE account_id = %s
          AND account_type = 'team_leader'
          AND is_active = true
        LIMIT 1;
    """, (team_leader_account_id,))
    if not leader:
        raise ValueError("Select an active team leader.")

    reseller = execute_write("""
        UPDATE resellers
        SET team_leader_account_id = %s
        WHERE reseller_id = %s
        RETURNING reseller_id, business_name, team_leader_account_id;
    """, (team_leader_account_id, reseller_id), returning=True)
    if not reseller:
        raise ValueError("Reseller was not found.")

    add_log("MEATTRACK", "assigned_reseller_team_leader", f"{reseller['business_name']} -> {leader['name']}")
    return clean_row(reseller)


def list_activity_logs(q: str = "", page: int | None = None, page_size: int = 10) -> list[dict]:
    where = []
    params: list[object] = []
    q = q.strip()
    if q:
        where.append("(COALESCE(a.name, 'MEATTRACK') ILIKE %s OR al.action ILIKE %s OR al.entity_type ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
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
        {where_sql}
        ORDER BY al.activity_log_id DESC
        {paging_sql};
    """.format(where_sql=where_sql, paging_sql=paging_sql), tuple(params) or None))


def count_activity_logs(q: str = "") -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM activity_logs al
        LEFT JOIN accounts a ON a.account_id = al.account_id
    """
    params: list[object] = []
    q = q.strip()
    if q:
        query += " WHERE (COALESCE(a.name, 'MEATTRACK') ILIKE %s OR al.action ILIKE %s OR al.entity_type ILIKE %s)"
        search = f"%{q}%"
        params.extend([search, search, search])
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


# ----------------- Dynamic Attribute Getters -----------------

def __getattr__(name: str):
    if name == "departments":
        return clean_row(fetch_all("""
            SELECT d.department_id, d.department_name, NULL::text AS leader
            FROM departments d
            ORDER BY d.department_id;
        """))
    elif name == "resellers":
        ensure_system_tables()
        return clean_row(fetch_all("""
            SELECT r.reseller_id, r.business_name, r.contact_person, r.email, r.contact_number, r.address, r.reseller_status,
                   a.name AS approved_by,
                   r.team_leader_account_id,
                   tl.name AS team_leader_name,
                   r.created_at
            FROM resellers r
            LEFT JOIN accounts a ON a.account_id = r.approved_by_account_id
            LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
            ORDER BY r.reseller_id DESC;
        """))
    elif name == "inventory_items":
        return list_inventory_items()
    elif name == "inventory_batches":
        return list_inventory_batches()
    elif name == "products":
        return list_products()
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
        return list_raw_materials()
    elif name == "product_recipes":
        return list_product_recipes()
    elif name == "inquiries":
        return list_inquiries()

    elif name == "orders":
        return list_orders()
    elif name == "sales_reports":
        return list_sales_reports()
    elif name == "alerts":
        return list_alerts()
    elif name == "forecasts":
        return list_forecasts()
    elif name == "accounts":
        return list_accounts()
    elif name == "activity_logs":
        return list_activity_logs()

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


def list_reseller_cart_items(account_id: int) -> list[dict]:
    ensure_system_tables()
    return clean_row(fetch_all("""
        SELECT rci.cart_item_id, rci.account_id, rci.product_id,
               p.name, p.category, p.description, p.unit, p.base_price,
               COALESCE(SUM(pb.quantity_available) FILTER (
                   WHERE pb.quality_status = 'approved'
                     AND (pb.expiry_date IS NULL OR pb.expiry_date >= CURRENT_DATE)
               ), 0) AS available,
               rci.quantity AS cart_quantity,
               round(rci.quantity * p.base_price, 2) AS line_total,
               rci.updated_at
        FROM reseller_cart_items rci
        JOIN inventory_items p ON p.item_id = rci.product_id
        LEFT JOIN inventory_batches pb ON pb.item_id = p.item_id
        WHERE rci.account_id = %s
          AND p.item_type = 'finished_product'
          AND p.is_active = true
        GROUP BY rci.cart_item_id, rci.account_id, rci.product_id,
                 p.name, p.category, p.description, p.unit, p.base_price,
                 rci.quantity, rci.updated_at
        ORDER BY rci.updated_at DESC, rci.cart_item_id DESC;
    """, (account_id,)))


def reseller_cart_count(account_id: int) -> float:
    ensure_system_tables()
    row = fetch_one("""
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM reseller_cart_items
        WHERE account_id = %s;
    """, (account_id,))
    return float(row["total"] if row else 0)


def add_reseller_cart_item(account_id: int, product_id: int, quantity: object) -> dict:
    ensure_system_tables()
    product = product_by_id(product_id)
    if product is None or not product.get("is_active", True):
        raise ValueError("Unknown product")
    clean_quantity = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if clean_quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    row = execute_write("""
        INSERT INTO reseller_cart_items (account_id, product_id, quantity)
        VALUES (%s, %s, %s)
        ON CONFLICT (account_id, product_id)
        DO UPDATE SET
            quantity = reseller_cart_items.quantity + EXCLUDED.quantity,
            updated_at = now()
        RETURNING cart_item_id, account_id, product_id, quantity;
    """, (account_id, product_id, clean_quantity), returning=True)
    return clean_row(row)


def update_reseller_cart_item(account_id: int, product_id: int, quantity: object) -> None:
    ensure_system_tables()
    clean_quantity = parse_inventory_decimal(quantity, "Quantity", "0.001")
    if clean_quantity <= 0:
        remove_reseller_cart_item(account_id, product_id)
        return
    product = product_by_id(product_id)
    if product is None or not product.get("is_active", True):
        raise ValueError("Unknown product")
    execute_write("""
        INSERT INTO reseller_cart_items (account_id, product_id, quantity)
        VALUES (%s, %s, %s)
        ON CONFLICT (account_id, product_id)
        DO UPDATE SET
            quantity = EXCLUDED.quantity,
            updated_at = now();
    """, (account_id, product_id, clean_quantity))


def remove_reseller_cart_item(account_id: int, product_id: int) -> None:
    ensure_system_tables()
    execute_write("""
        DELETE FROM reseller_cart_items
        WHERE account_id = %s
          AND product_id = %s;
    """, (account_id, product_id))


def clear_reseller_cart(account_id: int) -> None:
    ensure_system_tables()
    execute_write("DELETE FROM reseller_cart_items WHERE account_id = %s;", (account_id,))


def role_key_for_account_type(account_type: str) -> str | None:
    if account_type == "team_leader":
        return "team-leader"
    if account_type in {"owner", "reseller"}:
        return account_type
    return None


def authenticate_account(email: str, password: str) -> dict | None:
    ensure_system_tables()
    account = fetch_one(
        """
        SELECT account_id, account_type, name, email, password_hash, is_active, auth_provider
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


def social_login_account(
    auth_user_id: str,
    email: str,
    name: str,
    provider: str,
    allow_reseller_signup: bool = True,
) -> dict | None:
    ensure_system_tables()
    normalized_email = email.strip().lower()
    display_name = " ".join((name or normalized_email.split("@")[0]).split()) or normalized_email

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT account_id, account_type, name, email, is_active, auth_provider
            FROM accounts
            WHERE auth_user_id = %s::uuid OR lower(email) = lower(%s)
            ORDER BY CASE WHEN auth_user_id = %s::uuid THEN 0 ELSE 1 END
            LIMIT 1
            FOR UPDATE;
            """,
            (auth_user_id, normalized_email, auth_user_id),
        )
        account = cur.fetchone()

        if account:
            if not account["is_active"]:
                return None
            cur.execute(
                """
                UPDATE accounts
                SET auth_user_id = COALESCE(auth_user_id, %s::uuid),
                    auth_provider = %s
                WHERE account_id = %s
                RETURNING account_id, account_type, name, email, is_active, auth_provider;
                """,
                (auth_user_id, provider, account["account_id"]),
            )
            account = cur.fetchone()
        elif allow_reseller_signup:
            cur.execute("""
                SELECT account_id
                FROM accounts
                WHERE account_type = 'team_leader'
                  AND is_active = true
                ORDER BY account_id
                LIMIT 1;
            """)
            leader = cur.fetchone()
            leader_id = leader["account_id"] if leader else None
            cur.execute(
                """
                INSERT INTO resellers (
                    business_name, contact_person, email, contact_number, address,
                    reseller_status, team_leader_account_id, approved_at
                )
                VALUES (%s, %s, %s, 'OAuth signup', 'Pending onboarding details', 'active', %s, %s)
                RETURNING reseller_id;
                """,
                (display_name, display_name, normalized_email, leader_id, datetime.now()),
            )
            reseller = cur.fetchone()
            cur.execute(
                """
                INSERT INTO accounts (
                    account_type, reseller_id, name, email, password_hash,
                    is_active, auth_user_id, auth_provider
                )
                VALUES ('reseller', %s, %s, %s, %s, true, %s::uuid, %s)
                RETURNING account_id, account_type, name, email, is_active, auth_provider;
                """,
                (
                    reseller["reseller_id"],
                    display_name,
                    normalized_email,
                    hash_password(secrets.token_urlsafe(32)),
                    auth_user_id,
                    provider,
                ),
            )
            account = cur.fetchone()
        else:
            return None

    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"])
    if clean["role_key"] == "reseller":
        create_notification(
            recipient_role="owner",
            category="account",
            severity="info",
            title="New reseller account",
            message=f"{clean['name']} signed in with {provider.title()} and was added as a reseller.",
            target_url="/portal/owner/accounts",
            source_type="accounts",
            source_id=clean["account_id"],
            dedupe_key=f"social-reseller-{clean['account_id']}",
        )
    return clean if clean["role_key"] else None


def record_user_consent(account_id: int, policy_version: str, consent_source: str, provider: str | None = None) -> None:
    ensure_system_tables()
    execute_write(
        """
        INSERT INTO user_consents (account_id, policy_version, consent_source, provider)
        VALUES (%s, %s, %s, %s);
        """,
        (account_id, policy_version, consent_source, provider),
    )


def create_notification(
    *,
    recipient_role: str | None = None,
    recipient_account_id: int | None = None,
    category: str,
    severity: str = "info",
    title: str,
    message: str,
    target_url: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    dedupe_key: str | None = None,
) -> None:
    if recipient_role is None and recipient_account_id is None:
        return
    ensure_system_tables()
    execute_write(
        """
        INSERT INTO notifications (
            recipient_role, recipient_account_id, category, severity, title, message,
            target_url, source_type, source_id, dedupe_key
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING;
        """,
        (
            recipient_role,
            recipient_account_id,
            category,
            severity,
            title,
            message,
            target_url,
            source_type,
            source_id,
            dedupe_key,
        ),
    )


def list_notifications(role_key: str, account_id: int | None = None, limit: int = 8) -> list[dict]:
    ensure_system_tables()
    return clean_row(fetch_all(
        """
        SELECT notification_id, category, severity, title, message, target_url, read_at, created_at
        FROM notifications
        WHERE (recipient_role = %s OR recipient_account_id = %s)
        ORDER BY read_at NULLS FIRST, created_at DESC
        LIMIT %s;
        """,
        (role_key, account_id, limit),
    ))


def unread_notification_count(role_key: str, account_id: int | None = None) -> int:
    ensure_system_tables()
    row = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM notifications
        WHERE read_at IS NULL
          AND (recipient_role = %s OR recipient_account_id = %s);
        """,
        (role_key, account_id),
    )
    return int(row["total"])


def mark_notifications_read(role_key: str, account_id: int | None = None) -> None:
    ensure_system_tables()
    execute_write(
        """
        UPDATE notifications
        SET read_at = now()
        WHERE read_at IS NULL
          AND (recipient_role = %s OR recipient_account_id = %s);
        """,
        (role_key, account_id),
    )

def current_metrics(team_leader_account_id: int | None = None, reseller_account_id: int | None = None) -> dict:
    ensure_system_tables()
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
                    OR reseller_id IN (
                        SELECT reseller_id
                        FROM resellers
                        WHERE team_leader_account_id = %s
                    )
                )
        """
        pending_scope = """
                AND reseller_id IN (
                    SELECT reseller_id
                    FROM resellers
                    WHERE team_leader_account_id = %s
                )
        """
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
            COALESCE((
                SELECT SUM(ib.quantity_available)
                FROM inventory_batches ib
                JOIN inventory_items ii ON ii.item_id = ib.item_id
                WHERE ii.item_type = 'finished_product'
                  AND ib.quality_status = 'approved'
                  AND (ib.expiry_date IS NULL OR ib.expiry_date >= CURRENT_DATE)
            ), 0) AS total_available;
    """, tuple(params) or None)

    return {
        "fulfilled_sales": float(row["fulfilled_sales"]),
        "pending_reseller_orders": int(row["pending_reseller_orders"]),
        "open_alerts": int(row["open_alerts"]),
        "active_resellers": int(row["active_resellers"]),
        "total_available": float(row["total_available"]),
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
    leader = fetch_one("""
        SELECT account_id
        FROM accounts
        WHERE account_type = 'team_leader'
          AND is_active = true
        ORDER BY account_id
        LIMIT 1;
    """)
    leader_id = leader["account_id"] if leader else None
    
    inq = execute_write("""
        INSERT INTO inquiries (name, contact_number, email, business_name, message, status, assigned_team_leader_account_id)
        VALUES (%s, %s, %s, %s, %s, 'assigned', %s)
        RETURNING inquiry_id, name, contact_number, email, business_name, message, status, created_at;
    """, (name, contact_number, email, business_name, message, leader_id), returning=True)
    
    inq = clean_row(inq)
    
    add_log("MEATTRACK", "created_inquiry", f"Inquiry #{inq['inquiry_id']}")
    create_notification(
        recipient_account_id=leader_id,
        recipient_role=None if leader_id else "owner",
        category="inquiry",
        severity="info",
        title="New reseller inquiry",
        message=f"{business_name} is waiting for review.",
        target_url="/portal/team-leader/inquiries",
        source_type="inquiries",
        source_id=inq["inquiry_id"],
        dedupe_key=f"inquiry-{inq['inquiry_id']}",
    )
    return inq

def add_reseller_from_inquiry(inquiry_id: int, approving_team_leader_account_id: int | None = None) -> dict | None:
    ensure_system_tables()
    inq = fetch_one("SELECT * FROM inquiries WHERE inquiry_id = %s;", (inquiry_id,))
    if not inq:
        return None
    if approving_team_leader_account_id is not None and inq["assigned_team_leader_account_id"] not in {None, approving_team_leader_account_id}:
        return None

    leader_id = approving_team_leader_account_id or inq["assigned_team_leader_account_id"]
    if leader_id is None:
        leader = fetch_one("""
            SELECT account_id
            FROM accounts
            WHERE account_type = 'team_leader'
              AND is_active = true
            ORDER BY account_id
            LIMIT 1;
        """)
        leader_id = leader["account_id"] if leader else None
    
    execute_write("""
        UPDATE inquiries 
        SET status = 'approved', assigned_team_leader_account_id = %s, reviewed_by_account_id = %s, reviewed_at = %s
        WHERE inquiry_id = %s;
    """, (leader_id, leader_id, datetime.now(), inquiry_id))
    
    res = execute_write("""
        INSERT INTO resellers (inquiry_id, business_name, contact_person, email, contact_number, address, reseller_status, team_leader_account_id, approved_by_account_id, approved_at)
        VALUES (%s, %s, %s, %s, %s, 'Pending onboarding details', 'active', %s, %s, %s)
        RETURNING reseller_id, business_name, contact_person, email, contact_number, address, reseller_status, team_leader_account_id, approved_by_account_id, created_at;
    """, (inquiry_id, inq["business_name"], inq["name"], inq["email"], inq["contact_number"], leader_id, leader_id, datetime.now()), returning=True)
    
    res = clean_row(res)
    
    execute_write("""
        INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
        VALUES ('reseller', %s, %s, %s, %s, true);
    """, (res["reseller_id"], res["business_name"], res["email"], hash_password(RESELLER_PASSWORD)))
    
    add_log("Maria Santos", "approved_reseller_inquiry", f"Inquiry #{inquiry_id}")
    create_notification(
        recipient_role="owner",
        category="account",
        severity="info",
        title="Reseller approved",
        message=f"{res['business_name']} was approved from inquiry #{inquiry_id}.",
        target_url="/portal/owner/accounts",
        source_type="inquiries",
        source_id=inquiry_id,
        dedupe_key=f"inquiry-approved-{inquiry_id}",
    )
    return res

def reject_inquiry(inquiry_id: int, reviewing_team_leader_account_id: int | None = None) -> bool:
    inq = fetch_one("SELECT * FROM inquiries WHERE inquiry_id = %s;", (inquiry_id,))
    if not inq:
        return False
    if reviewing_team_leader_account_id is not None and inq["assigned_team_leader_account_id"] not in {None, reviewing_team_leader_account_id}:
        return False
    
    execute_write("""
        UPDATE inquiries 
        SET status = 'rejected', assigned_team_leader_account_id = %s, reviewed_by_account_id = %s, reviewed_at = %s
        WHERE inquiry_id = %s;
    """, (reviewing_team_leader_account_id or inq["assigned_team_leader_account_id"], reviewing_team_leader_account_id or inq["assigned_team_leader_account_id"], datetime.now(), inquiry_id))
    

    
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

def create_order_from_items(role: str, items: list[tuple[int, object]], notes: str = "", account_id: int | None = None) -> dict:
    if not items:
        raise ValueError("Add at least one product to the cart.")

    quantities: dict[int, Decimal] = {}
    for product_id, quantity in items:
        try:
            clean_product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Unknown product")
        clean_quantity = parse_inventory_decimal(quantity, "Quantity", "0.001")
        if clean_quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        quantities[clean_product_id] = quantities.get(clean_product_id, Decimal("0")) + clean_quantity

    order_type = "reseller" if role == "reseller" else "walk_in"
    reseller_id = None
    created_by_name = "Maria Santos"
    team_leader_account_id = None
    creator_id = account_id
    status = "pending" if role == "reseller" else "fulfilled"

    if role == "reseller":
        if account_id is None:
            raise ValueError("Your session expired. Please sign in again.")
        profile = require_reseller_team_leader(account_id)
        reseller_id = profile["reseller_id"]
        created_by_name = profile["business_name"]
        team_leader_account_id = profile["team_leader_account_id"]
    elif account_id is not None:
        account = fetch_one("SELECT name FROM accounts WHERE account_id = %s LIMIT 1;", (account_id,))
        if account:
            created_by_name = account["name"]

    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT item_id AS product_id, name, unit, base_price
            FROM inventory_items
            WHERE item_type = 'finished_product'
              AND is_active = true
              AND item_id = ANY(%s);
        """, (list(quantities.keys()),))
        products = {row["product_id"]: row for row in cur.fetchall()}
        if len(products) != len(quantities):
            raise ValueError("Unknown product")

        if creator_id is None:
            cur.execute("SELECT account_id FROM accounts WHERE name = %s LIMIT 1;", (created_by_name,))
            acc = cur.fetchone()
            creator_id = acc["account_id"] if acc else None

        total = Decimal("0.00")
        lines = []
        for product_id, quantity in quantities.items():
            product = products[product_id]
            line_total = (product["base_price"] * quantity).quantize(Decimal("0.01"))
            total += line_total
            lines.append((product_id, quantity, product["unit"], product["base_price"]))

        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, approved_by_account_id, approved_at, status, order_date, fulfilled_at, total_amount, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING order_id, order_type, reseller_id, status, order_date, total_amount, notes;
        """, (
            order_type, reseller_id, creator_id,
            creator_id if status == "fulfilled" else None,
            datetime.now() if status == "fulfilled" else None,
            status, date.today(),
            datetime.now() if status == "fulfilled" else None,
            total.quantize(Decimal("0.01")), notes
        ))
        ord_res = cur.fetchone()

        order_id = ord_res["order_id"]
        for product_id, quantity, unit, unit_price in lines:
            cur.execute("""
                INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
                VALUES (%s, %s, %s, %s, %s);
            """, (order_id, product_id, quantity, unit, unit_price))

    if status == "fulfilled":
        for product_id, quantity in quantities.items():
            deduct_stock_fefo(product_id, float(quantity))
        add_log("Maria Santos", "created_walk_in_sale", f"Order #{order_id}")
    else:
        add_log(created_by_name, "created_reseller_order", f"Order #{order_id}")
        create_notification(
            recipient_account_id=team_leader_account_id,
            category="order",
            severity="warning",
            title="Pending reseller order",
            message=f"Order #{order_id} from {created_by_name} needs review.",
            target_url="/portal/team-leader/orders",
            source_type="orders",
            source_id=order_id,
            dedupe_key=f"reseller-order-{order_id}",
        )
    
    return clean_row(ord_res)


def create_order(role: str, product_id: int, quantity: float, notes: str = "", account_id: int | None = None) -> dict:
    return create_order_from_items(role, [(product_id, quantity)], notes, account_id=account_id)

def decide_order(order_id: int, decision: str, team_leader_account_id: int | None = None) -> bool:
    ensure_system_tables()
    if team_leader_account_id is None:
        ord_res = fetch_one("SELECT * FROM orders WHERE order_id = %s;", (order_id,))
    else:
        ord_res = fetch_one("""
            SELECT o.*
            FROM orders o
            JOIN resellers r ON r.reseller_id = o.reseller_id
            WHERE o.order_id = %s
              AND r.team_leader_account_id = %s;
        """, (order_id, team_leader_account_id))
    if not ord_res or ord_res["order_type"] != "reseller":
        return False
    if ord_res["status"] in {"fulfilled", "rejected"}:
        return False
    
    leader_id = team_leader_account_id
    if leader_id is None:
        leader = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'team_leader' LIMIT 1;")
        leader_id = leader["account_id"] if leader else None
    
    if decision == "approve":
        execute_write("""
            UPDATE orders 
            SET status = 'approved', approved_by_account_id = %s, approved_at = %s 
            WHERE order_id = %s;
        """, (leader_id, datetime.now(), order_id))
        add_log("Maria Santos", "approved_reseller_order", f"Order #{order_id}")
        create_notification(
            recipient_account_id=ord_res["created_by_account_id"],
            category="order",
            severity="info",
            title="Order approved",
            message=f"Your reseller order #{order_id} was approved.",
            target_url="/portal/reseller/history",
            source_type="orders",
            source_id=order_id,
            dedupe_key=f"order-approved-{order_id}",
        )
        
    elif decision == "reject":
        execute_write("""
            UPDATE orders 
            SET status = 'rejected', approved_by_account_id = %s, approved_at = %s 
            WHERE order_id = %s;
        """, (leader_id, datetime.now(), order_id))
        add_log("Maria Santos", "rejected_reseller_order", f"Order #{order_id}")
        create_notification(
            recipient_account_id=ord_res["created_by_account_id"],
            category="order",
            severity="critical",
            title="Order rejected",
            message=f"Your reseller order #{order_id} was rejected.",
            target_url="/portal/reseller/history",
            source_type="orders",
            source_id=order_id,
            dedupe_key=f"order-rejected-{order_id}",
        )
        
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
        create_notification(
            recipient_account_id=ord_res["created_by_account_id"],
            category="order",
            severity="info",
            title="Order fulfilled",
            message=f"Your reseller order #{order_id} was marked fulfilled.",
            target_url="/portal/reseller/history",
            source_type="orders",
            source_id=order_id,
            dedupe_key=f"order-fulfilled-{order_id}",
        )
    else:
        return False
    return True


def team_sales_report_totals(period_start: date, period_end: date, team_leader_account_id: int | None = None) -> dict:
    ensure_system_tables()
    scope_sql = ""
    params: list[object] = [period_start, period_end]
    if team_leader_account_id is not None:
        scope_sql = """
          AND (
              (o.order_type = 'walk_in' AND o.created_by_account_id = %s)
              OR (o.order_type = 'reseller' AND r.team_leader_account_id = %s)
          )
        """
        params.extend([team_leader_account_id, team_leader_account_id])
    totals = fetch_one(f"""
        SELECT COALESCE(SUM(oi.line_total), 0) AS total_sales,
               COUNT(DISTINCT o.order_id) AS total_orders
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        WHERE o.status = 'fulfilled'
          AND p.item_type = 'finished_product'
          AND COALESCE(o.fulfilled_at, o.order_date)::date BETWEEN %s AND %s
          {scope_sql};
    """, tuple(params))
    totals = clean_row(totals)
    return {
        "total_sales": float(totals["total_sales"] or 0),
        "total_orders": int(totals["total_orders"] or 0),
    }


def team_sales_report_entries(team_leader_account_id: int | None = None) -> list[dict]:
    ensure_system_tables()
    scope_sql = ""
    params: list[object] = []
    if team_leader_account_id is not None:
        scope_sql = """
          AND (
              (o.order_type = 'walk_in' AND o.created_by_account_id = %s)
              OR (o.order_type = 'reseller' AND r.team_leader_account_id = %s)
          )
        """
        params.extend([team_leader_account_id, team_leader_account_id])
    entries = clean_row(fetch_all(f"""
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
          {scope_sql}
        ORDER BY sale_date DESC, o.order_id DESC, p.name;
    """, tuple(params) or None))
    for entry in entries:
        entry["sale_date"] = entry["sale_date"].isoformat()
    return entries


def team_rejected_order_entries(team_leader_account_id: int | None = None) -> list[dict]:
    ensure_system_tables()
    scope_sql = ""
    params: list[object] = []
    if team_leader_account_id is not None:
        scope_sql = "AND r.team_leader_account_id = %s"
        params.append(team_leader_account_id)
    return clean_row(fetch_all(f"""
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
          {scope_sql}
        GROUP BY o.order_id, r.business_name, o.approved_at, o.order_date, o.total_amount, o.notes
        ORDER BY rejected_at DESC, o.order_id DESC;
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
    ensure_system_tables()
    if account_id is not None:
        acc = fetch_one("SELECT account_id, name FROM accounts WHERE account_id = %s LIMIT 1;", (account_id,))
    else:
        acc = fetch_one("SELECT account_id, name FROM accounts WHERE name = %s LIMIT 1;", (submitted_by,))
    acc_id = acc["account_id"] if acc else None
    actor_name = acc["name"] if acc else submitted_by
    
    reseller_id = None
    department_id = None
    if source == "reseller":
        if account_id is None:
            raise ValueError("Reseller reports require a signed-in reseller account.")
        profile = require_reseller_team_leader(account_id)
        reseller_id = profile["reseller_id"]
    else:
        dep = fetch_one("SELECT department_id FROM departments LIMIT 1;")
        department_id = dep["department_id"] if dep else 1
        
    rep = execute_write("""
        INSERT INTO sales_reports (report_source, submitted_by_account_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING sales_report_id, report_source, period_start, period_end, total_sales, total_orders, notes;
    """, (source, acc_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes), returning=True)
    
    add_log(actor_name, "submitted_sales_report", f"Report #{rep['sales_report_id']}")
    return clean_row(rep)


def reseller_account_profile(account_id: int) -> dict:
    ensure_system_tables()
    row = fetch_one("""
        SELECT a.account_id, a.name, a.email, a.reseller_id,
               COALESCE(r.business_name, a.name) AS business_name,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               tl.email AS team_leader_email
        FROM accounts a
        LEFT JOIN resellers r ON r.reseller_id = a.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        WHERE a.account_id = %s
          AND a.account_type = 'reseller'
        LIMIT 1;
    """, (account_id,))
    if not row:
        raise ValueError("Reseller account was not found.")
    if row["reseller_id"] is None:
        reseller = fetch_one("""
            SELECT r.reseller_id,
                   r.business_name,
                   r.team_leader_account_id,
                   tl.name AS team_leader_name,
                   tl.email AS team_leader_email
            FROM resellers r
            LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
            WHERE lower(r.email) = lower(%s)
            LIMIT 1;
        """, (row["email"],))
        if reseller:
            row["reseller_id"] = reseller["reseller_id"]
            row["business_name"] = reseller["business_name"]
            row["team_leader_account_id"] = reseller["team_leader_account_id"]
            row["team_leader_name"] = reseller["team_leader_name"]
            row["team_leader_email"] = reseller["team_leader_email"]
    if row["reseller_id"] is None:
        raise ValueError("This reseller account is not linked to an approved reseller profile.")
    return clean_row(row)


def require_reseller_team_leader(account_id: int) -> dict:
    profile = reseller_account_profile(account_id)
    if not profile.get("team_leader_account_id"):
        raise ValueError("Your reseller account is not assigned to a team leader yet. Please contact the owner before submitting orders or reports.")
    return profile


def reseller_month_orders(account_id: int, period_start: date, period_end: date) -> list[dict]:
    ensure_system_tables()
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
    ensure_system_tables()
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
    ensure_system_tables()
    profile = require_reseller_team_leader(account_id)
    reportable = {int(row["product_id"]): row for row in reseller_reportable_products(account_id, period_start, period_end)}
    clean_quantities: dict[int, Decimal] = {}

    for product_id, quantity in quantities.items():
        try:
            clean_product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Unknown product.")
        clean_quantity = parse_inventory_decimal(quantity, "Sold quantity", "0.001")
        if clean_quantity <= 0:
            continue
        if clean_product_id not in reportable:
            raise ValueError("You can only report products from fulfilled orders that still have an unreported balance.")
        remaining = Decimal(str(reportable[clean_product_id]["reportable_quantity"]))
        if clean_quantity > remaining:
            raise ValueError(f"{reportable[clean_product_id]['name']} can only report up to {display_decimal(remaining)} {reportable[clean_product_id]['unit']}.")
        clean_quantities[clean_product_id] = clean_quantities.get(clean_product_id, Decimal("0")) + clean_quantity

    if not clean_quantities:
        raise ValueError("Report at least one sold product quantity.")

    total_sales = Decimal("0.00")
    total_units = Decimal("0")
    lines = []
    for product_id, quantity in clean_quantities.items():
        product = reportable[product_id]
        unit_price = Decimal(str(product["unit_price"])).quantize(Decimal("0.01"))
        total_sales += (unit_price * quantity).quantize(Decimal("0.01"))
        total_units += quantity
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

    add_log(profile["business_name"], "submitted_reseller_sell_through_report", f"Report #{rep['sales_report_id']}")
    create_notification(
        recipient_account_id=profile["team_leader_account_id"],
        category="report",
        severity="info",
        title="Reseller sales report submitted",
        message=f"{profile['business_name']} submitted sell-through report #{rep['sales_report_id']}.",
        target_url="/portal/team-leader/reports",
        source_type="sales_reports",
        source_id=rep["sales_report_id"],
        dedupe_key=f"reseller-report-{rep['sales_report_id']}",
    )
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
    if days <= 7:
        create_notification(
            recipient_role="team-leader",
            category="inventory",
            severity="critical" if days <= 2 else "warning",
            title="Batch expiry warning",
            message=f"{product['name']} batch {batch_code} expires in {days} day(s).",
            target_url="/portal/team-leader/inventory",
            source_type="inventory_batches",
            source_id=batch["product_batch_id"],
            dedupe_key=f"batch-expiry-{batch['product_batch_id']}",
        )
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
        create_notification(
            recipient_role="team-leader",
            category="inventory",
            severity="critical" if days <= 2 else "warning",
            title="Batch expiry warning",
            message=f"{product['name']} batch {batch_code} expires in {days} day(s).",
            target_url="/portal/team-leader/inventory",
            source_type="inventory_batches",
            source_id=batch["product_batch_id"],
            dedupe_key=f"batch-expiry-{batch['product_batch_id']}",
        )
        
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
    create_notification(
        recipient_role="owner",
        category="forecast",
        severity="info",
        title="Forecast updated",
        message=f"{model_name} generated a {forecast_horizon_days}-day demand forecast.",
        target_url="/portal/owner/dashboard",
        source_type="forecast_runs",
        source_id=run_id,
        dedupe_key=f"forecast-run-{run_id}",
    )

def update_product_price(product_id: int, base_price: float) -> None:
    execute_write("""
        UPDATE inventory_items
        SET base_price = %s
        WHERE item_id = %s
          AND item_type = 'finished_product';
    """, (base_price, product_id))
    
    prod = fetch_one("SELECT name FROM inventory_items WHERE item_id = %s AND item_type = 'finished_product';", (product_id,))
    add_log("Owner", "updated_product_price", prod["name"] if prod else f"Product #{product_id}")
