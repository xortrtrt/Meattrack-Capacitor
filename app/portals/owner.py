from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from app import repositories as data
from app.business_time import business_today
from app.portals import shared


router = APIRouter()

OWNER_SALES_PERIOD_OPTIONS = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
]

OWNER_ACCOUNT_TYPES = ("owner", "team_leader")
SUPERVISOR_ACCOUNT_TYPES = ("team_leader", "reseller")
ACCOUNT_TYPE_LABELS = {
    "owner": "Owner",
    "team_leader": "Team leader",
    "reseller": "Reseller",
}


def manageable_account_types(role_key: str) -> tuple[str, ...]:
    if role_key == "sales-supervisor":
        return SUPERVISOR_ACCOUNT_TYPES
    return OWNER_ACCOUNT_TYPES


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
    today_value = business_today()
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


def forecasts_page(request: Request, page_size: int = 10) -> dict:
    filters = shared.portal_filters(request)
    raw_run_id = request.query_params.get("run_id", "").strip()
    run_id = int(raw_run_id) if raw_run_id.isdigit() and int(raw_run_id) > 0 else None
    items = data.list_forecasts(
        q=filters["q"], page=filters["page"], page_size=page_size,
        sort=filters["sort"], run_id=run_id,
    )
    total = data.count_forecasts(q=filters["q"], run_id=run_id)
    return shared.paged(
        items, total, filters["page"], page_size,
        q=filters["q"], sort=filters["sort"], run_id=run_id or "",
    )


def selected_forecast_run(request: Request) -> dict | None:
    raw_run_id = request.query_params.get("run_id", "").strip()
    if raw_run_id.isdigit() and int(raw_run_id) > 0:
        return data.get_forecast_run(int(raw_run_id))
    return data.latest_forecast_run()


def accounts_page(request: Request, page_size: int = 10) -> dict:
    filters = shared.portal_filters(request)
    role_key = request.path_params.get("role_key", "owner")
    account_types = manageable_account_types(role_key)
    account_type = filters["type"] if filters["type"] in account_types else ""
    items = data.list_accounts(
        q=filters["q"],
        account_type=account_type,
        account_types=account_types,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    total = data.count_accounts(q=filters["q"], account_type=account_type, account_types=account_types)
    page = shared.paged(items, total, filters["page"], page_size, q=filters["q"], type=account_type, sort=filters["sort"])
    page["type_options"] = [{"value": value, "label": ACCOUNT_TYPE_LABELS[value]} for value in account_types]
    page["team_leaders"] = data.list_team_leader_accounts()
    page["reseller_assignments"] = data.list_reseller_assignments()
    return page


def dashboard_context(request: Request) -> dict:
    chart_context = owner_sales_chart_context(request)
    context = {
        "metrics": data.current_metrics(),
        "products": data.list_products(page=1, page_size=8),
        "forecasts": data.list_forecasts(limit=6),
        "top_products": data.reseller_most_bought_products(limit=5),
    }
    context.update(chart_context)
    return context


def products_context(request: Request) -> dict:
    page = shared.product_page(request)
    return {"products_page": page, "products": page["items"]}


def reports_context(request: Request) -> dict:
    page = shared.reports_page(request, "team_leader")
    return {"sales_reports_page": page, "sales_reports": page["items"]}


def forecasts_context(request: Request) -> dict:
    page = forecasts_page(request)
    return {
        "forecasts_page": page,
        "forecasts": page["items"],
        "latest_forecast_run": selected_forecast_run(request),
        "forecast_runs": data.list_forecast_runs(),
    }


def accounts_context(request: Request) -> dict:
    page = accounts_page(request)
    role_key = request.path_params.get("role_key", "owner")
    return {
        "accounts_page": page,
        "accounts": page["items"],
        "team_leaders": page["team_leaders"] if role_key == "sales-supervisor" else [],
        "reseller_assignments": page["reseller_assignments"] if role_key == "sales-supervisor" else [],
    }


def profile_context(request: Request) -> dict:
    return {"account_profile": data.account_portal_profile(shared.session_account_id(request))}


SECTION_LOADERS = {
    ("owner", "dashboard"): dashboard_context,
    ("owner", "products"): products_context,
    ("owner", "reports"): reports_context,
    ("owner", "forecasts"): forecasts_context,
    ("owner", "accounts"): accounts_context,
    ("owner", "profile"): profile_context,
}


@router.get("/portal/owner/dashboard/sales-chart")
async def owner_dashboard_sales_chart(request: Request):
    guard = shared.require_portal_session(request, "owner")
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


@router.post("/portal/owner/profile/password")
async def owner_profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    try:
        if account_id is None:
            raise ValueError("Your session expired. Please sign in again.")
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
        pending = data.request_account_password_change(account_id, current_password, new_password, ("owner",))
        sent, email_message = shared.send_password_change_otp(
            to_email=pending["email"], name=pending["name"], otp_code=pending["otp_code"]
        )
        if not sent:
            data.cancel_account_password_change(account_id, pending.get("otp_id"))
            raise ValueError(email_message)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "profile", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "profile", otp="1", message="OTP sent to your email."))


@router.post("/portal/owner/profile/password/confirm")
async def owner_profile_password_confirm(request: Request, otp_code: str = Form(...)):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    try:
        if account_id is None:
            raise ValueError("Your session expired. Please sign in again.")
        data.confirm_account_password_change(account_id, otp_code, ("owner",))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "profile", otp="1", error=str(exc)))
    request.session.clear()
    return shared.redirect_to(shared.path_with_query("/login", message="Password updated. Sign in again."))


@router.post("/portal/owner/accounts/{account_id}/activation")
async def owner_resend_activation(request: Request, account_id: int):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        data.resend_activation(account_id, shared.session_account_id(request) or 0, account_types=OWNER_ACCOUNT_TYPES)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="A new activation link was queued."))


@router.post("/portal/owner/accounts/{account_id}/role")
async def owner_account_role(request: Request, account_id: int, team_leader_role: str = Form(...)):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        data.update_team_leader_account_role(account_id, team_leader_role, actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Team leader role updated."))


@router.post("/portal/owner/products")
async def owner_product(request: Request, product_id: int = Form(...), base_price: str = Form(...)):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        parsed_price = data.parse_money(base_price, "Base price")
        product = data.product_by_id(product_id)
        if product is None:
            raise ValueError("Unknown product.")
        data.update_product_price(product_id, parsed_price, actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "products", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "products", message="Product pricing updated."))


@router.post("/portal/owner/accounts")
async def owner_account(
    request: Request,
    account_type: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    team_leader_role: str = Form("sales"),
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        if account_type not in {"owner", "team_leader"}:
            raise ValueError("Invalid account type.")
        if len(name.strip()) < 2:
            raise ValueError("Account name is required.")
        email = shared.require_email(email)
        data.add_account(account_type, name, email, team_leader_role=team_leader_role)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Account created. A single-use activation email was queued."))


@router.post("/portal/owner/resellers/{reseller_id}/team-leader")
async def owner_reseller_team_leader(request: Request, reseller_id: int, team_leader_account_id: int = Form(...)):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        data.set_reseller_team_leader(reseller_id, team_leader_account_id, actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Reseller team leader updated."))


@router.post("/portal/owner/forecasts")
async def owner_forecast(request: Request, model_name: str = Form(...), forecast_horizon_days: int = Form(...)):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    try:
        account_id = shared.session_account_id(request)
        if account_id is None:
            raise ValueError("Your session expired. Please sign in again.")
        run = data.queue_forecast(model_name, forecast_horizon_days, account_id)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "forecasts", error=str(exc)))
    message = "An existing forecast is already in progress." if run.get("existing") else "Forecast queued for processing."
    return shared.redirect_to(shared.path_with_query(
        "/portal/owner/forecasts",
        message=message,
        run_id=run["forecast_run_id"],
    ))


@router.get("/portal/owner/forecasts/runs/{forecast_run_id}/status")
async def owner_forecast_status(request: Request, forecast_run_id: int):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    status_payload = data.forecast_run_status(forecast_run_id, account_id or 0)
    if not status_payload:
        raise HTTPException(status_code=404)
    return status_payload
