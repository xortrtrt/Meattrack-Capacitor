from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import demo_data as data
from app.chatbot import ask_chatbot


BASE_DIR = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(title="MEATTRACK", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


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


templates.env.filters["currency"] = currency
templates.env.filters["number"] = number
templates.env.filters["nice_date"] = nice_date


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


def role_from_email(email: str) -> str | None:
    account = next((item for item in data.accounts if item["email"].lower() == email.lower()), None)
    if account is None:
        return None
    account_type = account["account_type"]
    if account_type == "team_leader":
        return "team-leader"
    if account_type in {"owner", "reseller"}:
        return account_type
    return None


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
async def submit_login(email: str = Form(...), password: str = Form(...)):
    try:
        email = require_email(email)
        if len(password.strip()) < 4:
            raise ValueError("Password must be at least 4 characters for the demo login.")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))

    role = role_from_email(email)
    if role is None:
        return redirect_to(path_with_query("/login", error="Account not found. Use a registered Batangas Premium portal email."))

    response = redirect_to(safe_portal_path(role, data.roles[role]["default_section"], message=f"Signed in as {data.roles[role]['label']}."))
    response.set_cookie("meattrack_role", role, httponly=True, samesite="lax")
    data.add_log(data.roles[role]["name"], "demo_login", data.roles[role]["label"])
    return response


@app.get("/logout")
async def logout():
    response = redirect_to(path_with_query("/login", message="You have been signed out."))
    response.delete_cookie("meattrack_role")
    return response


@app.get("/portal/{role_key}")
async def portal_default(role_key: str):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    return redirect_to(f"/portal/{role_key}/{data.roles[role_key]['default_section']}")


@app.get("/portal/{role_key}/{section}")
async def portal(request: Request, role_key: str, section: str, message: str = "", error: str = ""):
    if role_key not in data.roles:
        raise HTTPException(status_code=404)
    nav_sections = {item[0]: item for item in data.portal_nav[role_key]}
    if section not in nav_sections:
        raise HTTPException(status_code=404)

    selected_product_id = int(request.query_params.get("product_id", data.products[0]["product_id"]))
    selected_product = data.product_by_id(selected_product_id) or data.products[0]

    return templates.TemplateResponse(
        request,
        "portal.html",
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
            "employees": data.employees,
            "resellers": data.resellers,
            "products": data.products,
            "selected_product": selected_product,
            "product_batches": data.product_batches,
            "inquiries": data.inquiries,
            "inquiry_messages": data.inquiry_messages,
            "orders": data.orders,
            "sales_reports": data.sales_reports,
            "alerts": data.alerts,
            "tasks": data.tasks,
            "attendance": data.attendance,
            "evaluations": data.evaluations,
            "forecasts": data.forecasts,
            "accounts": data.accounts,
            "activity_logs": data.activity_logs,
            "price_adjustments": data.price_adjustments,
            "today": data.today,
        },
    )


@app.post("/portal/reseller/order")
async def reseller_order(product_id: int = Form(...), quantity: float = Form(...), notes: str = Form("")):
    try:
        require_positive_number(quantity, "Quantity")
        data.create_order("reseller", product_id, quantity, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "order", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "history", message="Order submitted for team leader approval."))


@app.post("/portal/reseller/reports")
async def reseller_report(
    period_start: date = Form(...),
    period_end: date = Form(...),
    total_sales: float = Form(...),
    total_orders: int = Form(...),
    notes: str = Form(""),
):
    try:
        require_date_range(period_start, period_end)
        require_nonnegative_number(total_sales, "Total sales")
        if total_orders < 0:
            raise ValueError("Total orders cannot be negative.")
        data.add_sales_report("reseller", "Lipa Fresh Mart", period_start, period_end, total_sales, total_orders, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("reseller", "reports", error=str(exc)))
    return redirect_to(safe_portal_path("reseller", "reports", message="Sales report submitted."))


@app.post("/portal/reseller/messages")
async def reseller_message(message: str = Form(...)):
    if len(message.strip()) < 3:
        return redirect_to(safe_portal_path("reseller", "messages", error="Message is too short."))
    inquiry_id = data.inquiries[0]["inquiry_id"] if data.inquiries else 1
    data.add_inquiry_message(inquiry_id, "potential_reseller", "Lipa Fresh Mart", message.strip())
    reply = ask_chatbot(message)
    data.add_inquiry_message(inquiry_id, "chatbot", "Batangas Premium Assistant", reply)
    data.add_log("Lipa Fresh Mart", "sent_reseller_message", f"Inquiry #{inquiry_id}")
    return redirect_to(safe_portal_path("reseller", "messages", message="Message sent."))


@app.post("/portal/team-leader/sales")
async def team_walk_in_sale(product_id: int = Form(...), quantity: float = Form(...), notes: str = Form("")):
    try:
        require_positive_number(quantity, "Quantity")
        data.create_order("team-leader", product_id, quantity, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "sales", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "sales", message="Walk-in sale recorded and inventory updated."))


@app.post("/portal/team-leader/inventory")
async def team_inventory_batch(
    product_id: int = Form(...),
    batch_code: str = Form(...),
    quantity: float = Form(...),
    expiry_date: date = Form(...),
    source_type: str = Form(...),
):
    try:
        if source_type not in {"direct_received", "production"}:
            raise ValueError("Choose a valid batch source.")
        if len(batch_code.strip()) < 4:
            raise ValueError("Batch code is too short.")
        require_positive_number(quantity, "Quantity")
        if expiry_date < date.today():
            raise ValueError("Expiry date cannot be in the past.")
        data.add_product_batch(product_id, batch_code.strip().upper(), quantity, expiry_date, source_type)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "inventory", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "inventory", message="Product batch registered."))


@app.post("/portal/team-leader/inquiries/{inquiry_id}/{decision}")
async def team_inquiry_decision(inquiry_id: int, decision: str):
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
async def team_order_decision(order_id: int, decision: str):
    if decision not in {"approve", "reject", "fulfill"}:
        raise HTTPException(status_code=404)
    if not data.decide_order(order_id, decision):
        return redirect_to(safe_portal_path("team-leader", "orders", error="Order not found."))
    return redirect_to(safe_portal_path("team-leader", "orders", message=f"Order {decision} action recorded."))


@app.post("/portal/team-leader/reports")
async def team_report(
    period_start: date = Form(...),
    period_end: date = Form(...),
    total_sales: float = Form(...),
    total_orders: int = Form(...),
    notes: str = Form(""),
):
    try:
        require_date_range(period_start, period_end)
        require_nonnegative_number(total_sales, "Total sales")
        if total_orders < 0:
            raise ValueError("Total orders cannot be negative.")
        data.add_sales_report("team_leader", "Maria Santos", period_start, period_end, total_sales, total_orders, notes.strip())
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "reports", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "reports", message="Team leader report submitted."))


@app.post("/portal/team-leader/attendance")
async def team_attendance(employee: str = Form(...), work_date: date = Form(...), attendance_status: str = Form(...), time_in: str = Form("")):
    if attendance_status not in {"present", "absent", "late", "excused"}:
        return redirect_to(safe_portal_path("team-leader", "employees", error="Invalid attendance status."))
    try:
        data.add_attendance(employee, work_date, attendance_status, time_in)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "employees", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "employees", message="Attendance recorded."))


@app.post("/portal/team-leader/tasks")
async def team_task(employee: str = Form(...), title: str = Form(...), due_date: date = Form(...)):
    if len(title.strip()) < 4:
        return redirect_to(safe_portal_path("team-leader", "employees", error="Task title is too short."))
    try:
        data.add_task(employee, title.strip(), due_date)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "employees", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "employees", message="Task assigned."))


@app.post("/portal/team-leader/merit")
async def team_merit(
    employee: str = Form(...),
    period: str = Form(...),
    attendance_score: int = Form(...),
    task_score: int = Form(...),
    behavior_score: int = Form(...),
    feedback: str = Form(""),
):
    scores = [attendance_score, task_score, behavior_score]
    if any(score < 1 or score > 5 for score in scores):
        return redirect_to(safe_portal_path("team-leader", "employees", error="Scores must be from 1 to 5."))
    try:
        data.add_evaluation(employee, period, attendance_score, task_score, behavior_score, feedback)
    except ValueError as exc:
        return redirect_to(safe_portal_path("team-leader", "employees", error=str(exc)))
    return redirect_to(safe_portal_path("team-leader", "employees", message="Merit evaluation submitted."))


@app.post("/portal/owner/products")
async def owner_product(product_id: int = Form(...), base_price: float = Form(...), reorder_level: float = Form(...)):
    try:
        require_nonnegative_number(base_price, "Base price")
        require_nonnegative_number(reorder_level, "Reorder level")
        product = data.product_by_id(product_id)
        if product is None:
            raise ValueError("Unknown product.")
        data.update_product_price(product_id, base_price, reorder_level)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "products", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "products", message="Product pricing updated."))


@app.post("/portal/owner/price-adjustments")
async def owner_price_adjustment(batch_code: str = Form(...), adjustment_type: str = Form(...), value: str = Form(...), reason: str = Form(...)):
    if adjustment_type not in {"discount_percent", "fixed_price"}:
        return redirect_to(safe_portal_path("owner", "products", error="Invalid adjustment type."))
    if len(batch_code.strip()) < 4 or len(value.strip()) < 1 or len(reason.strip()) < 4:
        return redirect_to(safe_portal_path("owner", "products", error="Complete the price adjustment fields."))
    try:
        data.add_price_adjustment(batch_code, adjustment_type, value, reason)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "products", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "products", message="Batch price adjustment created."))


@app.post("/portal/owner/accounts")
async def owner_account(account_type: str = Form(...), name: str = Form(...), email: str = Form(...)):
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
async def owner_forecast(model_name: str = Form(...), forecast_horizon_days: int = Form(...)):
    if len(model_name.strip()) < 3:
        return redirect_to(safe_portal_path("owner", "forecasts", error="Model name is required."))
    if forecast_horizon_days <= 0:
        return redirect_to(safe_portal_path("owner", "forecasts", error="Forecast horizon must be greater than zero."))
    try:
        data.add_forecast(model_name, forecast_horizon_days)
    except ValueError as exc:
        return redirect_to(safe_portal_path("owner", "forecasts", error=str(exc)))
    return redirect_to(safe_portal_path("owner", "forecasts", message="Forecast run completed."))
