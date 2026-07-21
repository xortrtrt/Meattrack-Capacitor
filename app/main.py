from __future__ import annotations

import re
import base64
import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import repositories as data
from app.chatbot import process_chatbot_message
from app.emailer import (
    send_inquiry_status_update,
    send_login_otp,
    send_password_change_otp,
    send_portal_credentials,
    send_reseller_credentials,
)
from app.config import APP_ENV, CONSENT_VERSION, MEDIA_BASE_URL, SESSION_SECRET_KEY, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL


BASE_DIR = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MEDIA_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

class CachedStaticFiles(StaticFiles):
    """Give versioned local dependencies a long-lived browser cache."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        normalized_path = path.lstrip("/\\").replace("\\", "/")
        if response.status_code == 200 and normalized_path.startswith(("fonts/", "vendor/")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app = FastAPI(title="MEATTRACK", version="0.1.0")
PUBLIC_SESSION_SECONDS = 24 * 60 * 60
PORTAL_SESSION_SECONDS = 2 * 60 * 60
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=APP_ENV == "production",
    max_age=PUBLIC_SESSION_SECONDS,
)
app.mount("/static", CachedStaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

PRODUCT_IMAGE_FILENAMES = {
    "bacon smoked": "bacon_smoked.jpg",
    "beef longganisa": "beef_longganisa.jpg",
    "beef tapa ala eh": "beef_tapa_ala_eh.jpg",
    "cheesy overload sausage": "cheesy_overload_sausage.jpg",
    "chicken rebusado": "chicken_rebusado.jpg",
    "chicken tocino": "chicken_tocino.jpg",
    "deli beef": "deli_beef.jpg",
    "hamon ala eh": "hamon_ala_eh.jpg",
    "hungarian sausage": "hungarian_sausage.jpg",
    "pork garlic longganisa": "pork_garlic_longganisa.jpg",
    "pork rebusado": "pork_rebusado.jpg",
    "pork tapa": "pork_tapa.jpg",
    "spicy garlic longganisa": "spicy_garlic_longganisa.jpg",
    "tocino ala eh": "tocino_ala_eh.jpg",
    "trial package": "trial_package.jpg",
    "resellers package": "reseller_package.jpg",
    "area distributors package": "area_distributor_package.jpg",
    "triple garlic longganisa": "triple_garlic_longganisa.jpg",
}

PRODUCT_IMAGE_KEYWORDS = [
    ("bacon", "bacon_smoked.jpg"),
    ("tocino", "tocino_ala_eh.jpg"),
    ("longganisa", "pork_garlic_longganisa.jpg"),
    ("sausage", "hungarian_sausage.jpg"),
    ("tapa", "beef_tapa_ala_eh.jpg"),
    ("rebusado", "pork_rebusado.jpg"),
    ("ham", "hamon_ala_eh.jpg"),
    ("beef", "deli_beef.jpg"),
    ("chicken", "chicken_tocino.jpg"),
    ("pork", "pork_tapa.jpg"),
]


def currency(value: float | int) -> str:
    return f"PHP {value:,.2f}"


def number(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def nice_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y %I:%M %p")
    if isinstance(value, date):
        return value.strftime("%b %d, %Y")
    return str(value)


def product_image(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    if key in PRODUCT_IMAGE_FILENAMES:
        return PRODUCT_IMAGE_FILENAMES[key]
    for keyword, filename in PRODUCT_IMAGE_KEYWORDS:
        if keyword in key:
            return filename
    return "hero-meat-counter.png"


def media_url(filename: str) -> str:
    encoded = quote(filename, safe="")
    if MEDIA_BASE_URL:
        return f"{MEDIA_BASE_URL}/{encoded}"
    return f"/static/img/{encoded}"


templates.env.filters["currency"] = currency
templates.env.filters["number"] = number
templates.env.filters["nice_date"] = nice_date
templates.env.filters["product_image"] = product_image
templates.env.filters["media_url"] = media_url

PORTAL_TEMPLATES = {
    "owner": "portals/owner.html",
    "team-leader": "portals/team_leader.html",
    "reseller": "portals/reseller.html",
}


def positive_int(value: str | None, default: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        return default
    return max(1, min(parsed, maximum))


def portal_filters(request: Request) -> dict:
    return {
        "q": request.query_params.get("q", "").strip(),
        "status": request.query_params.get("status", "").strip(),
        "type": request.query_params.get("type", "").strip(),
        "sort": request.query_params.get("sort", "").strip(),
        "page": positive_int(request.query_params.get("page")),
    }


def date_filter(value: str | None, fallback: date) -> date:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return fallback


def paged(items: list[dict], total: int, page: int, page_size: int = 10, **filters: str) -> dict:
    return {
        "items": items,
        "pagination": data.pagination_meta(total, page, page_size),
        "filters": filters,
    }


def product_page(request: Request, page_size: int = 12) -> dict:
    filters = portal_filters(request)
    items = data.list_products(q=filters["q"], category=filters["type"], page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_products(q=filters["q"], category=filters["type"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def orders_page(
    request: Request,
    order_type: str,
    page_size: int = 10,
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> dict:
    filters = portal_filters(request)
    items = data.list_orders(
        order_type=order_type,
        q=filters["q"],
        status=filters["status"],
        team_leader_account_id=team_leader_account_id,
        reseller_account_id=reseller_account_id,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    total = data.count_orders(
        order_type=order_type,
        q=filters["q"],
        status=filters["status"],
        team_leader_account_id=team_leader_account_id,
        reseller_account_id=reseller_account_id,
    )
    return paged(items, total, filters["page"], page_size, q=filters["q"], status=filters["status"], sort=filters["sort"])


def reports_page(
    request: Request,
    report_source: str | None = None,
    page_size: int = 10,
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> dict:
    filters = portal_filters(request)
    items = data.list_sales_reports(
        report_source=report_source,
        q=filters["q"],
        team_leader_account_id=team_leader_account_id,
        reseller_account_id=reseller_account_id,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    total = data.count_sales_reports(
        report_source=report_source,
        q=filters["q"],
        team_leader_account_id=team_leader_account_id,
        reseller_account_id=reseller_account_id,
    )
    return paged(items, total, filters["page"], page_size, q=filters["q"], sort=filters["sort"])


def inquiries_page(request: Request, page_size: int = 10, assigned_team_leader_account_id: int | None = None) -> dict:
    filters = portal_filters(request)
    items = data.list_inquiries(
        q=filters["q"],
        status=filters["status"],
        assigned_team_leader_account_id=assigned_team_leader_account_id,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    total = data.count_inquiries(
        q=filters["q"],
        status=filters["status"],
        assigned_team_leader_account_id=assigned_team_leader_account_id,
    )
    return paged(items, total, filters["page"], page_size, q=filters["q"], status=filters["status"], sort=filters["sort"])


def forecasts_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_forecasts(q=filters["q"], page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_forecasts(q=filters["q"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], sort=filters["sort"])


def accounts_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    account_type = filters["type"] if filters["type"] in {"owner", "team_leader", "reseller"} else ""
    items = data.list_accounts(q=filters["q"], account_type=account_type, page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_accounts(q=filters["q"], account_type=account_type)
    page = paged(items, total, filters["page"], page_size, q=filters["q"], type=account_type, sort=filters["sort"])
    page["team_leaders"] = data.list_team_leader_accounts()
    page["reseller_assignments"] = data.list_reseller_assignments()
    return page


def logs_page(request: Request, page_size: int = 10, inventory_only: bool = False) -> dict:
    filters = portal_filters(request)
    items = data.list_activity_logs(q=filters["q"], page=filters["page"], page_size=page_size, inventory_only=inventory_only, sort=filters["sort"])
    total = data.count_activity_logs(q=filters["q"], inventory_only=inventory_only)
    return paged(items, total, filters["page"], page_size, q=filters["q"], sort=filters["sort"])


def inventory_items_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_inventory_items(q=filters["q"], category=filters["type"], page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_items(q=filters["q"], category=filters["type"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def finished_inventory_products_page(request: Request, page_size: int = 8) -> dict:
    filters = portal_filters(request)
    category = f"finished_product:{filters['type']}" if filters["type"] else "finished_product"
    items = data.list_inventory_items(q=filters["q"], category=category, page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_items(q=filters["q"], category=category)
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def raw_materials_inventory_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    category = f"raw_material:{filters['type']}" if filters["type"] else "raw_material"
    items = data.list_inventory_items(q=filters["q"], category=category, page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_items(q=filters["q"], category=category)
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def raw_materials_inventory_context(request: Request) -> dict:
    raw_materials_page = raw_materials_inventory_page(request)
    return {
        "raw_materials_page": raw_materials_page,
        "raw_items": raw_materials_page["items"],
        "raw_material_tabs": [
            {"label": "All raw materials", "value": ""},
            *[
                {"label": category.replace("_", " ").title(), "value": category}
                for category in data.raw_material_categories
            ],
        ],
    }


def finished_products_inventory_context(request: Request) -> dict:
    products_page = finished_inventory_products_page(request)
    return {
        "inventory_items_page": products_page,
        "inventory_products_page": products_page,
        "finished_products": products_page["items"],
        "inventory_item_tabs": [
            {"label": "All products", "value": ""},
            *[
                {"label": category, "value": category}
                for category in data.product_categories
            ],
        ],
    }


def inventory_batches_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_inventory_batches(q=filters["q"], category=filters["type"], page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_batches(q=filters["q"], category=filters["type"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def session_account_id(request: Request) -> int | None:
    try:
        return int(request.session.get("account_id"))
    except (TypeError, ValueError):
        return None


def session_team_leader_role(request: Request) -> str | None:
    value = request.session.get("team_leader_role")
    return value if value in {"inventory", "sales"} else None


def portal_nav_for_request(role_key: str, request: Request) -> list[tuple[str, str, str]]:
    return data.portal_nav_for(role_key, session_team_leader_role(request))


def portal_role_for_request(role_key: str, request: Request) -> dict:
    role = dict(data.roles[role_key])
    account_name = request.session.get("account_name")
    account_email = request.session.get("account_email")
    if account_name:
        role["name"] = account_name
    if account_email:
        role["email"] = account_email
    if role_key == "team-leader":
        leader_role = session_team_leader_role(request) or "sales"
        role["label"] = data.team_leader_role_labels.get(leader_role, role["label"])
        role["team_leader_role"] = leader_role
    return role


def team_leader_section_allowed(request: Request, section: str) -> bool:
    return section in {item[0] for item in portal_nav_for_request("team-leader", request)}


def require_team_leader_role(request: Request, required_role: str) -> RedirectResponse | None:
    if session_team_leader_role(request) == required_role:
        return None
    return redirect_to(safe_portal_path("team-leader", "dashboard", error="Your team leader account cannot access that action."))


def reseller_cart_count(request: Request) -> float:
    account_id = session_account_id(request)
    if account_id is None:
        return 0
    return data.reseller_cart_count(account_id)


def reseller_cart_context(request: Request) -> dict:
    products = data.list_products()
    account_id = session_account_id(request)
    items = data.list_reseller_cart_items(account_id) if account_id is not None else []
    total = sum((Decimal(str(item.get("line_total") or 0)) for item in items), Decimal("0.00"))
    return {
        "products": products,
        "cart_items": items,
        "cart_total": total.quantize(Decimal("0.01")),
        "cart_count": sum(item["cart_quantity"] for item in items),
    }


def reseller_cart_payload(account_id: int, message: str = "") -> JSONResponse:
    items = data.list_reseller_cart_items(account_id)
    total = sum((Decimal(str(item.get("line_total") or 0)) for item in items), Decimal("0.00"))
    count = sum(float(item["cart_quantity"]) for item in items)
    return JSONResponse({
        "ok": True,
        "message": message,
        "cart_total": float(total.quantize(Decimal("0.01"))),
        "cart_count": count,
        "line_totals": {
            str(item["product_id"]): float(Decimal(str(item.get("line_total") or 0)).quantize(Decimal("0.01")))
            for item in items
        },
    })


def reseller_dashboard_context(request: Request) -> dict:
    account_id = session_account_id(request)
    reseller_profile = None
    if account_id is not None:
        try:
            reseller_profile = data.reseller_account_profile(account_id)
        except ValueError:
            reseller_profile = None
    default_end = date.today()
    default_start = default_end - timedelta(days=29)
    start_date = date_filter(request.query_params.get("start_date"), default_start)
    end_date = date_filter(request.query_params.get("end_date"), default_end)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    status_filter = request.query_params.get("sales_status", "fulfilled").strip() or "fulfilled"
    if status_filter not in {"all", "pending", "approved", "fulfilled", "rejected", "cancelled"}:
        status_filter = "fulfilled"

    sales_series = data.reseller_sales_series(start_date, end_date, status_filter, account_id=account_id)
    max_sales = max((row["total_sales"] for row in sales_series), default=0)
    total_sales = sum(row["total_sales"] for row in sales_series)
    total_orders = sum(row["order_count"] for row in sales_series)
    for row in sales_series:
        row["bar_percent"] = 0 if max_sales <= 0 else max(6, round((row["total_sales"] / max_sales) * 100, 2))

    return {
        "metrics": data.current_metrics(reseller_account_id=account_id),
        "reseller_profile": reseller_profile,
        "most_bought_products": data.reseller_most_bought_products(limit=3, account_id=account_id),
        "sales_series": sales_series,
        "sales_chart_total": total_sales,
        "sales_chart_orders": total_orders,
        "sales_chart_filters": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "status": status_filter,
        },
    }


def reseller_profile_context(request: Request) -> dict:
    account_id = session_account_id(request)
    account = data.account_portal_profile(account_id) if account_id is not None else None
    reseller_profile = None
    if account_id is not None:
        try:
            reseller_profile = data.reseller_account_profile(account_id)
        except ValueError:
            reseller_profile = None
    return {
        "account_profile": account,
        "reseller_profile": reseller_profile,
        "password_otp_pending": request.query_params.get("otp") == "1",
    }


def team_leader_profile_context(request: Request) -> dict:
    account_id = session_account_id(request)
    account = data.account_portal_profile(account_id) if account_id is not None else None
    return {
        "account_profile": account,
        "password_otp_pending": request.query_params.get("otp") == "1",
    }


OWNER_SALES_PERIOD_OPTIONS = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
]


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def owner_sales_period_points(granularity: str, today_value: date) -> tuple[date, list[dict]]:
    if granularity == "weekly":
        end_week = today_value - timedelta(days=today_value.weekday())
        start = end_week - timedelta(weeks=11)
        points = [
            {
                "key": week_start.isoformat(),
                "label": week_start.strftime("%b %d"),
            }
            for week_start in (start + timedelta(weeks=offset) for offset in range(12))
        ]
        return start, points

    if granularity == "monthly":
        start = add_months(today_value.replace(day=1), -11)
        points = [
            {
                "key": month_start.strftime("%Y-%m"),
                "label": month_start.strftime("%b %Y"),
            }
            for month_start in (add_months(start, offset) for offset in range(12))
        ]
        return start, points

    if granularity == "quarterly":
        current_quarter_month = ((today_value.month - 1) // 3) * 3 + 1
        current_quarter = date(today_value.year, current_quarter_month, 1)
        start = add_months(current_quarter, -21)
        points = []
        for offset in range(8):
            quarter_start = add_months(start, offset * 3)
            quarter = ((quarter_start.month - 1) // 3) + 1
            points.append({
                "key": f"{quarter_start.year}-Q{quarter}",
                "label": f"Q{quarter} {quarter_start.year}",
            })
        return start, points

    if granularity == "yearly":
        start = date(today_value.year - 4, 1, 1)
        points = [
            {
                "key": str(year),
                "label": str(year),
            }
            for year in range(start.year, today_value.year + 1)
        ]
        return start, points

    start = today_value - timedelta(days=30)
    points = [
        {
            "key": current_day.isoformat(),
            "label": current_day.strftime("%b %d"),
        }
        for current_day in (start + timedelta(days=offset) for offset in range((today_value - start).days + 1))
    ]
    return start, points


def owner_sales_bucket_key(value: object, granularity: str) -> str:
    if hasattr(value, "date"):
        value = value.date()
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if not isinstance(value, date):
        raise ValueError("sale_date must be a date")

    if granularity == "weekly":
        return (value - timedelta(days=value.weekday())).isoformat()
    if granularity == "monthly":
        return value.strftime("%Y-%m")
    if granularity == "quarterly":
        quarter = ((value.month - 1) // 3) + 1
        return f"{value.year}-Q{quarter}"
    if granularity == "yearly":
        return str(value.year)
    return value.isoformat()


def owner_sales_chart_context(request: Request | None = None) -> dict:
    today_value = date.today()
    requested_period = request.query_params.get("sales_period", "daily") if request is not None else "daily"
    sales_period = requested_period if requested_period in {key for key, _ in OWNER_SALES_PERIOD_OPTIONS} else "daily"
    sales_start, sales_points = owner_sales_period_points(sales_period, today_value)
    sales_series = data.reseller_sales_series(sales_start, today_value, "fulfilled")
    sales_chart_total = sum(row["total_sales"] for row in sales_series)
    sales_chart_orders = sum(row["order_count"] for row in sales_series)
    sales_by_bucket = {point["key"]: {"sales": 0, "orders": 0} for point in sales_points}
    for row in sales_series:
        bucket_key = owner_sales_bucket_key(row["sale_date"], sales_period)
        if bucket_key in sales_by_bucket:
            sales_by_bucket[bucket_key]["sales"] += row["total_sales"]
            sales_by_bucket[bucket_key]["orders"] += row["order_count"]
    sales_chart_rows = [
        {
            "label": point["label"],
            "sales": sales_by_bucket[point["key"]]["sales"],
            "orders": sales_by_bucket[point["key"]]["orders"],
        }
        for point in sales_points
    ]
    return {
        "owner_sales_chart": sales_chart_rows,
        "owner_sales_chart_total": sales_chart_total,
        "owner_sales_chart_orders": sales_chart_orders,
        "owner_sales_period": sales_period,
        "owner_sales_period_options": OWNER_SALES_PERIOD_OPTIONS,
        "owner_sales_chart_range": {
            "start": sales_start.isoformat(),
            "end": today_value.isoformat(),
        },
    }


def _owner_dashboard_context(request: Request) -> dict:
    chart_context = owner_sales_chart_context(request)
    context = {
        "metrics": data.current_metrics(),
        "products": data.list_products(page=1, page_size=8),
        "forecasts": data.list_forecasts(limit=6),
        "top_products": data.reseller_most_bought_products(limit=5),
    }
    context.update(chart_context)
    return context


def _team_dashboard_context(request: Request) -> dict:
    account_id = session_account_id(request)
    leader_role = session_team_leader_role(request) or "sales"
    if leader_role == "inventory":
        return {
            "team_leader_role": leader_role,
            "metrics": data.current_metrics(team_leader_account_id=account_id),
            "products": data.list_products(),
            "movement_analytics": data.inventory_product_movement_analytics(days=30, limit=8),
        }
    return {
        "team_leader_role": leader_role,
        "metrics": data.current_metrics(team_leader_account_id=account_id),
        "inquiries": data.list_inquiries(limit=4, assigned_team_leader_account_id=account_id),
        "orders": data.list_orders(order_type="reseller", limit=5, team_leader_account_id=account_id),
    }


def _team_inventory_context(request: Request) -> dict:
    recipe_products_page = product_page(request, page_size=8)
    recipe_product_ids = [product["product_id"] for product in recipe_products_page["items"]]
    return {
        "products": data.list_products(),
        "recipe_products_page": recipe_products_page,
        "recipe_products": recipe_products_page["items"],
        "raw_materials": data.list_raw_materials(),
        "product_recipes": data.list_product_recipes(product_ids=recipe_product_ids),
        "raw_material_categories": data.raw_material_categories,
        "product_categories": data.product_categories,
        "stock_units": data.stock_units,
        "content_units": data.content_units,
        "recipe_units": data.recipe_units,
    }


PORTAL_SECTION_LOADERS = {
    ("owner", "dashboard"): _owner_dashboard_context,
    ("owner", "products"): lambda request: {"products_page": (page := product_page(request)), "products": page["items"]},
    ("owner", "reports"): lambda request: {
        "sales_reports_page": (page := reports_page(request, "team_leader")),
        "sales_reports": page["items"],
    },
    ("owner", "forecasts"): lambda request: {
        "forecasts_page": (page := forecasts_page(request)),
        "forecasts": page["items"],
        "latest_forecast_run": data.latest_forecast_run(),
    },
    ("owner", "accounts"): lambda request: {
        "accounts_page": (page := accounts_page(request)),
        "accounts": page["items"],
        "team_leaders": page["team_leaders"],
        "reseller_assignments": page["reseller_assignments"],
    },
    ("team-leader", "dashboard"): _team_dashboard_context,
    ("team-leader", "sales"): lambda request: {
        "products": data.list_products(),
        "orders_page": (page := orders_page(request, "walk_in", team_leader_account_id=session_account_id(request))),
        "orders": page["items"],
    },
    ("team-leader", "inventory"): _team_inventory_context,
    ("team-leader", "raw-materials"): raw_materials_inventory_context,
    ("team-leader", "finished-products"): finished_products_inventory_context,
    ("team-leader", "batches"): lambda request: {
        "inventory_batches_page": (page := inventory_batches_page(request)),
        "inventory_batches": page["items"],
        "product_categories": data.product_categories,
    },
    ("team-leader", "logs"): lambda request: {
        "activity_logs_page": (page := logs_page(request, inventory_only=session_team_leader_role(request) == "inventory")),
        "activity_logs": page["items"],
    },
    ("team-leader", "inquiries"): lambda request: {
        "inquiries_page": (page := inquiries_page(request, assigned_team_leader_account_id=session_account_id(request))),
        "inquiries": page["items"],
    },
    ("team-leader", "orders"): lambda request: {
        "orders_page": (page := orders_page(request, "reseller", team_leader_account_id=session_account_id(request))),
        "orders": page["items"],
    },
    ("team-leader", "reports"): lambda request: {
        "sales_reports_page": (page := reports_page(request, "team_leader", team_leader_account_id=session_account_id(request))),
        "sales_reports": page["items"],
        "reseller_purchase_summary": data.team_reseller_purchase_summary(team_leader_account_id=session_account_id(request)),
        "team_report_sales": data.team_sales_report_entries(team_leader_account_id=session_account_id(request)),
        "team_rejected_orders": data.team_rejected_order_entries(team_leader_account_id=session_account_id(request)),
    },
    ("team-leader", "profile"): team_leader_profile_context,
    ("reseller", "dashboard"): reseller_dashboard_context,
    ("reseller", "order"): lambda request: {
        "products_page": (page := product_page(request, page_size=8)),
        "products": page["items"],
        "product_categories": data.product_categories,
    },
    ("reseller", "cart"): reseller_cart_context,
    ("reseller", "history"): lambda request: {
        "orders_page": (page := orders_page(request, "reseller", reseller_account_id=session_account_id(request))),
        "orders": page["items"],
    },
    ("reseller", "profile"): reseller_profile_context,
}


def portal_section_context(role_key: str, section: str, request: Request) -> dict:
    context = PORTAL_SECTION_LOADERS[(role_key, section)](request)
    products = context.get("products", [])
    if products:
        selected_product = products[0]
        requested_product_id = request.query_params.get("product_id")
        if requested_product_id:
            try:
                product_id = int(requested_product_id)
            except ValueError:
                product_id = None
            selected_product = next(
                (product for product in products if product["product_id"] == product_id),
                selected_product,
            )
        context["selected_product"] = selected_product
    return context


@app.get("/health", include_in_schema=False)
async def health():
    """Deployment and mobile connectivity probe without exposing secrets."""
    try:
        result = data.database_health()
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "unavailable"}, status_code=503)
    return {"status": "ok", "database": result}


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def require_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    return email


def require_positive_number(value: float, label: str) -> float:
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def require_nonnegative_number(value: float, label: str) -> float:
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


def require_date_range(period_start: date, period_end: date) -> None:
    if period_end < period_start:
        raise ValueError("Period end cannot be earlier than period start.")


PAYMENT_PROOF_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def parse_payment_proof_attachments(uploads: list[UploadFile]) -> list[dict]:
    attachments = []
    for upload in uploads:
        if not upload.filename:
            continue
        filename = Path(upload.filename).name
        content = await upload.read()
        if not content:
            continue
        if len(attachments) >= 3:
            raise ValueError("Attach up to 3 payment screenshots only.")
        if len(content) > 5 * 1024 * 1024:
            raise ValueError(f"{filename} is larger than 5 MB.")
        content_type = upload.content_type or "application/octet-stream"
        if content_type not in PAYMENT_PROOF_TYPES:
            raise ValueError(f"{filename} must be a JPG, PNG, or WebP screenshot.")
        attachments.append({
            "filename": filename,
            "content_type": content_type,
            "content": content,
            "size_bytes": len(content),
            "checksum_sha256": hashlib.sha256(content).hexdigest(),
        })
    return attachments


def safe_portal_path(role_key: str, section: str, message: str = "", error: str = "", **extra_params: str) -> str:
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    if role_key == "team-leader":
        allowed_sections = {item[0] for nav in data.team_leader_nav_by_role.values() for item in nav}
    else:
        allowed_sections = {item[0] for item in data.portal_nav[role_key]}
    if section not in allowed_sections:
        raise HTTPException(status_code=404)
    params = {key: value for key, value in extra_params.items() if value}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    query = "?" + urlencode(params) if params else ""
    return f"/portal/{role_key}/{section}{query}"


def path_with_query(path: str, **params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return path
    return path + "?" + urlencode(clean)


def supabase_auth_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def json_request(url: str, payload: dict, access_token: str = "") -> dict:
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Content-Type": "application/json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = UrlRequest(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=20) as response:
        if response.status < 200 or response.status >= 300:
            raise HTTPException(status_code=502, detail="Supabase Auth rejected the request.")
        return json.loads(response.read().decode("utf-8"))


def get_supabase_user(access_token: str) -> dict:
    request = UrlRequest(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        if response.status < 200 or response.status >= 300:
            raise HTTPException(status_code=502, detail="Supabase Auth user lookup failed.")
        return json.loads(response.read().decode("utf-8"))


def account_name_from_user(user: dict, email: str) -> str:
    metadata = user.get("user_metadata") or {}
    return (
        metadata.get("full_name")
        or metadata.get("name")
        or metadata.get("display_name")
        or email.split("@")[0]
    )


def establish_portal_session(request: Request, account: dict) -> RedirectResponse:
    role = account["role_key"]
    request.session.clear()
    request.session["account_id"] = account["account_id"]
    request.session["role_key"] = role
    request.session["account_name"] = account["name"]
    request.session["account_email"] = account["email"]
    request.session["portal_expires_at"] = int(datetime.now().timestamp()) + PORTAL_SESSION_SECONDS
    if role == "team-leader":
        request.session["team_leader_role"] = account.get("team_leader_role") or "sales"
    data.add_log(account["name"], "login", data.roles[role]["label"])
    return redirect_to(safe_portal_path(role, data.default_section_for(role, account.get("team_leader_role"))))


def begin_login_otp(request: Request, account: dict, consent_source: str = "password_otp") -> RedirectResponse:
    pending = data.request_login_otp(account["account_id"])
    sent, email_message = send_login_otp(
        to_email=account["email"],
        name=account["name"],
        otp_code=pending["otp_code"],
    )
    if not sent:
        data.cancel_login_otp(account["account_id"], pending.get("otp_id"))
        raise ValueError(email_message)
    request.session.clear()
    request.session["pending_login_account_id"] = account["account_id"]
    request.session["pending_login_email"] = account["email"]
    request.session["pending_login_consent_version"] = CONSENT_VERSION
    request.session["pending_login_consent_source"] = consent_source
    return redirect_to(path_with_query("/login", message="OTP sent to your account email. Enter it below to finish signing in."))


@app.get("/media/{filename}")
async def media_asset(filename: str):
    if not MEDIA_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404)
    return RedirectResponse(
        media_url(filename),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def require_portal_session(request: Request, role_key: str) -> RedirectResponse | None:
    expires_at = request.session.get("portal_expires_at")
    try:
        expired = int(expires_at) <= int(datetime.now().timestamp())
    except (TypeError, ValueError):
        expired = True
    if request.session.get("role_key") == role_key and not expired:
        return None
    if request.session.get("role_key"):
        request.session.clear()
    return redirect_to(path_with_query("/login", error="Please sign in to access that portal."))


def public_products() -> list[dict]:
    try:
        return data.list_products()
    except Exception:
        return []


def dispatch_due_inquiry_followups() -> None:
    try:
        inquiries = data.due_inquiry_followups(limit=20)
    except Exception:
        return
    for inquiry in inquiries:
        sent, _message = send_inquiry_status_update(
            to_email=inquiry["email"],
            name=inquiry["name"],
            business_name=inquiry["business_name"],
        )
        if sent:
            try:
                data.mark_inquiry_followup_sent(int(inquiry["inquiry_id"]))
            except Exception:
                continue


@app.get("/")
async def landing(request: Request, message: str = "", error: str = ""):
    dispatch_due_inquiry_followups()
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "message": message,
            "error": error,
            "products": public_products(),
        },
    )


@app.get("/products")
async def products(request: Request):
    return RedirectResponse("/#store", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/about")
async def about(request: Request):
    return RedirectResponse("/#about", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/partnerships")
async def partnerships(request: Request):
    return RedirectResponse("/#partnerships", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.post("/inquiries")
async def create_public_inquiry(
    name: str = Form(...),
    business_name: str = Form(...),
    email: str = Form(...),
    contact_number: str = Form(...),
    message: str = Form(...),
):
    return redirect_to(
        path_with_query(
            "/",
            error="Public sign-up is disabled. Please use the chatbot so a sales team leader can qualify your reseller inquiry.",
        )
        + "#inquiry"
    )


@app.post("/api/chatbot")
async def chatbot_api(request: Request):
    dispatch_due_inquiry_followups()
    payload = await request.json()
    message = str(payload.get("message", "")).strip()
    if len(message) < 2:
        return JSONResponse({"reply": "Please contact Batangas Premium directly for complete details."})
    result = process_chatbot_message(message, request.session.get("chatbot_state") or {})
    lead_created = False
    assigned_team_leader = None
    if result.get("action") == "create_lead":
        lead = result.get("lead") or {}
        inquiry = data.add_inquiry(
            str(lead.get("name", "")).strip(),
            str(lead.get("business_name", "")).strip(),
            require_email(str(lead.get("email", "")).strip()),
            str(lead.get("contact_number", "")).strip(),
            "\n".join(
                [
                    f"Location: {lead.get('location', '')}",
                    f"Interest: {lead.get('interest', '')}",
                    "Source: chatbot lead capture",
                ]
            ),
        )
        lead_created = True
        request.session["chatbot_state"] = {}
        assigned_id = inquiry.get("assigned_team_leader_account_id")
        if assigned_id:
            assigned = data.account_portal_profile(int(assigned_id))
            assigned_team_leader = assigned.get("name") if assigned else None
        reply = result["reply"]
        if assigned_team_leader:
            reply += f" Your assigned sales team leader is {assigned_team_leader}."
    else:
        request.session["chatbot_state"] = result.get("state") or {}
        reply = result["reply"]
    data.add_log("Website visitor", "used_chatbot", "Public support widget")
    return JSONResponse({"reply": reply, "lead_created": lead_created, "assigned_team_leader": assigned_team_leader})


@app.get("/login")
async def login(request: Request, message: str = "", error: str = ""):
    pending_login_account_id = request.session.get("pending_login_account_id")
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "roles": data.roles,
            "message": message,
            "error": error,
            "social_auth_enabled": supabase_auth_ready(),
            "consent_version": CONSENT_VERSION,
            "login_otp_pending": bool(pending_login_account_id),
            "pending_login_email": request.session.get("pending_login_email", ""),
        },
    )


@app.get("/privacy")
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"request": request, "consent_version": CONSENT_VERSION})


@app.get("/terms")
async def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html", {"request": request, "consent_version": CONSENT_VERSION})


@app.post("/login")
async def submit_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    consent: str = Form(""),
):
    try:
        email = require_email(email)
        if consent != "yes":
            raise ValueError("Please accept the privacy notice and terms to continue.")
        if len(password.strip()) < 4:
            raise ValueError("Password must be at least 4 characters.")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))

    account = data.authenticate_account(email, password)
    if account is None:
        return redirect_to(path_with_query("/login", error="Invalid email or password."))

    try:
        return begin_login_otp(request, account, "password_otp")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))


@app.post("/login/otp")
async def submit_login_otp(request: Request, otp_code: str = Form(...)):
    account_id = request.session.get("pending_login_account_id")
    if not account_id:
        return redirect_to(path_with_query("/login", error="Login OTP session expired. Please sign in again."))
    try:
        account = data.confirm_login_otp(int(account_id), otp_code)
        data.record_user_consent(
            account["account_id"],
            request.session.get("pending_login_consent_version") or CONSENT_VERSION,
            request.session.get("pending_login_consent_source") or "password_otp",
        )
    except (TypeError, ValueError) as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))
    return establish_portal_session(request, account)


@app.post("/auth/oauth/{provider}")
async def start_oauth(request: Request, provider: str, consent: str = Form("")):
    if provider not in {"google", "facebook"}:
        raise HTTPException(status_code=404)
    if consent != "yes":
        return redirect_to(path_with_query("/login", error="Please accept the privacy notice and terms to continue."))
    if not supabase_auth_ready():
        return redirect_to(path_with_query("/login", error="Social login is not configured yet."))

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    request.session["oauth_provider"] = provider

    redirect_url = str(request.url_for("auth_callback"))
    authorize_url = f"{SUPABASE_URL}/auth/v1/authorize?" + urlencode(
        {
            "provider": provider,
            "redirect_to": redirect_url,
            "code_challenge": challenge,
            "code_challenge_method": "s256",
            "state": state,
        }
    )
    return RedirectResponse(authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return redirect_to(path_with_query("/login", error=error_description or "Social login was cancelled."))
    if not code:
        return redirect_to(path_with_query("/login", error="Social login did not return an authorization code."))
    if state != request.session.get("oauth_state"):
        request.session.clear()
        return redirect_to(path_with_query("/login", error="Social login session expired. Please try again."))

    verifier = request.session.get("oauth_verifier")
    provider = request.session.get("oauth_provider", "social")
    if not verifier or not supabase_auth_ready():
        request.session.clear()
        return redirect_to(path_with_query("/login", error="Social login session expired. Please try again."))

    try:
        token = json_request(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=pkce",
            {"auth_code": code, "code_verifier": verifier},
        )
        user = token.get("user") or get_supabase_user(token["access_token"])
    except (HTTPError, URLError, KeyError, TimeoutError, HTTPException):
        request.session.clear()
        return redirect_to(path_with_query("/login", error="Social login could not be completed."))

    email = (user.get("email") or "").strip().lower()
    if not email:
        request.session.clear()
        return redirect_to(path_with_query("/login", error="Your social account did not provide an email address."))

    account = data.social_login_account(
        str(user.get("id")),
        email,
        account_name_from_user(user, email),
        provider,
        allow_reseller_signup=False,
    )
    if account is None:
        request.session.clear()
        return redirect_to(path_with_query("/login", error="No portal account exists for this social login. Please use the credentials provided by Batangas Premium."))

    try:
        return begin_login_otp(request, account, f"oauth_otp:{provider}")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    response = redirect_to(path_with_query("/login"))
    return response


@app.get("/portal/order-payment-proofs/{proof_id}")
async def portal_order_payment_proof(request: Request, proof_id: int):
    role_key = request.session.get("role_key")
    account_id = session_account_id(request)
    if role_key not in {"owner", "team-leader", "reseller"} or account_id is None:
        raise HTTPException(status_code=404)
    proof = data.get_order_payment_proof(proof_id, account_id, role_key)
    if not proof:
        raise HTTPException(status_code=404)
    return Response(
        content=proof["content"],
        media_type=proof["content_type"],
        headers={
            "Content-Disposition": f"inline; filename=\"{proof['filename']}\"",
            "Cache-Control": "private, max-age=300",
        },
    )


@app.get("/portal/{role_key}")
async def portal_default(request: Request, role_key: str):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    return redirect_to(f"/portal/{role_key}/{data.default_section_for(role_key, session_team_leader_role(request))}")


@app.get("/portal/owner/dashboard/sales-chart")
async def owner_dashboard_sales_chart(request: Request):
    guard = require_portal_session(request, "owner")
    if guard:
        return guard
    chart_context = owner_sales_chart_context(request)
    return JSONResponse(
        {
            "period": chart_context["owner_sales_period"],
            "range": chart_context["owner_sales_chart_range"],
            "total": chart_context["owner_sales_chart_total"],
            "orders": chart_context["owner_sales_chart_orders"],
            "rows": chart_context["owner_sales_chart"],
        }
    )


@app.get("/portal/team-leader/inventory-items")
async def team_inventory_items_legacy_redirect(request: Request):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    query = request.url.query
    suffix = f"?{query}" if query else ""
    return redirect_to(f"/portal/team-leader/raw-materials{suffix}")


@app.get("/portal/team-leader/inventory-items/products")
async def team_inventory_products_legacy_partial_redirect(request: Request):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    query = request.url.query
    suffix = f"?{query}" if query else ""
    return redirect_to(f"/portal/team-leader/finished-products/products{suffix}")


@app.get("/portal/team-leader/finished-products/products")
async def team_inventory_products_partial(request: Request):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    products_page = finished_inventory_products_page(request)
    return templates.TemplateResponse(
        request,
        "portals/team-leader/_inventory_products.html",
        {
            "request": request,
            "inventory_items_page": products_page,
            "inventory_products_page": products_page,
            "finished_products": products_page["items"],
            "inventory_item_tabs": [
                {"label": "All products", "value": ""},
                *[
                    {"label": category, "value": category}
                    for category in data.product_categories
                ],
            ],
        },
    )


@app.get("/portal/{role_key}/{section}")
async def portal(request: Request, role_key: str, section: str, message: str = "", error: str = ""):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    nav = portal_nav_for_request(role_key, request)
    nav_sections = {item[0]: item for item in nav}
    if section not in nav_sections:
        raise HTTPException(status_code=404)

    context = {
        "request": request,
        "role_key": role_key,
        "role": portal_role_for_request(role_key, request),
        "nav": nav,
        "section": section,
        "section_title": nav_sections[section][1],
        "message": message,
        "error": error,
        "cart_count": reseller_cart_count(request) if role_key == "reseller" else 0,
        "notifications": data.list_notifications(role_key, request.session.get("account_id")),
        "unread_notifications": data.unread_notification_count(role_key, request.session.get("account_id")),
    }
    context.update(portal_section_context(role_key, section, request))
    if (role_key == "team-leader" and section == "inquiries") or (role_key == "owner" and section == "accounts"):
        context["credential_flash"] = request.session.pop("credential_flash", None)

    return templates.TemplateResponse(
        request,
        PORTAL_TEMPLATES[role_key],
        context,
    )


@app.post("/portal/{role_key}/notifications/read")
async def portal_notifications_read(request: Request, role_key: str):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    data.mark_notifications_read(role_key, request.session.get("account_id"))
    section = request.query_params.get("section") or data.roles[role_key]["default_section"]
    return redirect_to(safe_portal_path(role_key, section))


@app.post("/portal/reseller/order")
async def reseller_order(request: Request, product_id: int = Form(...), quantity: float = Form(...), notes: str = Form("")):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "order", error="Your session expired. Please sign in again."))
    try:
        require_positive_number(quantity, "Quantity")
        data.add_reseller_cart_item(account_id, product_id, quantity)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "order", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "order", message="Product added to cart."))


@app.post("/portal/reseller/cart")
async def reseller_cart_update(
    request: Request,
    product_id: int = Form(...),
    quantity: float = Form(0),
    action: str = Form("update"),
):
    wants_json = request.headers.get("x-requested-with") == "fetch" or "application/json" in request.headers.get("accept", "")
    guard = require_portal_session(request, "reseller")
    if guard:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
        return redirect_to(safe_portal_path("reseller", "cart", error="Your session expired. Please sign in again."))
    if action == "remove" or quantity <= 0:
        data.remove_reseller_cart_item(account_id, product_id)
        if wants_json:
            return reseller_cart_payload(account_id, "Product removed from cart.")
        return redirect_to(safe_portal_path("reseller", "cart", message="Product removed from cart."))
    try:
        require_positive_number(quantity, "Quantity")
        data.update_reseller_cart_item(account_id, product_id, quantity)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return redirect_to(safe_portal_path("reseller", "cart", error=str(exc)))
    if wants_json:
        return reseller_cart_payload(account_id, "Cart updated.")
    return redirect_to(safe_portal_path("reseller", "cart", message="Cart updated."))


@app.post("/portal/reseller/cart/checkout")
async def reseller_cart_checkout(request: Request, notes: str = Form("")):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "cart", error="Your session expired. Please sign in again."))
    cart_items = data.list_reseller_cart_items(account_id)
    if not cart_items:
        return redirect_to(safe_portal_path("reseller", "cart", error="Your cart is empty."))
    try:
        items = [(item["product_id"], item["cart_quantity"]) for item in cart_items]
        data.create_order_from_items("reseller", items, notes.strip(), account_id=account_id)
        data.clear_reseller_cart(account_id)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "cart", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "history", message="Order submitted for team leader approval."))


@app.post("/portal/reseller/orders/{order_id}/payment-proof")
async def reseller_order_payment_proof(
    request: Request,
    order_id: int,
    payment_proofs: list[UploadFile] = File(default=[]),
):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "history", error="Your session expired. Please sign in again."))
    try:
        attachments = await parse_payment_proof_attachments(payment_proofs)
        count = data.add_order_payment_proofs(account_id, order_id, attachments)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "history", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "history", message=f"{count} payment proof screenshot uploaded."))


@app.post("/portal/reseller/profile/password")
async def reseller_profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "profile", error="Your session expired. Please sign in again."))
    try:
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
        pending = data.request_reseller_password_change(account_id, current_password, new_password)
        sent, email_message = send_password_change_otp(
            to_email=pending["email"],
            name=pending["name"],
            otp_code=pending["otp_code"],
        )
        if not sent:
            data.cancel_reseller_password_change(account_id, pending.get("otp_id"))
            raise ValueError(email_message)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "profile", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "profile", otp="1", message="OTP sent to your account email. Enter it below to confirm your password change."))


@app.post("/portal/reseller/profile/password/confirm")
async def reseller_profile_password_confirm(request: Request, otp_code: str = Form(...)):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "profile", error="Your session expired. Please sign in again."))
    try:
        data.confirm_reseller_password_change(account_id, otp_code)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "profile", otp="1", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "profile", message="Password updated."))


@app.post("/portal/team-leader/profile/password")
async def team_leader_profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("team-leader", "profile", error="Your session expired. Please sign in again."))
    try:
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
        pending = data.request_account_password_change(account_id, current_password, new_password, ("team_leader",))
        sent, email_message = send_password_change_otp(
            to_email=pending["email"],
            name=pending["name"],
            otp_code=pending["otp_code"],
        )
        if not sent:
            data.cancel_account_password_change(account_id, pending.get("otp_id"))
            raise ValueError(email_message)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "profile", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "profile", otp="1", message="OTP sent to your account email. Enter it below to confirm your password change."))


@app.post("/portal/team-leader/profile/password/confirm")
async def team_leader_profile_password_confirm(request: Request, otp_code: str = Form(...)):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("team-leader", "profile", error="Your session expired. Please sign in again."))
    try:
        data.confirm_account_password_change(account_id, otp_code, ("team_leader",))
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "profile", otp="1", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "profile", message="Password updated."))


@app.post("/portal/reseller/profile")
async def reseller_profile_update(
    request: Request,
    name: str = Form(...),
    business_name: str = Form(...),
    contact_number: str = Form(...),
    address: str = Form(...),
):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "profile", error="Your session expired. Please sign in again."))
    try:
        data.update_reseller_profile(account_id, name, business_name, contact_number, address)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "profile", error=str(exc)))
    request.session["account_name"] = " ".join(name.strip().split())
    return redirect_to(safe_portal_path("reseller", "profile", message="Profile updated."))


@app.post("/portal/team-leader/sales")
async def team_walk_in_sale(request: Request, product_id: int = Form(...), quantity: float = Form(...), notes: str = Form("")):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    return redirect_to(safe_portal_path("team-leader", "dashboard", error="Walk-in sales are disabled for team leader demo accounts."))


@app.post("/portal/team-leader/inventory-items")
async def team_inventory_item(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    unit: str = Form(...),
    quantity: float = Form(...),
):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        require_positive_number(quantity, "Quantity")
        data.add_raw_inventory_item(name, category, unit, quantity)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "inventory", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "inventory", message="Raw inventory updated."))


@app.post("/portal/team-leader/inventory-items/quantity")
async def team_inventory_item_quantity(
    request: Request,
    raw_material_id: int = Form(...),
    quantity: float = Form(...),
):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        require_positive_number(quantity, "Quantity")
        data.add_raw_inventory_quantity(raw_material_id, quantity)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "inventory", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "inventory", message="Raw inventory quantity added."))


@app.post("/portal/team-leader/products")
async def team_product(request: Request):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard

    form = await request.form()
    material_item_ids = [value for value in form.getlist("material_item_id[]") if str(value).strip()]
    quantity_required = [value for value in form.getlist("quantity_required[]") if str(value).strip()]
    quantity_required_units = [value for value in form.getlist("quantity_required_unit[]") if str(value).strip()]

    try:
        data.create_product_with_recipe(
            str(form.get("name", "")),
            str(form.get("category", "")),
            form.get("base_price", "0"),
            material_item_ids,
            quantity_required,
            quantity_required_units,
            form.get("pack_size", ""),
            str(form.get("pack_size_unit", "")),
        )
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "inventory", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "inventory", message="Product recipe created."))


@app.post("/portal/team-leader/production")
async def team_production(
    request: Request,
    product_id: int = Form(...),
    batch_code: str = Form(...),
    quantity: float = Form(...),
    expiry_date: date = Form(...),
):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        if len(batch_code.strip()) < 4:
            raise ValueError("Batch code is too short.")
        require_positive_number(quantity, "Quantity")
        if quantity != int(quantity):
            raise ValueError("Produced quantity must be a whole number of packs.")
        if expiry_date < date.today():
            raise ValueError("Expiry date cannot be in the past.")
        data.produce_product(product_id, batch_code.strip().upper(), quantity, expiry_date)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "inventory", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "inventory", message="Product produced and raw materials deducted."))


@app.post("/portal/team-leader/inquiries/{inquiry_id}/{decision}")
async def team_inquiry_decision(request: Request, inquiry_id: int, decision: str):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "sales")
    if role_guard:
        return role_guard
    if decision == "approve":
        try:
            reseller = data.add_reseller_from_inquiry(inquiry_id, approving_team_leader_account_id=session_account_id(request))
            if reseller is None:
                return redirect_to(safe_portal_path("team-leader", "inquiries", error="Inquiry not found."))
        except ValueError as exc:
            return redirect_to(safe_portal_path("team-leader", "inquiries", error=str(exc)))
        sent, email_message = send_reseller_credentials(
            to_email=reseller["account_email"],
            business_name=reseller["business_name"],
            temporary_password=reseller["temporary_password"],
            team_leader_name=reseller.get("team_leader_name") or request.session.get("account_name") or "Sales team leader",
        )
        if not sent:
            request.session["credential_flash"] = {
                "email": reseller["account_email"],
                "temporary_password": reseller["temporary_password"],
                "business_name": reseller["business_name"],
                "reason": email_message,
            }
            return redirect_to(safe_portal_path("team-leader", "inquiries", message="Inquiry approved. Email was not sent, so show the credentials below once."))
        return redirect_to(safe_portal_path("team-leader", "inquiries", message="Inquiry approved and reseller credentials were emailed."))
    if decision == "reject":
        if not data.reject_inquiry(inquiry_id, reviewing_team_leader_account_id=session_account_id(request)):
            return redirect_to(safe_portal_path("team-leader", "inquiries", error="Inquiry not found."))
        return redirect_to(safe_portal_path("team-leader", "inquiries", message="Inquiry rejected."))
    raise HTTPException(status_code=404)


@app.post("/portal/team-leader/orders/{order_id}/{decision}")
async def team_order_decision(request: Request, order_id: int, decision: str):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "sales")
    if role_guard:
        return role_guard
    if decision not in {"approve", "reject", "fulfill"}:
        raise HTTPException(status_code=404)
    try:
        if not data.decide_order(order_id, decision, team_leader_account_id=session_account_id(request)):
            return redirect_to(safe_portal_path("team-leader", "orders", error="Order not found or already finalized."))
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "orders", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "orders", message=f"Order {decision} action recorded."))


@app.post("/portal/team-leader/reports")
async def team_report(
    request: Request,
    period_start: date = Form(...),
    period_end: date = Form(...),
    notes: str = Form(""),
):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = require_team_leader_role(request, "sales")
    if role_guard:
        return role_guard
    try:
        require_date_range(period_start, period_end)
        account_id = session_account_id(request)
        totals = data.team_sales_report_totals(period_start, period_end, team_leader_account_id=account_id)
        data.add_sales_report(
            "team_leader",
            request.session.get("account_name") or "Team leader",
            period_start,
            period_end,
            totals["total_sales"],
            totals["total_orders"],
            notes.strip(),
            account_id=account_id,
        )
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "reports", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "reports", message="Team leader report submitted with current fulfilled sales totals."))


@app.post("/portal/owner/products")
async def owner_product(request: Request, product_id: int = Form(...), base_price: float = Form(...)):
    guard = require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        require_nonnegative_number(base_price, "Base price")
        product = data.product_by_id(product_id)
        if product is None:
            raise ValueError("Unknown product.")
        data.update_product_price(product_id, base_price)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "products", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "products", message="Product pricing updated."))





@app.post("/portal/owner/accounts")
async def owner_account(
    request: Request,
    account_type: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    team_leader_role: str = Form("sales"),
):
    guard = require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        if account_type not in {"owner", "team_leader"}:
            raise ValueError("Invalid account type.")
        if len(name.strip()) < 2:
            raise ValueError("Account name is required.")
        email = require_email(email)
        account = data.add_account(account_type, name, email, team_leader_role=team_leader_role)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "accounts", error=str(exc)))
    account_label = "team leader" if account_type == "team_leader" else "owner"
    sent, email_message = send_portal_credentials(
        to_email=account["email"],
        name=account["name"],
        temporary_password=account["temporary_password"],
        account_label=account_label,
    )
    if not sent:
        request.session["credential_flash"] = {
            "email": account["email"],
            "temporary_password": account["temporary_password"],
            "business_name": account["name"],
            "reason": email_message,
        }
        return redirect_to(safe_portal_path("owner", "accounts", message="Account created. Email was not sent, so show the credentials below once."))
    return redirect_to(safe_portal_path("owner", "accounts", message="Account created and credentials were emailed."))


@app.post("/portal/owner/resellers/{reseller_id}/team-leader")
async def owner_reseller_team_leader(request: Request, reseller_id: int, team_leader_account_id: int = Form(...)):
    guard = require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        data.set_reseller_team_leader(reseller_id, team_leader_account_id)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "accounts", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "accounts", message="Reseller team leader updated."))


@app.post("/portal/owner/forecasts")
async def owner_forecast(request: Request, model_name: str = Form(...), forecast_horizon_days: int = Form(...)):
    guard = require_portal_session(request, "owner")
    if guard:
        return guard
    if len(model_name.strip()) < 3:
        return redirect_to(safe_portal_path("owner", "forecasts", error="Model name is required."))
    if forecast_horizon_days <= 0:
        return redirect_to(safe_portal_path("owner", "forecasts", error="Forecast horizon must be greater than zero."))
    try:
        data.add_forecast(model_name, forecast_horizon_days)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "forecasts", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "forecasts", message="Forecast run completed."))
