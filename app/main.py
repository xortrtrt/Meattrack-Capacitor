from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import repositories as data
from app.chatbot import ask_chatbot
from app.config import APP_ENV, SESSION_SECRET_KEY


BASE_DIR = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MEDIA_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

app = FastAPI(title="MEATTRACK", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=APP_ENV == "production",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

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
    return f"/media/{quote(filename)}"


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


@app.get("/media/{filename}")
async def media_asset(filename: str):
    if not MEDIA_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404)
    asset = data.media_asset_by_filename(filename)
    if asset is None:
        raise HTTPException(status_code=404)
    return Response(
        content=asset["content"],
        media_type=asset["content_type"],
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Length": str(asset["size_bytes"]),
        },
    )


def require_portal_session(request: Request, role_key: str) -> RedirectResponse | None:
    if request.session.get("role_key") == role_key:
        return None
    return redirect_to(path_with_query("/login", error="Please sign in to access that portal."))


@app.get("/")
async def landing(request: Request, message: str = "", error: str = ""):
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "message": message,
            "error": error,
            "products": data.products,
            "metrics": data.current_metrics(),
        },
    )


@app.get("/products")
async def products(request: Request):
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "request": request,
            "products": data.products,
        },
    )


@app.get("/about")
async def about(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        {
            "request": request,
        },
    )


@app.get("/partnerships")
async def partnerships(request: Request):
    return templates.TemplateResponse(
        request,
        "partnerships.html",
        {
            "request": request,
        },
    )


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
        },
    )


@app.post("/login")
async def submit_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        email = require_email(email)
        if len(password.strip()) < 4:
            raise ValueError("Password must be at least 4 characters.")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))

    account = data.authenticate_account(email, password)
    if account is None:
        return redirect_to(path_with_query("/login", error="Invalid email or password."))

    role = account["role_key"]
    response = redirect_to(safe_portal_path(role, data.roles[role]["default_section"]))
    request.session.clear()
    request.session["account_id"] = account["account_id"]
    request.session["role_key"] = role
    request.session["account_name"] = account["name"]
    data.add_log(account["name"], "login", data.roles[role]["label"])
    return response


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

    products = data.products
    selected_product = products[0] if products else None
    if products and request.query_params.get("product_id"):
        selected_product_id = int(request.query_params["product_id"])
        selected_product = data.product_by_id(selected_product_id) or products[0]

    return templates.TemplateResponse(
        request,
        PORTAL_TEMPLATES[role_key],
        {
            "request": request,
            "role_key": role_key,
            "role": data.roles[role_key],
            "nav": data.portal_nav[role_key],
            "section": section,
            "section_title": nav_sections[section][1],
            "message": message,
            "error": error,
            "metrics": data.current_metrics(),
            "departments": data.departments,
            "resellers": data.resellers,
            "products": products,
            "selected_product": selected_product,
            "inventory_items": data.inventory_items,
            "inventory_batches": data.inventory_batches,
            "product_batches": data.product_batches,
            "raw_materials": data.raw_materials,
            "raw_material_categories": data.raw_material_categories,
            "product_categories": data.product_categories,
            "stock_units": data.stock_units,
            "content_units": data.content_units,
            "recipe_units": data.recipe_units,
            "product_recipes": data.product_recipes,
            "inquiries": data.inquiries,
            "orders": data.orders,
            "sales_reports": data.sales_reports,
            "team_report_sales": data.team_sales_report_entries() if role_key == "team-leader" and section == "reports" else [],
            "team_rejected_orders": data.team_rejected_order_entries() if role_key == "team-leader" and section == "reports" else [],
            "alerts": data.alerts,
            "forecasts": data.forecasts,
            "accounts": data.accounts,
            "activity_logs": data.activity_logs,
            "today": data.today,
        },
    )


@app.post("/portal/reseller/order")
async def reseller_order(request: Request, product_id: int = Form(...), quantity: float = Form(...), notes: str = Form("")):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    try:
        require_positive_number(quantity, "Quantity")
        data.create_order("reseller", product_id, quantity, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "order", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "history", message="Order submitted for team leader approval."))


@app.post("/portal/reseller/reports")
async def reseller_report(
    request: Request,
    period_start: date = Form(...),
    period_end: date = Form(...),
    total_sales: float = Form(...),
    total_orders: int = Form(...),
    notes: str = Form(""),
):
    guard = require_portal_session(request, "reseller")
    if guard:
        return guard
    try:
        require_date_range(period_start, period_end)
        require_nonnegative_number(total_sales, "Total sales")
        if total_orders < 0:
            raise ValueError("Total orders cannot be negative.")
        data.add_sales_report("reseller", "Lipa Fresh Mart", period_start, period_end, total_sales, total_orders, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "reports", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "reports", message="Sales report submitted."))





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
