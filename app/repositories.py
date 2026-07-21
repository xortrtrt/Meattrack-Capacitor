from __future__ import annotations
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
import os
import secrets
import tempfile

from app.database import fetch_all, fetch_one, execute_write, clean_row, get_transaction_cursor
from app.config import DEFAULT_ACCOUNT_PASSWORD
from app.security import hash_password, password_needs_rehash, validate_password_policy, verify_password

today = date.today()


def safe_sort_sql(sort: str, allowed: dict[str, str], default_sql: str) -> str:
    """Return a whitelisted ORDER BY clause fragment."""
    return allowed.get((sort or "").strip(), default_sql)


PRODUCT_SORTS = {
    "name_asc": "p.name ASC, p.item_id ASC",
    "name_desc": "p.name DESC, p.item_id DESC",
    "price_asc": "p.base_price ASC, p.name ASC",
    "price_desc": "p.base_price DESC, p.name ASC",
    "stock_desc": "available DESC, p.name ASC",
}

INVENTORY_ITEM_SORTS = {
    "name_asc": "ii.name ASC, ii.item_id ASC",
    "category_asc": "ii.category ASC, ii.name ASC",
    "stock_desc": "available DESC, ii.name ASC",
    "stock_asc": "available ASC, ii.name ASC",
}

BATCH_SORTS = {
    "newest": "ib.batch_id DESC",
    "oldest": "ib.batch_id ASC",
    "expiry_asc": "ib.expiry_date ASC NULLS LAST, ib.batch_id ASC",
    "available_desc": "ib.quantity_available DESC, ib.batch_id DESC",
    "product_asc": "ii.name ASC, ib.batch_id DESC",
}

INQUIRY_SORTS = {
    "newest": "i.inquiry_id DESC",
    "oldest": "i.inquiry_id ASC",
    "business_asc": "i.business_name ASC, i.inquiry_id DESC",
    "status_asc": "i.status ASC, i.inquiry_id DESC",
}

ORDER_SORTS = {
    "newest": "o.order_id DESC",
    "oldest": "o.order_id ASC",
    "total_desc": "o.total_amount DESC, o.order_id DESC",
    "total_asc": "o.total_amount ASC, o.order_id DESC",
    "status_asc": "o.status ASC, o.order_id DESC",
}

REPORT_SORTS = {
    "newest": "sr.sales_report_id DESC",
    "oldest": "sr.sales_report_id ASC",
    "period_desc": "sr.period_end DESC, sr.sales_report_id DESC",
    "sales_desc": "sr.total_sales DESC, sr.sales_report_id DESC",
    "orders_desc": "sr.total_orders DESC, sr.sales_report_id DESC",
}

FORECAST_SORTS = {
    "newest": "fr.forecast_result_id DESC",
    "product_asc": "p.name ASC, fr.forecast_date DESC",
    "date_desc": "fr.forecast_date DESC, p.name ASC",
    "qty_desc": "fr.predicted_quantity DESC, p.name ASC",
}

ACCOUNT_SORTS = {
    "newest": "a.account_id DESC",
    "oldest": "a.account_id ASC",
    "name_asc": "a.name ASC, a.account_id ASC",
    "type_asc": "a.account_type ASC, a.name ASC",
    "status_asc": "a.is_active DESC, a.name ASC",
}

LOG_SORTS = {
    "newest": "al.activity_log_id DESC",
    "oldest": "al.activity_log_id ASC",
    "actor_asc": "COALESCE(a.name, 'MEATTRACK') ASC, al.activity_log_id DESC",
    "action_asc": "al.action ASC, al.activity_log_id DESC",
}


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
    ADD COLUMN IF NOT EXISTS auth_provider text,
    ADD COLUMN IF NOT EXISTS team_leader_role text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'accounts_team_leader_role_check'
    ) THEN
        ALTER TABLE accounts
            ADD CONSTRAINT accounts_team_leader_role_check
            CHECK (team_leader_role IS NULL OR team_leader_role IN ('inventory', 'sales'));
    END IF;
END $$;

UPDATE accounts
SET team_leader_role = 'inventory'
WHERE account_type = 'team_leader'
  AND team_leader_role IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_auth_user_id
    ON accounts (auth_user_id)
    WHERE auth_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_accounts_team_leader_role
    ON accounts (team_leader_role)
    WHERE account_type = 'team_leader';

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
      AND team_leader_role = 'sales'
    ORDER BY account_id
    LIMIT 1
)
WHERE team_leader_account_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM accounts
      WHERE account_type = 'team_leader'
        AND is_active = true
        AND team_leader_role = 'sales'
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

CREATE TABLE IF NOT EXISTS account_password_otps (
    account_password_otp_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    otp_hash text NOT NULL,
    pending_password_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(otp_hash) <> ''),
    CHECK (btrim(pending_password_hash) <> '')
);

ALTER TABLE account_password_otps ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS ix_account_password_otps_pending
    ON account_password_otps (account_id, created_at DESC)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS account_login_otps (
    account_login_otp_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    otp_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(otp_hash) <> '')
);

ALTER TABLE account_login_otps ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS ix_account_login_otps_pending
    ON account_login_otps (account_id, created_at DESC)
    WHERE consumed_at IS NULL;

ALTER TABLE inquiries
    ADD COLUMN IF NOT EXISTS follow_up_sent_at timestamptz;

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

CREATE TABLE IF NOT EXISTS order_payment_proofs (
    order_payment_proof_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    uploaded_by_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    filename text NOT NULL,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 5242880),
    checksum_sha256 text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\/]'),
    CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    CHECK (length(checksum_sha256) = 64)
);

CREATE INDEX IF NOT EXISTS ix_order_payment_proofs_order
    ON order_payment_proofs (order_id, uploaded_at DESC);

ALTER TABLE order_payment_proofs ENABLE ROW LEVEL SECURITY;

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
        "name": "Team Leader",
        "email": "team.leader@batangaspremium.test",
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

team_leader_role_labels = {
    "inventory": "Inventory Team Leader",
    "sales": "Sales Team Leader",
}

portal_nav = {
    "reseller": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("order", "Products", "shopping-basket"),
        ("cart", "Cart", "shopping-cart"),
        ("history", "Order History", "clipboard-list"),
        ("profile", "Profile", "user-cog"),
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
    ],
}

team_leader_nav_by_role = {
    "inventory": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("inventory", "Inventory", "boxes"),
        ("raw-materials", "Raw Materials", "beef"),
        ("finished-products", "Finished Products", "package-search"),
        ("batches", "Product Batches", "package-check"),
        ("logs", "Inventory Logs", "scroll-text"),
        ("profile", "Profile", "user-cog"),
    ],
    "sales": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("reports", "Reports", "file-text"),
        ("profile", "Profile", "user-cog"),
    ],
}


def portal_nav_for(role_key: str, team_leader_role: str | None = None) -> list[tuple[str, str, str]]:
    if role_key == "team-leader":
        return team_leader_nav_by_role.get(team_leader_role or "sales", team_leader_nav_by_role["sales"])
    return portal_nav[role_key]


def default_section_for(role_key: str, team_leader_role: str | None = None) -> str:
    nav = portal_nav_for(role_key, team_leader_role)
    return nav[0][0] if nav else roles[role_key]["default_section"]


# ----------------- Explicit read operations -----------------

def inventory_item_filter(category: str) -> tuple[str, str]:
    raw = (category or "").strip()
    if ":" in raw:
        item_type, item_category = raw.split(":", 1)
        if item_type in {"raw_material", "finished_product"}:
            return item_type, item_category.strip().replace("_", " ")
    if raw in {"raw_material", "finished_product"}:
        return raw, ""
    return "", raw.replace("_", " ")


def list_inventory_items(q: str = "", category: str = "", page: int | None = None, page_size: int = 10, sort: str = "") -> list[dict]:
    where = ["ii.item_type IN ('raw_material', 'finished_product')"]
    params: list[object] = []
    q = q.strip()
    item_type, item_category = inventory_item_filter(category)
    if q:
        where.append("(ii.name ILIKE %s OR ii.category ILIKE %s OR ii.unit ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if item_type:
        where.append("ii.item_type = %s")
        params.append(item_type)
    if item_category:
        where.append("LOWER(ii.category) = LOWER(%s)")
        params.append(item_category)
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, INVENTORY_ITEM_SORTS, "CASE ii.item_type WHEN 'raw_material' THEN 1 ELSE 2 END, ii.category, ii.name")
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
        WHERE {where_sql}
        GROUP BY ii.item_id, ii.item_type, ii.category, ii.name, ii.unit,
                 ii.base_price, ii.quantity_available, ii.is_active
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=" AND ".join(where), order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


def count_inventory_items(q: str = "", category: str = "") -> int:
    where = ["ii.item_type IN ('raw_material', 'finished_product')"]
    params: list[object] = []
    q = q.strip()
    item_type, item_category = inventory_item_filter(category)
    if q:
        where.append("(ii.name ILIKE %s OR ii.category ILIKE %s OR ii.unit ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if item_type:
        where.append("ii.item_type = %s")
        params.append(item_type)
    if item_category:
        where.append("LOWER(ii.category) = LOWER(%s)")
        params.append(item_category)
    row = fetch_one(f"""
        SELECT COUNT(*) AS total
        FROM inventory_items ii
        WHERE {" AND ".join(where)};
    """, tuple(params) or None)
    return int(row["total"])


def list_inventory_batches(q: str = "", category: str = "", page: int | None = None, page_size: int = 10, sort: str = "") -> list[dict]:
    where = ["ii.item_type = 'finished_product'"]
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        where.append("(ib.batch_code ILIKE %s OR ii.name ILIKE %s OR ib.source_type ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if category:
        where.append("ii.category = %s")
        params.append(category)
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, BATCH_SORTS, BATCH_SORTS["newest"])
    return clean_row(fetch_all("""
        SELECT ib.batch_id, ib.item_id, ii.item_type,
               CASE ii.item_type
                   WHEN 'raw_material' THEN 'Raw material'
                   WHEN 'finished_product' THEN 'Finished product'
               END AS item_type_label,
               ii.name AS item_name, ii.category, ib.batch_code, ib.source_type,
               ib.quantity_received, ib.quantity_available, ib.unit,
               ib.received_date, ib.expiry_date, ib.quality_status
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE {where_sql}
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=" AND ".join(where), order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


def count_inventory_batches(q: str = "", category: str = "") -> int:
    where = ["ii.item_type = 'finished_product'"]
    params: list[object] = []
    q = q.strip()
    category = category.strip()
    if q:
        where.append("(ib.batch_code ILIKE %s OR ii.name ILIKE %s OR ib.source_type ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if category:
        where.append("ii.category = %s")
        params.append(category)
    row = fetch_one(f"""
        SELECT COUNT(*) AS total
        FROM inventory_batches ib
        JOIN inventory_items ii ON ii.item_id = ib.item_id
        WHERE {" AND ".join(where)};
    """, tuple(params) or None)
    return int(row["total"])


def list_products(q: str = "", category: str = "", page: int | None = None, page_size: int = 12, sort: str = "") -> list[dict]:
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
    order_sql = safe_sort_sql(sort, PRODUCT_SORTS, "p.item_id")
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
        ORDER BY {order_sql}
        {paging_sql};
    """.format(
        where_extra=("AND " + " AND ".join(where_extra)) if where_extra else "",
        order_sql=order_sql,
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


def list_product_recipes(product_ids: list[int] | None = None) -> list[dict]:
    where = [
        "p.item_type = 'finished_product'",
        "rm.item_type = 'raw_material'",
    ]
    params: list[object] = []
    if product_ids is not None:
        scoped_product_ids = [int(product_id) for product_id in product_ids]
        if not scoped_product_ids:
            return []
        where.append("pr.product_item_id = ANY(%s)")
        params.append(scoped_product_ids)

    return clean_row(fetch_all(f"""
        SELECT pr.recipe_id, pr.product_item_id AS product_id, p.name AS product_name,
               pr.material_item_id AS raw_material_id, rm.name AS raw_material_name,
               rm.category AS raw_material_category,
               pr.quantity_required, pr.unit
        FROM product_recipes pr
        JOIN inventory_items p ON p.item_id = pr.product_item_id
        JOIN inventory_items rm ON rm.item_id = pr.material_item_id
        WHERE {" AND ".join(where)}
        ORDER BY p.name, rm.category, rm.name;
    """, tuple(params) or None))


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
    sort: str = "",
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
    query += f" ORDER BY {safe_sort_sql(sort, INQUIRY_SORTS, INQUIRY_SORTS['newest'])}"
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
    sort: str = "",
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
    order_sql = safe_sort_sql(sort, ORDER_SORTS, ORDER_SORTS["newest"])
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
        ORDER BY {order_sql}
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

    proofs = clean_row(fetch_all("""
        SELECT order_payment_proof_id,
               order_id,
               filename,
               content_type,
               size_bytes,
               checksum_sha256,
               uploaded_at
        FROM order_payment_proofs
        WHERE order_id = ANY(%s)
        ORDER BY uploaded_at DESC, order_payment_proof_id DESC;
    """, (order_ids,)))
    proofs_by_order: dict[int, list[dict]] = {}
    for proof in proofs:
        order_id = proof.pop("order_id")
        proofs_by_order.setdefault(order_id, []).append(proof)
    for order in orders:
        order["payment_proofs"] = proofs_by_order.get(order["order_id"], [])
        order["payment_proof_count"] = len(order["payment_proofs"])
    return orders


def add_order_payment_proofs(account_id: int, order_id: int, attachments: list[dict]) -> int:
    ensure_system_tables()
    if not attachments:
        raise ValueError("Attach at least one proof of payment screenshot.")
    profile = reseller_account_profile(account_id)
    order = fetch_one(
        """
        SELECT order_id, status
        FROM orders
        WHERE order_id = %s
          AND order_type = 'reseller'
          AND reseller_id = %s
        LIMIT 1;
        """,
        (order_id, profile["reseller_id"]),
    )
    if not order:
        raise ValueError("Order was not found.")
    if order["status"] not in {"pending", "rejected"}:
        raise ValueError("Proof of payment can only be uploaded while the order is pending or rejected.")
    inserted = 0
    with get_transaction_cursor() as cur:
        for attachment in attachments:
            cur.execute(
                """
                INSERT INTO order_payment_proofs (
                    order_id, uploaded_by_account_id, filename, content_type,
                    content, size_bytes, checksum_sha256
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    order_id,
                    account_id,
                    attachment["filename"],
                    attachment["content_type"],
                    attachment["content"],
                    attachment["size_bytes"],
                    attachment["checksum_sha256"],
                ),
            )
            inserted += 1
    add_log(profile["business_name"], "uploaded_payment_proof", f"Order #{order_id}")
    create_notification(
        recipient_account_id=profile["team_leader_account_id"],
        category="order",
        severity="info",
        title="Payment proof uploaded",
        message=f"{profile['business_name']} uploaded payment proof for order #{order_id}.",
        target_url="/portal/team-leader/orders",
        source_type="orders",
        source_id=order_id,
        dedupe_key=f"order-proof-{order_id}-{inserted}-{datetime.now().timestamp()}",
    )
    return inserted


def get_order_payment_proof(proof_id: int, account_id: int | None, role_key: str | None) -> dict | None:
    ensure_system_tables()
    proof = fetch_one(
        """
        SELECT opp.order_payment_proof_id,
               opp.order_id,
               opp.filename,
               opp.content_type,
               opp.content,
               opp.size_bytes,
               o.created_by_account_id,
               r.team_leader_account_id
        FROM order_payment_proofs opp
        JOIN orders o ON o.order_id = opp.order_id
        LEFT JOIN resellers r ON r.reseller_id = o.reseller_id
        WHERE opp.order_payment_proof_id = %s
        LIMIT 1;
        """,
        (proof_id,),
    )
    if not proof:
        return None
    if role_key == "owner":
        return clean_row(proof)
    if role_key == "reseller" and account_id == proof["created_by_account_id"]:
        return clean_row(proof)
    if role_key == "team-leader" and account_id == proof["team_leader_account_id"]:
        return clean_row(proof)
    return None


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
    sort: str = "",
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
    query += f" ORDER BY {safe_sort_sql(sort, REPORT_SORTS, REPORT_SORTS['newest'])}"
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


def list_forecasts(limit: int | None = None, q: str = "", page: int | None = None, page_size: int = 10, sort: str = "") -> list[dict]:
    query = """
        SELECT fr.forecast_result_id, p.name AS product, fr.forecast_date, fr.predicted_quantity,
               fr.confidence_lower,
               fr.confidence_upper,
               CASE
                   WHEN fr.confidence_lower IS NOT NULL AND fr.confidence_upper IS NOT NULL
                   THEN trim(to_char(fr.confidence_lower, 'FM999999990.0')) || ' - ' || trim(to_char(fr.confidence_upper, 'FM999999990.0')) || ' packs'
                   ELSE 'Range unavailable'
               END AS confidence,
               f.model_name,
               f.forecast_horizon_days,
               f.status,
               f.notes
        FROM forecast_results fr
        JOIN inventory_items p ON p.item_id = fr.product_id
        JOIN forecast_runs f ON f.forecast_run_id = fr.forecast_run_id
        WHERE p.item_type = 'finished_product'
    """
    params: list[object] = []
    q = q.strip()
    if q:
        query += " AND p.name ILIKE %s"
        params.append(f"%{q}%")
    query += f" ORDER BY {safe_sort_sql(sort, FORECAST_SORTS, FORECAST_SORTS['newest'])}"
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


def latest_forecast_run() -> dict | None:
    row = fetch_one("""
        SELECT forecast_run_id,
               model_name,
               input_period_start,
               input_period_end,
               forecast_horizon_days,
               status,
               started_at,
               completed_at,
               notes
        FROM forecast_runs
        ORDER BY forecast_run_id DESC
        LIMIT 1;
    """)
    return clean_row(row) if row else None


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


def list_accounts(q: str = "", account_type: str = "", page: int | None = None, page_size: int = 10, sort: str = "") -> list[dict]:
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
    order_sql = safe_sort_sql(sort, ACCOUNT_SORTS, ACCOUNT_SORTS["oldest"])
    return clean_row(fetch_all("""
        SELECT a.account_id, a.account_type, a.team_leader_role, a.reseller_id, a.name, a.email,
               (CASE WHEN a.is_active THEN 'active' ELSE 'inactive' END) AS status,
               a.auth_provider,
               r.team_leader_account_id,
               tl.name AS team_leader_name
        FROM accounts a
        LEFT JOIN resellers r ON r.reseller_id = a.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        {where_sql}
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=where_sql, order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


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
        SELECT account_id, name, email, team_leader_role
        FROM accounts
        WHERE account_type = 'team_leader'
          AND is_active = true
          AND team_leader_role = 'sales'
        ORDER BY name, account_id;
    """))


def next_sales_team_leader() -> dict | None:
    ensure_system_tables()
    leaders = list_team_leader_accounts()
    if not leaders:
        return None
    row = fetch_one("""
        SELECT COUNT(*) AS total
        FROM inquiries
        WHERE assigned_team_leader_account_id IS NOT NULL;
    """)
    index = int(row["total"] if row else 0) % len(leaders)
    return leaders[index]


def list_all_team_leader_accounts() -> list[dict]:
    ensure_system_tables()
    return clean_row(fetch_all("""
        SELECT account_id, name, email, team_leader_role
        FROM accounts
        WHERE account_type = 'team_leader'
          AND is_active = true
        ORDER BY team_leader_role, name, account_id;
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


INVENTORY_LOG_ACTIONS = (
    "updated_raw_inventory",
    "added_raw_inventory_quantity",
    "created_product_recipe",
    "produced_product_batch",
    "registered_product_batch",
)


def list_activity_logs(q: str = "", page: int | None = None, page_size: int = 10, inventory_only: bool = False, sort: str = "") -> list[dict]:
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


def count_activity_logs(q: str = "", inventory_only: bool = False) -> int:
    query = """
        SELECT COUNT(*) AS total
        FROM activity_logs al
        LEFT JOIN accounts a ON a.account_id = al.account_id
    """
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
    if where:
        query += " WHERE " + " AND ".join(where)
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
        SELECT account_id, account_type, team_leader_role, name, email, password_hash, is_active, auth_provider
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
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    return clean if clean["role_key"] else None


def change_account_password(account_id: int, current_password: str, new_password: str) -> None:
    ensure_system_tables()
    account = fetch_one(
        """
        SELECT account_id, name, password_hash
        FROM accounts
        WHERE account_id = %s
          AND account_type = 'reseller'
          AND is_active = true
        LIMIT 1;
        """,
        (account_id,),
    )
    if not account:
        raise ValueError("Reseller account was not found.")
    if not verify_password(current_password, account["password_hash"]):
        raise ValueError("Current password is incorrect.")
    cleaned_password = validate_password_policy(new_password)
    execute_write(
        "UPDATE accounts SET password_hash = %s WHERE account_id = %s;",
        (hash_password(cleaned_password), account_id),
    )
    add_log(account["name"], "changed_password", "Reseller profile")


def request_account_password_change(
    account_id: int,
    current_password: str,
    new_password: str,
    allowed_account_types: tuple[str, ...] = ("reseller", "team_leader"),
) -> dict:
    ensure_system_tables()
    account = fetch_one(
        """
        SELECT account_id, account_type, name, email, password_hash
        FROM accounts
        WHERE account_id = %s
          AND is_active = true
        LIMIT 1;
        """,
        (account_id,),
    )
    if not account or account["account_type"] not in allowed_account_types:
        raise ValueError("Account was not found.")
    if not verify_password(current_password, account["password_hash"]):
        raise ValueError("Current password is incorrect.")
    cleaned_password = validate_password_policy(new_password)

    otp_code = f"{secrets.randbelow(1_000_000):06d}"
    pending_password_hash = hash_password(cleaned_password)
    otp_hash = hash_password(otp_code)
    expires_at = datetime.now() + timedelta(minutes=10)

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE account_password_otps
            SET consumed_at = now()
            WHERE account_id = %s
              AND consumed_at IS NULL;
            """,
            (account_id,),
        )
        cur.execute(
            """
            INSERT INTO account_password_otps (account_id, otp_hash, pending_password_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING account_password_otp_id;
            """,
            (account_id, otp_hash, pending_password_hash, expires_at),
        )
        otp = cur.fetchone()

    profile_label = "Team leader profile" if account["account_type"] == "team_leader" else "Reseller profile"
    add_log(account["name"], "requested_password_otp", profile_label)
    return {
        "otp_id": otp["account_password_otp_id"],
        "account_id": account["account_id"],
        "account_type": account["account_type"],
        "name": account["name"],
        "email": account["email"],
        "otp_code": otp_code,
    }


def request_reseller_password_change(account_id: int, current_password: str, new_password: str) -> dict:
    return request_account_password_change(account_id, current_password, new_password, ("reseller",))


def cancel_reseller_password_change(account_id: int, otp_id: int | None = None) -> None:
    ensure_system_tables()
    params: list[object] = [account_id]
    where = "account_id = %s AND consumed_at IS NULL"
    if otp_id is not None:
        where += " AND account_password_otp_id = %s"
        params.append(otp_id)
    execute_write(f"UPDATE account_password_otps SET consumed_at = now() WHERE {where};", tuple(params))


def cancel_account_password_change(account_id: int, otp_id: int | None = None) -> None:
    cancel_reseller_password_change(account_id, otp_id)


def confirm_account_password_change(
    account_id: int,
    otp_code: str,
    allowed_account_types: tuple[str, ...] = ("reseller", "team_leader"),
) -> None:
    ensure_system_tables()
    cleaned_otp = "".join(ch for ch in otp_code.strip() if ch.isdigit())
    if len(cleaned_otp) != 6:
        raise ValueError("Enter the 6-digit OTP sent to your email.")

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT p.account_password_otp_id, p.otp_hash, p.pending_password_hash, a.name, a.account_type
            FROM account_password_otps p
            JOIN accounts a ON a.account_id = p.account_id
            WHERE p.account_id = %s
              AND p.consumed_at IS NULL
              AND p.expires_at >= now()
              AND a.is_active = true
            ORDER BY p.created_at DESC
            LIMIT 1
            FOR UPDATE OF p;
            """,
            (account_id,),
        )
        pending = cur.fetchone()
        if not pending:
            raise ValueError("OTP expired or no password change request is pending.")
        if pending["account_type"] not in allowed_account_types:
            raise ValueError("Account was not found.")
        if not verify_password(cleaned_otp, pending["otp_hash"]):
            raise ValueError("OTP is incorrect.")
        cur.execute(
            "UPDATE accounts SET password_hash = %s WHERE account_id = %s;",
            (pending["pending_password_hash"], account_id),
        )
        cur.execute(
            "UPDATE account_password_otps SET consumed_at = now() WHERE account_password_otp_id = %s;",
            (pending["account_password_otp_id"],),
        )

    profile_label = "Team leader profile OTP" if pending["account_type"] == "team_leader" else "Reseller profile OTP"
    add_log(pending["name"], "changed_password", profile_label)


def confirm_reseller_password_change(account_id: int, otp_code: str) -> None:
    confirm_account_password_change(account_id, otp_code, ("reseller",))


def request_login_otp(account_id: int) -> dict:
    ensure_system_tables()
    account = fetch_one(
        """
        SELECT account_id, account_type, team_leader_role, name, email, is_active
        FROM accounts
        WHERE account_id = %s
        LIMIT 1;
        """,
        (account_id,),
    )
    if not account or not account["is_active"]:
        raise ValueError("Account was not found.")

    otp_code = f"{secrets.randbelow(1_000_000):06d}"
    otp_hash = hash_password(otp_code)
    expires_at = datetime.now() + timedelta(minutes=10)

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE account_login_otps
            SET consumed_at = now()
            WHERE account_id = %s
              AND consumed_at IS NULL;
            """,
            (account_id,),
        )
        cur.execute(
            """
            INSERT INTO account_login_otps (account_id, otp_hash, expires_at)
            VALUES (%s, %s, %s)
            RETURNING account_login_otp_id;
            """,
            (account_id, otp_hash, expires_at),
        )
        otp = cur.fetchone()

    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"])
    return {
        "otp_id": otp["account_login_otp_id"],
        "otp_code": otp_code,
        "account": clean,
    }


def cancel_login_otp(account_id: int, otp_id: int | None = None) -> None:
    ensure_system_tables()
    params: list[object] = [account_id]
    where = "account_id = %s AND consumed_at IS NULL"
    if otp_id is not None:
        where += " AND account_login_otp_id = %s"
        params.append(otp_id)
    execute_write(f"UPDATE account_login_otps SET consumed_at = now() WHERE {where};", tuple(params))


def confirm_login_otp(account_id: int, otp_code: str) -> dict:
    ensure_system_tables()
    cleaned_otp = "".join(ch for ch in otp_code.strip() if ch.isdigit())
    if len(cleaned_otp) != 6:
        raise ValueError("Enter the 6-digit OTP sent to your email.")

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT p.account_login_otp_id, p.otp_hash,
                   a.account_id, a.account_type, a.team_leader_role, a.name, a.email, a.is_active
            FROM account_login_otps p
            JOIN accounts a ON a.account_id = p.account_id
            WHERE p.account_id = %s
              AND p.consumed_at IS NULL
              AND p.expires_at >= now()
              AND a.is_active = true
            ORDER BY p.created_at DESC
            LIMIT 1
            FOR UPDATE OF p;
            """,
            (account_id,),
        )
        pending = cur.fetchone()
        if not pending:
            raise ValueError("OTP expired or no login request is pending.")
        if not verify_password(cleaned_otp, pending["otp_hash"]):
            raise ValueError("OTP is incorrect.")
        cur.execute(
            "UPDATE account_login_otps SET consumed_at = now() WHERE account_login_otp_id = %s;",
            (pending["account_login_otp_id"],),
        )

    clean = clean_row(pending)
    clean["role_key"] = role_key_for_account_type(clean["account_type"])
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    return clean


def update_reseller_profile(
    account_id: int,
    name: str,
    business_name: str,
    contact_number: str,
    address: str,
) -> dict:
    ensure_system_tables()
    cleaned_name = " ".join(name.strip().split())
    cleaned_business = " ".join(business_name.strip().split())
    cleaned_contact = " ".join(contact_number.strip().split())
    cleaned_address = " ".join(address.strip().split())
    if not cleaned_name:
        raise ValueError("Name is required.")
    if not cleaned_business:
        raise ValueError("Business name is required.")
    if not cleaned_contact:
        raise ValueError("Contact number is required.")
    if not cleaned_address:
        raise ValueError("Address is required.")

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT a.account_id, a.name, a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
              AND a.is_active = true
            LIMIT 1
            FOR UPDATE;
            """,
            (account_id,),
        )
        account = cur.fetchone()
        if not account or account["reseller_id"] is None:
            raise ValueError("Reseller account was not found.")
        cur.execute(
            "UPDATE accounts SET name = %s WHERE account_id = %s;",
            (cleaned_name, account_id),
        )
        cur.execute(
            """
            UPDATE resellers
            SET business_name = %s,
                contact_person = %s,
                contact_number = %s,
                address = %s
            WHERE reseller_id = %s;
            """,
            (cleaned_business, cleaned_name, cleaned_contact, cleaned_address, account["reseller_id"]),
        )

    add_log(cleaned_name, "updated_profile", "Reseller profile")
    return reseller_account_profile(account_id)


def account_portal_profile(account_id: int) -> dict | None:
    ensure_system_tables()
    account = fetch_one("""
        SELECT account_id, account_type, team_leader_role, name, email, is_active
        FROM accounts
        WHERE account_id = %s
        LIMIT 1;
    """, (account_id,))
    if not account or not account["is_active"]:
        return None
    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"])
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    return clean


def social_login_account(
    auth_user_id: str,
    email: str,
    name: str,
    provider: str,
    allow_reseller_signup: bool = False,
) -> dict | None:
    ensure_system_tables()
    normalized_email = email.strip().lower()
    display_name = " ".join((name or normalized_email.split("@")[0]).split()) or normalized_email

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT account_id, account_type, team_leader_role, name, email, is_active, auth_provider
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
                RETURNING account_id, account_type, team_leader_role, name, email, is_active, auth_provider;
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
                RETURNING account_id, account_type, team_leader_role, name, email, is_active, auth_provider;
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


def inventory_product_movement_analytics(days: int = 30, limit: int = 8) -> list[dict]:
    clean_days = max(1, min(int(days), 365))
    clean_limit = max(1, min(int(limit), 20))
    return clean_row(fetch_all("""
        SELECT p.item_id AS product_id,
               p.name,
               p.category,
               p.unit,
               COALESCE(stock.total_available, 0) AS total_available,
               COALESCE(product_in.total_in, 0) AS total_in,
               COALESCE(product_out.total_out, 0) AS total_out
        FROM inventory_items p
        LEFT JOIN (
            SELECT item_id, SUM(quantity_available) AS total_available
            FROM inventory_batches
            WHERE quality_status = 'approved'
              AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)
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
    leader = next_sales_team_leader()
    leader_id = leader["account_id"] if leader else None
    
    inq = execute_write("""
        INSERT INTO inquiries (name, contact_number, email, business_name, message, status, assigned_team_leader_account_id)
        VALUES (%s, %s, %s, %s, %s, 'assigned', %s)
        RETURNING inquiry_id, name, contact_number, email, business_name, message, status, assigned_team_leader_account_id, created_at;
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


def due_inquiry_followups(limit: int = 20) -> list[dict]:
    ensure_system_tables()
    return clean_row(fetch_all(
        """
        SELECT inquiry_id, name, email, business_name, created_at
        FROM inquiries
        WHERE status = 'assigned'
          AND follow_up_sent_at IS NULL
          AND created_at <= now() - interval '1 day'
        ORDER BY created_at ASC
        LIMIT %s;
        """,
        (limit,),
    ))


def mark_inquiry_followup_sent(inquiry_id: int) -> None:
    ensure_system_tables()
    execute_write(
        """
        UPDATE inquiries
        SET follow_up_sent_at = now()
        WHERE inquiry_id = %s
          AND follow_up_sent_at IS NULL;
        """,
        (inquiry_id,),
    )


def generate_temporary_password() -> str:
    token = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
    return f"BP-{token[:10]}"


def add_reseller_from_inquiry(inquiry_id: int, approving_team_leader_account_id: int | None = None) -> dict | None:
    ensure_system_tables()
    temporary_password = generate_temporary_password()
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM inquiries WHERE inquiry_id = %s FOR UPDATE;", (inquiry_id,))
        inq = cur.fetchone()
        if not inq:
            return None
        if approving_team_leader_account_id is not None and inq["assigned_team_leader_account_id"] not in {None, approving_team_leader_account_id}:
            return None

        cur.execute(
            """
            SELECT account_id, account_type
            FROM accounts
            WHERE lower(email) = lower(%s)
            LIMIT 1;
            """,
            (inq["email"],),
        )
        existing_account = cur.fetchone()
        if existing_account:
            raise ValueError("That email is already used by an existing portal account. Use a different reseller email before approval.")

        leader_id = approving_team_leader_account_id or inq["assigned_team_leader_account_id"]
        if leader_id is None:
            cur.execute("""
                SELECT account_id
                FROM accounts
                WHERE account_type = 'team_leader'
                  AND is_active = true
                  AND team_leader_role = 'sales'
                ORDER BY account_id
                LIMIT 1;
            """)
            leader = cur.fetchone()
            leader_id = leader["account_id"] if leader else None
        if leader_id is None:
            raise ValueError("No active sales team leader is available for this reseller.")

        cur.execute(
            """
            SELECT reseller_id
            FROM resellers
            WHERE inquiry_id = %s
            LIMIT 1;
            """,
            (inquiry_id,),
        )
        if cur.fetchone():
            raise ValueError("This inquiry already has a reseller account.")

        cur.execute("""
            UPDATE inquiries
            SET status = 'approved', assigned_team_leader_account_id = %s, reviewed_by_account_id = %s, reviewed_at = %s
            WHERE inquiry_id = %s;
        """, (leader_id, leader_id, datetime.now(), inquiry_id))

        cur.execute("""
            INSERT INTO resellers (inquiry_id, business_name, contact_person, email, contact_number, address, reseller_status, team_leader_account_id, approved_by_account_id, approved_at)
            VALUES (%s, %s, %s, %s, %s, 'Pending onboarding details', 'active', %s, %s, %s)
            RETURNING reseller_id, business_name, contact_person, email, contact_number, address, reseller_status, team_leader_account_id, approved_by_account_id, created_at;
        """, (inquiry_id, inq["business_name"], inq["name"], inq["email"], inq["contact_number"], leader_id, leader_id, datetime.now()))
        res = cur.fetchone()

        cur.execute("""
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
            VALUES ('reseller', %s, %s, %s, %s, true)
            RETURNING account_id, email;
        """, (res["reseller_id"], res["business_name"], res["email"], hash_password(temporary_password)))
        account = cur.fetchone()

        cur.execute("SELECT name, email FROM accounts WHERE account_id = %s;", (leader_id,))
        leader = cur.fetchone()

    res = clean_row(res)

    add_log(leader["name"] if leader else "Sales team leader", "approved_reseller_inquiry", f"Inquiry #{inquiry_id}")
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
    res["account_id"] = account["account_id"] if account else None
    res["account_email"] = account["email"] if account else res["email"]
    res["temporary_password"] = temporary_password
    res["team_leader_name"] = leader["name"] if leader else "Assigned sales team leader"
    res["team_leader_email"] = leader["email"] if leader else ""
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
        proof = fetch_one(
            """
            SELECT 1
            FROM order_payment_proofs
            WHERE order_id = %s
            LIMIT 1;
            """,
            (order_id,),
        )
        if not proof:
            raise ValueError("Proof of payment is required before approving this order.")
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


def team_reseller_purchase_summary(team_leader_account_id: int | None = None) -> list[dict]:
    """Summarize actual reseller purchases from approved/fulfilled system orders."""
    ensure_system_tables()
    scope_sql = ""
    params: list[object] = []
    if team_leader_account_id is not None:
        scope_sql = "AND r.team_leader_account_id = %s"
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
          AND o.status IN ('approved', 'fulfilled')
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
               r.contact_person,
               r.contact_number,
               r.address,
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
                   r.contact_person,
                   r.contact_number,
                   r.address,
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
            row["contact_person"] = reseller["contact_person"]
            row["contact_number"] = reseller["contact_number"]
            row["address"] = reseller["address"]
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

def add_account(account_type: str, name: str, email: str, team_leader_role: str | None = None) -> dict:
    ensure_system_tables()
    reseller_id = None
    cleaned_name = " ".join(name.strip().split())
    cleaned_email = email.strip().lower()
    
    if account_type == "reseller":
        raise ValueError("Reseller accounts are created only by approving chatbot inquiries.")
    if account_type == "team_leader":
        team_leader_role = team_leader_role if team_leader_role in {"inventory", "sales"} else "sales"
    else:
        team_leader_role = None
    if fetch_one("SELECT account_id FROM accounts WHERE lower(email) = lower(%s) LIMIT 1;", (cleaned_email,)):
        raise ValueError("That email is already used by an existing portal account.")

    temporary_password = generate_temporary_password()
    account = execute_write("""
        INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, team_leader_role, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, true)
        RETURNING account_id, account_type, team_leader_role, name, email;
    """, (account_type, reseller_id, cleaned_name, cleaned_email, hash_password(temporary_password), team_leader_role), returning=True)
    add_log("Owner", "created_account", cleaned_email)
    clean = clean_row(account)
    clean["temporary_password"] = temporary_password
    clean["role_key"] = role_key_for_account_type(clean["account_type"])
    return clean

FORECAST_HISTORY_DAYS = 180
PROPHET_MIN_HISTORY_POINTS = 3
FORECAST_EVENT_PRIOR_SCALE = 8


def product_sales_history(product_ids: list[int], start_date: date, end_date: date) -> dict[int, list[dict]]:
    if not product_ids:
        return {}
    rows = clean_row(fetch_all("""
        SELECT oi.product_id,
               COALESCE(o.fulfilled_at::date, o.order_date) AS sale_date,
               COALESCE(SUM(oi.quantity), 0) AS quantity
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN inventory_items p ON p.item_id = oi.product_id
        WHERE o.order_type = 'reseller'
          AND o.status = 'fulfilled'
          AND p.item_type = 'finished_product'
          AND oi.product_id = ANY(%s)
          AND COALESCE(o.fulfilled_at::date, o.order_date) BETWEEN %s AND %s
        GROUP BY oi.product_id, COALESCE(o.fulfilled_at::date, o.order_date)
        ORDER BY oi.product_id, sale_date;
    """, (product_ids, start_date, end_date)))
    grouped: dict[int, list[dict]] = {product_id: [] for product_id in product_ids}
    for row in rows:
        grouped.setdefault(row["product_id"], []).append(row)
    return grouped


def forecast_business_events(start_date: date, end_date: date):
    import pandas as pd

    rows = []
    for year in range(start_date.year, end_date.year + 1):
        for month in range(1, 13):
            rows.append({
                "holiday": "payday_window",
                "ds": date(year, month, 15),
                "lower_window": -1,
                "upper_window": 1,
                "prior_scale": FORECAST_EVENT_PRIOR_SCALE,
            })
            rows.append({
                "holiday": "month_end_payday_window",
                "ds": date(year, month, calendar.monthrange(year, month)[1]),
                "lower_window": -1,
                "upper_window": 1,
                "prior_scale": FORECAST_EVENT_PRIOR_SCALE,
            })
        rows.extend([
            {
                "holiday": "christmas_rush",
                "ds": date(year, 12, 24),
                "lower_window": -8,
                "upper_window": 1,
                "prior_scale": FORECAST_EVENT_PRIOR_SCALE,
            },
            {
                "holiday": "new_year_rush",
                "ds": date(year, 12, 31),
                "lower_window": -2,
                "upper_window": 1,
                "prior_scale": FORECAST_EVENT_PRIOR_SCALE,
            },
            {
                "holiday": "batangas_sublian_foundation_season",
                "ds": date(year, 7, 23),
                "lower_window": -13,
                "upper_window": 0,
                "prior_scale": FORECAST_EVENT_PRIOR_SCALE,
            },
        ])
    frame = pd.DataFrame(rows)
    return frame[(frame["ds"] >= start_date) & (frame["ds"] <= end_date)]


def prophet_product_forecast(history_rows: list[dict], forecast_horizon_days: int) -> dict:
    os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
    logging.getLogger("prophet").setLevel(logging.ERROR)
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
    try:
        import pandas as pd
        from prophet import Prophet
    except Exception as exc:
        raise RuntimeError("Prophet is unavailable") from exc

    forecast_end = date.today() + timedelta(days=forecast_horizon_days)
    history_start = min(row["sale_date"] for row in history_rows)
    custom_holidays = forecast_business_events(history_start, forecast_end)
    frame = pd.DataFrame(
        {
            "ds": [row["sale_date"] for row in history_rows],
            "y": [float(row["quantity"]) for row in history_rows],
        }
    )
    model = Prophet(
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=False,
        holidays=custom_holidays,
        holidays_prior_scale=FORECAST_EVENT_PRIOR_SCALE,
    )
    model.add_country_holidays(country_name="PH")
    model.fit(frame)
    future = model.make_future_dataframe(periods=forecast_horizon_days, freq="D", include_history=False)
    forecast = model.predict(future).tail(1).iloc[0]
    predicted = max(0, round(float(forecast["yhat"]), 1))
    lower = max(0, round(float(forecast.get("yhat_lower", predicted)), 1))
    upper = max(lower, round(float(forecast.get("yhat_upper", predicted)), 1))
    return {
        "forecast_date": forecast["ds"].date(),
        "predicted_quantity": predicted,
        "confidence_lower": lower,
        "confidence_upper": upper,
        "method": "Prophet",
    }


def baseline_product_forecast(history_rows: list[dict], available: float, forecast_horizon_days: int) -> dict:
    forecast_date = date.today() + timedelta(days=forecast_horizon_days)
    quantities = [float(row["quantity"]) for row in history_rows if float(row["quantity"]) > 0]
    if quantities:
        predicted = round(sum(quantities) / len(quantities), 1)
    else:
        predicted = round(max(float(available) * 0.42, 1), 1)
    predicted = max(1, predicted)
    lower = max(0, round(predicted * 0.85, 1))
    upper = max(lower, round(predicted * 1.15, 1))
    return {
        "forecast_date": forecast_date,
        "predicted_quantity": predicted,
        "confidence_lower": lower,
        "confidence_upper": upper,
        "method": "Baseline fallback",
    }


def add_forecast(model_name: str, forecast_horizon_days: int) -> None:
    owner = fetch_one("SELECT account_id FROM accounts WHERE account_type = 'owner' LIMIT 1;")
    owner_id = owner["account_id"] if owner else None
    today_value = date.today()
    history_start = today_value - timedelta(days=FORECAST_HISTORY_DAYS)

    run = execute_write("""
        INSERT INTO forecast_runs (run_by_account_id, model_name, input_period_start, input_period_end, forecast_horizon_days, status, started_at, completed_at, notes)
        VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, %s)
        RETURNING forecast_run_id;
    """, (
        owner_id,
        model_name,
        history_start,
        today_value,
        forecast_horizon_days,
        datetime.now(),
        datetime.now(),
        f"Prophet demand forecast using {FORECAST_HISTORY_DAYS} days of fulfilled reseller order history, Philippine holidays, paydays, Christmas/New Year demand windows, and Batangas/Sublian season; baseline fallback used when history is insufficient.",
    ), returning=True)
    
    run_id = run["forecast_run_id"]
    
    products = fetch_all("""
        SELECT item_id AS product_id, name
        FROM inventory_items
        WHERE item_type = 'finished_product';
    """)
    product_ids = [product["product_id"] for product in products]
    histories = product_sales_history(product_ids, history_start, today_value)
    methods_used = set()

    for product in products:
        avail_res = fetch_one("""
            SELECT COALESCE(SUM(quantity_available), 0) AS val
            FROM inventory_batches
            WHERE item_id = %s
              AND quality_status = 'approved'
              AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE);
        """, (product["product_id"],))
        avail = float(avail_res["val"])
        history_rows = histories.get(product["product_id"], [])
        nonzero_points = [row for row in history_rows if float(row["quantity"]) > 0]
        try:
            if len(nonzero_points) >= PROPHET_MIN_HISTORY_POINTS:
                forecast = prophet_product_forecast(nonzero_points, forecast_horizon_days)
            else:
                forecast = baseline_product_forecast(history_rows, avail, forecast_horizon_days)
        except Exception:
            forecast = baseline_product_forecast(history_rows, avail, forecast_horizon_days)
        methods_used.add(forecast["method"])
        
        execute_write("""
            INSERT INTO forecast_results (forecast_run_id, product_id, forecast_date, predicted_quantity, confidence_lower, confidence_upper)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (
            run_id,
            product["product_id"],
            forecast["forecast_date"],
            forecast["predicted_quantity"],
            forecast["confidence_lower"],
            forecast["confidence_upper"],
        ))

    execute_write("""
        UPDATE forecast_runs
        SET notes = %s
        WHERE forecast_run_id = %s;
    """, (
        f"Completed with: {', '.join(sorted(methods_used))}. Prophet uses fulfilled reseller order quantities with Philippine holidays, payday windows, Christmas/New Year windows, and Batangas/Sublian season; baseline fallback covers products with fewer than {PROPHET_MIN_HISTORY_POINTS} selling days.",
        run_id,
    ))
        
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
