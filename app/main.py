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
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import repositories as data
from app.chatbot import ask_chatbot
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
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=APP_ENV == "production",
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
        "page": positive_int(request.query_params.get("page")),
    }


def date_filter(value: str | None, fallback: date) -> date:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return fallback


def month_bounds(value: date) -> tuple[date, date]:
    first = value.replace(day=1)
    if value.month == 12:
        next_month = value.replace(year=value.year + 1, month=1, day=1)
    else:
        next_month = value.replace(month=value.month + 1, day=1)
    return first, next_month - timedelta(days=1)


def require_calendar_month(period_start: date, period_end: date) -> None:
    month_start, month_end = month_bounds(period_start)
    if period_start != month_start or period_end != month_end:
        raise ValueError("Sales reports must cover one complete calendar month.")


def require_completed_report_month(period_start: date, period_end: date) -> None:
    require_calendar_month(period_start, period_end)
    if date.today() < period_end:
        raise ValueError("Sales reports can only be submitted on or after the last day of the month.")


def paged(items: list[dict], total: int, page: int, page_size: int = 10, **filters: str) -> dict:
    return {
        "items": items,
        "pagination": data.pagination_meta(total, page, page_size),
        "filters": filters,
    }


def product_page(request: Request, page_size: int = 12) -> dict:
    filters = portal_filters(request)
    items = data.list_products(q=filters["q"], category=filters["type"], page=filters["page"], page_size=page_size)
    total = data.count_products(q=filters["q"], category=filters["type"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"])


def orders_page(request: Request, order_type: str, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_orders(
        order_type=order_type,
        q=filters["q"],
        status=filters["status"],
        page=filters["page"],
        page_size=page_size,
    )
    total = data.count_orders(order_type=order_type, q=filters["q"], status=filters["status"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], status=filters["status"])


def reports_page(request: Request, report_source: str | None = None, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_sales_reports(report_source=report_source, q=filters["q"], page=filters["page"], page_size=page_size)
    total = data.count_sales_reports(report_source=report_source, q=filters["q"])
    return paged(items, total, filters["page"], page_size, q=filters["q"])


def inquiries_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_inquiries(q=filters["q"], status=filters["status"], page=filters["page"], page_size=page_size)
    total = data.count_inquiries(q=filters["q"], status=filters["status"])
    return paged(items, total, filters["page"], page_size, q=filters["q"], status=filters["status"])


def forecasts_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_forecasts(q=filters["q"], page=filters["page"], page_size=page_size)
    total = data.count_forecasts(q=filters["q"])
    return paged(items, total, filters["page"], page_size, q=filters["q"])


def accounts_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    account_type = filters["type"] if filters["type"] in {"owner", "team_leader", "reseller"} else ""
    items = data.list_accounts(q=filters["q"], account_type=account_type, page=filters["page"], page_size=page_size)
    total = data.count_accounts(q=filters["q"], account_type=account_type)
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=account_type)


def logs_page(request: Request, page_size: int = 10) -> dict:
    filters = portal_filters(request)
    items = data.list_activity_logs(q=filters["q"], page=filters["page"], page_size=page_size)
    total = data.count_activity_logs(q=filters["q"])
    return paged(items, total, filters["page"], page_size, q=filters["q"])


def session_account_id(request: Request) -> int | None:
    try:
        return int(request.session.get("account_id"))
    except (TypeError, ValueError):
        return None


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
    default_end = date.today()
    default_start = default_end - timedelta(days=29)
    start_date = date_filter(request.query_params.get("start_date"), default_start)
    end_date = date_filter(request.query_params.get("end_date"), default_end)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    status_filter = request.query_params.get("sales_status", "fulfilled").strip() or "fulfilled"
    if status_filter not in {"all", "pending", "approved", "fulfilled", "rejected", "cancelled"}:
        status_filter = "fulfilled"

    sales_series = data.reseller_sales_series(start_date, end_date, status_filter)
    max_sales = max((row["total_sales"] for row in sales_series), default=0)
    total_sales = sum(row["total_sales"] for row in sales_series)
    total_orders = sum(row["order_count"] for row in sales_series)
    for row in sales_series:
        row["bar_percent"] = 0 if max_sales <= 0 else max(6, round((row["total_sales"] / max_sales) * 100, 2))

    return {
        "metrics": data.current_metrics(),
        "most_bought_products": data.reseller_most_bought_products(limit=3),
        "sales_reports": data.list_sales_reports(report_source="reseller", limit=1),
        "sales_series": sales_series,
        "sales_chart_total": total_sales,
        "sales_chart_orders": total_orders,
        "sales_chart_filters": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "status": status_filter,
        },
    }


def reseller_reports_context(request: Request) -> dict:
    period_start, period_end = month_bounds(date.today())
    account_id = session_account_id(request)
    reportable_products = []
    month_orders = []
    if account_id is not None:
        reportable_products = data.reseller_reportable_products(account_id, period_start, period_end)
        month_orders = data.reseller_month_orders(account_id, period_start, period_end)
    return {
        "sales_reports": data.list_sales_reports(report_source="reseller"),
        "reportable_products": reportable_products,
        "monthly_orders": month_orders,
        "report_period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "report_submit_available": date.today() >= period_end,
        "report_locked_reason": "" if date.today() >= period_end else "Sales reports unlock on the last day of the month.",
    }


def _owner_dashboard_context(request: Request) -> dict:
    return {
        "metrics": data.current_metrics(),
        "products": data.list_products(),
        "alerts": data.list_alerts(),
        "forecasts": data.list_forecasts(limit=5),
    }


def _team_dashboard_context(request: Request) -> dict:
    return {
        "metrics": data.current_metrics(),
        "inquiries": data.list_inquiries(limit=4),
        "orders": data.list_orders(order_type="walk_in", limit=5),
    }


def _team_inventory_context(request: Request) -> dict:
    return {
        "products": data.list_products(),
        "inventory_items": data.list_inventory_items(),
        "inventory_batches": data.list_inventory_batches(),
        "raw_materials": data.list_raw_materials(),
        "product_recipes": data.list_product_recipes(),
        "alerts": data.list_alerts(),
        "raw_material_categories": data.raw_material_categories,
        "product_categories": data.product_categories,
        "stock_units": data.stock_units,
        "content_units": data.content_units,
        "recipe_units": data.recipe_units,
    }


PORTAL_SECTION_LOADERS = {
    ("owner", "dashboard"): _owner_dashboard_context,
    ("owner", "products"): lambda request: {"products_page": (page := product_page(request)), "products": page["items"]},
    ("owner", "reports"): lambda request: {"sales_reports_page": (page := reports_page(request)), "sales_reports": page["items"]},
    ("owner", "forecasts"): lambda request: {"forecasts_page": (page := forecasts_page(request)), "forecasts": page["items"]},
    ("owner", "accounts"): lambda request: {"accounts_page": (page := accounts_page(request)), "accounts": page["items"]},
    ("owner", "logs"): lambda request: {"activity_logs_page": (page := logs_page(request)), "activity_logs": page["items"]},
    ("team-leader", "dashboard"): _team_dashboard_context,
    ("team-leader", "sales"): lambda request: {
        "products": data.list_products(),
        "orders_page": (page := orders_page(request, "walk_in")),
        "orders": page["items"],
    },
    ("team-leader", "inventory"): _team_inventory_context,
    ("team-leader", "inquiries"): lambda request: {"inquiries_page": (page := inquiries_page(request)), "inquiries": page["items"]},
    ("team-leader", "orders"): lambda request: {"orders_page": (page := orders_page(request, "reseller")), "orders": page["items"]},
    ("team-leader", "reports"): lambda request: {
        "sales_reports_page": (page := reports_page(request, "team_leader")),
        "sales_reports": page["items"],
        "team_report_sales": data.team_sales_report_entries(),
        "team_rejected_orders": data.team_rejected_order_entries(),
    },
    ("reseller", "dashboard"): reseller_dashboard_context,
    ("reseller", "order"): lambda request: {"products": data.list_products()},
    ("reseller", "cart"): reseller_cart_context,
    ("reseller", "history"): lambda request: {"orders": data.list_orders(order_type="reseller")},
    ("reseller", "reports"): reseller_reports_context,
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


REPORT_ATTACHMENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
    "text/csv",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


async def parse_report_attachments(uploads: list[UploadFile]) -> list[dict]:
    attachments = []
    for upload in uploads:
        if not upload.filename:
            continue
        filename = Path(upload.filename).name
        content = await upload.read()
        if not content:
            continue
        if len(attachments) >= 3:
            raise ValueError("Attach up to 3 files only.")
        if len(content) > 5 * 1024 * 1024:
            raise ValueError(f"{filename} is larger than 5 MB.")
        content_type = upload.content_type or "application/octet-stream"
        if content_type not in REPORT_ATTACHMENT_TYPES:
            raise ValueError(f"{filename} must be an image, PDF, CSV, text, or XLSX file.")
        attachments.append({
            "filename": filename,
            "content_type": content_type,
            "content": content,
            "size_bytes": len(content),
            "checksum_sha256": hashlib.sha256(content).hexdigest(),
        })
    return attachments


def safe_portal_path(role_key: str, section: str, message: str = "", error: str = "") -> str:
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    if section not in {item[0] for item in data.portal_nav[role_key]}:
        raise HTTPException(status_code=404)
    query = ""
    if message:
        query = "?" + urlencode({"message": message})
    if error:
        query = "?" + urlencode({"error": error})
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
    data.add_log(account["name"], "login", data.roles[role]["label"])
    return redirect_to(safe_portal_path(role, data.roles[role]["default_section"]))


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
    if request.session.get("role_key") == role_key:
        return None
    return redirect_to(path_with_query("/login", error="Please sign in to access that portal."))


def public_products() -> list[dict]:
    try:
        return data.list_products()
    except Exception:
        return []


@app.get("/")
async def landing(request: Request, message: str = "", error: str = ""):
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
    try:
        if len(name.strip()) < 2:
            raise ValueError("Name is required.")
        if len(business_name.strip()) < 2:
            raise ValueError("Business name is required.")
        email = require_email(email)
        if len(contact_number.strip()) < 7:
            raise ValueError("Contact number is too short.")
        if len(message.strip()) < 10:
            raise ValueError("Please include a short message about your reseller request.")
        data.add_inquiry(name.strip(), business_name.strip(), email, contact_number.strip(), message.strip())
    except ValueError as exc:
        return redirect_to(path_with_query("/", error=str(exc)) + "#reseller-inquiry")
    return redirect_to(path_with_query("/", message="Inquiry submitted. A team leader will review it.") + "#reseller-inquiry")


@app.post("/api/chatbot")
async def chatbot_api(request: Request):
    payload = await request.json()
    message = str(payload.get("message", "")).strip()
    if len(message) < 2:
        return JSONResponse({"reply": "Please contact Batangas Premium directly for complete details."})
    reply = ask_chatbot(message)
    data.add_log("Website visitor", "used_chatbot", "Public support widget")
    return JSONResponse({"reply": reply})


@app.get("/login")
async def login(request: Request, message: str = "", error: str = ""):
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

    data.record_user_consent(account["account_id"], CONSENT_VERSION, "password")
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

    account = data.social_login_account(str(user.get("id")), email, account_name_from_user(user, email), provider)
    if account is None:
        request.session.clear()
        return redirect_to(path_with_query("/login", error="This account is inactive. Please contact the owner."))

    data.record_user_consent(account["account_id"], CONSENT_VERSION, f"oauth:{provider}", provider)
    return establish_portal_session(request, account)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    response = redirect_to(path_with_query("/login"))
    return response


@app.get("/portal/{role_key}")
async def portal_default(request: Request, role_key: str):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    return redirect_to(f"/portal/{role_key}/{data.roles[role_key]['default_section']}")


@app.get("/portal/{role_key}/{section}")
async def portal(request: Request, role_key: str, section: str, message: str = "", error: str = ""):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    nav_sections = {item[0]: item for item in data.portal_nav[role_key]}
    if section not in nav_sections:
        raise HTTPException(status_code=404)

    context = {
        "request": request,
        "role_key": role_key,
        "role": data.roles[role_key],
        "nav": data.portal_nav[role_key],
        "section": section,
        "section_title": nav_sections[section][1],
        "message": message,
        "error": error,
        "cart_count": reseller_cart_count(request) if role_key == "reseller" else 0,
        "notifications": data.list_notifications(role_key, request.session.get("account_id")),
        "unread_notifications": data.unread_notification_count(role_key, request.session.get("account_id")),
    }
    context.update(portal_section_context(role_key, section, request))

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
        data.create_order_from_items("reseller", items, notes.strip())
        data.clear_reseller_cart(account_id)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "cart", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "history", message="Order submitted for team leader approval."))


@app.post("/portal/reseller/reports")
async def reseller_report(
    request: Request,
    period_start: date = Form(...),
    period_end: date = Form(...),
    notes: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path("reseller", "reports", error="Your session expired. Please sign in again."))
    try:
        require_date_range(period_start, period_end)
        require_completed_report_month(period_start, period_end)
        form = await request.form()
        quantities = {
            int(key.removeprefix("sold_")): value
            for key, value in form.items()
            if key.startswith("sold_") and str(value).strip()
        }
        parsed_attachments = await parse_report_attachments(attachments)
        data.add_reseller_sell_through_report(account_id, period_start, period_end, quantities, notes.strip(), parsed_attachments)
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "reports", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "reports", message="Sell-through report submitted."))





@app.post("/portal/team-leader/sales")
async def team_walk_in_sale(request: Request, product_id: int = Form(...), quantity: float = Form(...), notes: str = Form("")):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    try:
        require_positive_number(quantity, "Quantity")
        data.create_order("team-leader", product_id, quantity, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "sales", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "sales", message="Walk-in sale recorded and inventory updated."))


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
    if decision == "approve":
        if data.add_reseller_from_inquiry(inquiry_id) is None:
            return redirect_to(safe_portal_path("team-leader", "inquiries", error="Inquiry not found."))
        return redirect_to(safe_portal_path("team-leader", "inquiries", message="Inquiry approved and reseller account staged."))
    if decision == "reject":
        if not data.reject_inquiry(inquiry_id):
            return redirect_to(safe_portal_path("team-leader", "inquiries", error="Inquiry not found."))
        return redirect_to(safe_portal_path("team-leader", "inquiries", message="Inquiry rejected."))
    raise HTTPException(status_code=404)


@app.post("/portal/team-leader/orders/{order_id}/{decision}")
async def team_order_decision(request: Request, order_id: int, decision: str):
    guard = require_portal_session(request, "team-leader")
    if guard:
        return guard
    if decision not in {"approve", "reject", "fulfill"}:
        raise HTTPException(status_code=404)
    if not data.decide_order(order_id, decision):
        return redirect_to(safe_portal_path("team-leader", "orders", error="Order not found or already finalized."))
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
    try:
        require_date_range(period_start, period_end)
        totals = data.team_sales_report_totals(period_start, period_end)
        data.add_sales_report(
            "team_leader",
            "Maria Santos",
            period_start,
            period_end,
            totals["total_sales"],
            totals["total_orders"],
            notes.strip(),
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
async def owner_account(request: Request, account_type: str = Form(...), name: str = Form(...), email: str = Form(...)):
    guard = require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        if account_type not in {"owner", "team_leader", "reseller"}:
            raise ValueError("Invalid account type.")
        if len(name.strip()) < 2:
            raise ValueError("Account name is required.")
        email = require_email(email)
        data.add_account(account_type, name, email)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "accounts", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "accounts", message="Account created."))


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
