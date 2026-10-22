from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import URL

from app import repositories as data
from app import owner_recovery
from app.business_time import business_now, business_today
from app.capabilities import require_capability
from app.emailer import send_owner_step_up_otp
from app.portals import shared


router = APIRouter()

OWNER_SALES_PERIOD_OPTIONS = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
]

OWNER_ACCOUNT_TYPES = ("owner", "team_leader", "reseller")
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


def owner_step_up_active(request: Request) -> bool:
    try:
        return int(request.session.get("owner_step_up_until") or 0) > int(business_now().timestamp())
    except (TypeError, ValueError):
        return False


def require_owner_step_up(request: Request, return_section: str, *, draft_fields: dict | None = None) -> object | None:
    require_capability(request, "security.step_up", "owner")
    if owner_step_up_active(request):
        return None
    owner_recovery.capture(request, return_section, draft_fields)
    account_id = shared.session_account_id(request)
    if account_id is not None:
        data.record_owner_audit(
            account_id,
            "owner_step_up_required",
            "owner_security",
            reason="Sensitive action was blocked pending owner verification",
            after_state={"return_section": return_section},
            outcome="failure",
        )
    return shared.redirect_to(shared.safe_portal_path(
        "owner",
        "verify",
        error="Verify your identity before performing this sensitive action.",
        return_to=return_section,
    ))


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
    run_id = shared.optional_positive_id(raw_run_id)
    total = data.count_forecasts(q=filters["q"], run_id=run_id)
    filters["page"] = shared.clamp_page(total, filters["page"], page_size)
    items = data.list_forecasts(
        q=filters["q"], page=filters["page"], page_size=page_size,
        sort=filters["sort"], run_id=run_id,
    )
    return shared.paged(
        items, total, filters["page"], page_size,
        q=filters["q"], sort=filters["sort"], run_id=run_id or "",
    )


def selected_forecast_run(request: Request) -> dict | None:
    raw_run_id = request.query_params.get("run_id", "").strip()
    run_id = shared.optional_positive_id(raw_run_id)
    if run_id is not None:
        return data.get_forecast_run(run_id)
    return data.latest_forecast_run()


def accounts_page(request: Request, page_size: int = 10) -> dict:
    filters = shared.portal_filters(request)
    role_key = request.path_params.get("role_key", "owner")
    account_types = manageable_account_types(role_key)
    account_type = filters["type"] if filters["type"] in account_types else ""
    total = data.count_accounts(q=filters["q"], account_type=account_type, account_types=account_types)
    page_number = data.pagination_meta(total, shared.positive_int(request.query_params.get("page"), maximum=2147483647), page_size)["page"]
    items = data.list_accounts(
        q=filters["q"],
        account_type=account_type,
        account_types=account_types,
        page=page_number,
        page_size=page_size,
        sort=filters["sort"],
    )
    page = shared.paged(items, total, page_number, page_size, q=filters["q"], type=account_type, sort=filters["sort"])
    page["type_options"] = [{"value": value, "label": ACCOUNT_TYPE_LABELS[value]} for value in account_types]
    page["team_leaders"] = data.list_team_leader_accounts()
    page["reseller_assignments"] = data.list_reseller_assignments()
    return page


def dashboard_context(request: Request) -> dict:
    chart_context = owner_sales_chart_context(request)
    context = {
        "metrics": data.current_metrics(),
        "forecasts": data.list_forecasts(limit=3),
        "top_products": data.reseller_most_bought_products(limit=5),
    }
    context.update(chart_context)
    return context


def products_context(request: Request) -> dict:
    page = shared.product_page(request)
    return {"products_page": page, "products": page["items"]}


def reports_context(request: Request) -> dict:
    filters = shared.portal_filters(request)
    total = data.count_sales_reports(report_source="team_leader", q=filters["q"])
    page_number = data.pagination_meta(total, shared.positive_int(request.query_params.get("page"), maximum=2147483647), 10)["page"]
    items = data.list_sales_reports(report_source="team_leader", q=filters["q"], page=page_number, page_size=10, sort=filters["sort"])
    page = shared.paged(items, total, page_number, 10, q=filters["q"], sort=filters["sort"])
    return {"sales_reports_page": page, "sales_reports": page["items"]}


def forecasts_context(request: Request) -> dict:
    page = forecasts_page(request)
    default_horizon = int(data.business_setting("forecast.default_horizon_days"))
    return {
        "forecasts_page": page,
        "forecasts": page["items"],
        "latest_forecast_run": selected_forecast_run(request),
        "forecast_runs": data.list_forecast_runs(),
        "forecast_default_horizon": default_horizon,
    }


def accounts_context(request: Request) -> dict:
    page = accounts_page(request)
    return {
        "accounts_page": page,
        "accounts": page["items"],
        "team_leaders": page["team_leaders"],
        "reseller_assignments": page["reseller_assignments"],
    }


def operations_context(request: Request) -> dict:
    return {
        "operations_summary": data.owner_operations_summary(),
        "recent_orders": data.list_orders(order_type="reseller", limit=8),
        "recent_inquiries": data.list_inquiries(limit=8),
        "alerts": data.list_alerts()[:8],
        "team_workload": data.list_team_workload(),
    }


def inquiries_context(request: Request) -> dict:
    page = shared.inquiries_page(request)
    return {"inquiries_page": page, "inquiries": page["items"]}


def orders_context(request: Request) -> dict:
    page = shared.orders_page(request, "reseller", page_size=6)
    return {"orders_page": page, "orders": page["items"]}


def inventory_context(request: Request) -> dict:
    return {
        "products": data.list_products(page=1, page_size=50),
        "raw_materials": data.list_raw_materials(),
        "product_recipes": data.list_product_recipes(),
        "alerts": data.list_alerts(),
    }


def materials_context(request: Request) -> dict:
    filters = shared.portal_filters(request)
    total = data.count_inventory_items(q=filters["q"], category="raw_material")
    filters["page"] = shared.clamp_page(total, filters["page"], 20)
    items = data.list_inventory_items(
        q=filters["q"], category="raw_material", page=filters["page"], page_size=20, sort=filters["sort"]
    )
    return {"materials_page": shared.paged(items, total, filters["page"], 20, q=filters["q"], sort=filters["sort"]), "materials": items}


def batches_context(request: Request) -> dict:
    filters = shared.portal_filters(request)
    total = data.count_inventory_batches(q=filters["q"], category=filters["type"])
    filters["page"] = shared.clamp_page(total, filters["page"], 10)
    items = data.list_inventory_batches(
        q=filters["q"], category=filters["type"], page=filters["page"], page_size=10, sort=filters["sort"]
    )
    return {
        "batches_page": shared.paged(items, total, filters["page"], 10, q=filters["q"], type=filters["type"], sort=filters["sort"]),
        "batches": items,
        "finished_products": data.list_inventory_items(category="finished_product", page=1, page_size=250),
    }


def movements_context(request: Request) -> dict:
    filters = shared.portal_filters(request)
    category = filters["type"] if filters["type"] in {"opening", "receipts", "production", "sales", "corrections"} else ""
    total = data.count_inventory_movements(q=filters["q"], category=category)
    filters["page"] = shared.clamp_page(total, filters["page"], 10)
    items = data.list_inventory_movements(
        q=filters["q"], category=category, page=filters["page"], page_size=10, sort=filters["sort"]
    )
    return {
        "movements_page": shared.paged(items, total, filters["page"], 10, q=filters["q"], type=category, sort=filters["sort"]),
        "movements": items,
        "inventory_items": data.list_inventory_items(page=1, page_size=250),
        "inventory_batches": data.list_inventory_batches(page=1, page_size=250),
    }


def alerts_context(request: Request) -> dict:
    return {"alerts": data.list_alerts()}


def resellers_context(request: Request) -> dict:
    filters = shared.portal_filters(request)
    status = filters["status"] if filters["status"] in {"pending", "active", "suspended", "inactive"} else ""
    leader = request.query_params.get("leader", "")
    if leader != "unassigned":
        leader = str(shared.optional_positive_id(leader) or "")
    sort = filters["sort"] if filters["sort"] in {"business_asc", "status_asc", "newest", "oldest"} else "business_asc"
    criteria = {"q": filters["q"], "status": status, "leader": leader}
    total = data.count_owner_resellers(**criteria)
    page_number = data.pagination_meta(total, shared.positive_int(request.query_params.get("page"), maximum=2147483647), 10)["page"]
    items = data.list_owner_resellers(**criteria, sort=sort, page=page_number, page_size=10)
    return {
        "reseller_assignments": items,
        "resellers_page": shared.paged(items, total, page_number, 10, **criteria, sort=sort),
        "team_leaders": data.list_team_leader_accounts(),
    }


def verification_context(request: Request) -> dict:
    return {
        "owner_step_up_active": owner_step_up_active(request),
        "owner_step_up_pending": request.query_params.get("challenge") == "1",
    }


def settings_context(request: Request) -> dict:
    return {"business_settings": data.list_business_settings()}


def profile_context(request: Request) -> dict:
    return {"account_profile": data.account_portal_profile(shared.session_account_id(request))}


SECTION_LOADERS = {
    ("owner", "dashboard"): dashboard_context,
    ("owner", "operations"): operations_context,
    ("owner", "inquiries"): inquiries_context,
    ("owner", "orders"): orders_context,
    ("owner", "inventory"): inventory_context,
    ("owner", "materials"): materials_context,
    ("owner", "batches"): batches_context,
    ("owner", "movements"): movements_context,
    ("owner", "alerts"): alerts_context,
    ("owner", "products"): products_context,
    ("owner", "reports"): reports_context,
    ("owner", "forecasts"): forecasts_context,
    ("owner", "accounts"): accounts_context,
    ("owner", "resellers"): resellers_context,
    ("owner", "verify"): verification_context,
    ("owner", "settings"): settings_context,
    ("owner", "profile"): profile_context,
}


@router.get("/portal/owner/dashboard/sales-chart")
async def owner_dashboard_sales_chart(request: Request):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "operations.view", "owner")
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
    require_capability(request, "profile.manage_own", "owner")
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
    require_capability(request, "profile.manage_own", "owner")
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
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    try:
        data.resend_activation(account_id, shared.session_account_id(request) or 0, account_types=OWNER_ACCOUNT_TYPES)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="A new activation link was queued."))


@router.post("/portal/owner/accounts/{account_id}/role")
async def owner_account_role(
    request: Request,
    account_id: int,
    team_leader_role: str = Form(...),
    reason: str = Form(""),
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    try:
        clean_reason = " ".join(reason.strip().split())
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for the role change.")
        data.update_team_leader_account_role(account_id, team_leader_role, actor_account_id=shared.session_account_id(request))
        data.record_owner_audit(
            shared.session_account_id(request) or 0,
            "changed_team_leader_role",
            "accounts",
            account_id,
            reason=clean_reason,
            after_state={"team_leader_role": team_leader_role},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Team leader role updated."))


@router.post("/portal/owner/products")
async def owner_product(
    request: Request,
    product_id: int = Form(...),
    base_price: str = Form(...),
    reason: str = Form(""),
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inventory.prices", "owner")
    step_up = require_owner_step_up(request, "products")
    if step_up:
        return step_up
    try:
        parsed_price = data.parse_money(base_price, "Base price")
        product = data.product_by_id(product_id)
        if product is None:
            raise ValueError("Unknown product.")
        clean_reason = " ".join(reason.strip().split())
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for the price change.")
        data.update_product_price(product_id, parsed_price, actor_account_id=shared.session_account_id(request))
        data.record_owner_audit(
            shared.session_account_id(request) or 0,
            "changed_product_price",
            "inventory_items",
            product_id,
            reason=clean_reason,
            before_state={"base_price": product.get("base_price")},
            after_state={"base_price": str(parsed_price)},
        )
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
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    try:
        if account_type not in {"owner", "team_leader"}:
            raise ValueError("Invalid account type.")
        if len(name.strip()) < 2:
            raise ValueError("Account name is required.")
        email = shared.require_email(email)
        account = data.add_account(
            account_type,
            name,
            email,
            team_leader_role=team_leader_role,
            actor_account_id=shared.session_account_id(request),
        )
        data.record_owner_audit(
            shared.session_account_id(request) or 0,
            "created_portal_account",
            "accounts",
            account["account_id"],
            after_state={"account_type": account_type, "team_leader_role": account.get("team_leader_role")},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Account created. A single-use activation email was queued."))


@router.post("/portal/owner/resellers/{reseller_id}/team-leader")
async def owner_reseller_team_leader(
    request: Request,
    reseller_id: int,
    team_leader_account_id: int = Form(...),
    reason: str = Form(""),
    return_section: str = Form("accounts"),
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "resellers.manage_all", "owner")
    return_section = return_section if return_section in {"accounts", "resellers"} else "accounts"
    step_up = require_owner_step_up(request, return_section)
    if step_up:
        return step_up
    try:
        clean_reason = " ".join(reason.strip().split())
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for changing the reseller assignment.")
        data.set_reseller_team_leader(reseller_id, team_leader_account_id, actor_account_id=shared.session_account_id(request))
        data.record_owner_audit(
            shared.session_account_id(request) or 0,
            "overrode_reseller_assignment",
            "resellers",
            reseller_id,
            reason=clean_reason,
            after_state={"team_leader_account_id": team_leader_account_id},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", return_section, error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", return_section, message="Reseller team leader updated."))


@router.post("/portal/owner/forecasts")
async def owner_forecast(request: Request, model_name: str = Form(...), forecast_horizon_days: int = Form(...)):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "forecasts.manage", "owner")
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
    require_capability(request, "forecasts.manage", "owner")
    account_id = shared.session_account_id(request)
    status_payload = data.forecast_run_status(forecast_run_id, account_id or 0)
    if not status_payload:
        raise HTTPException(status_code=404)
    return status_payload


@router.post("/portal/owner/verify/step-up/request")
def owner_step_up_request(request: Request):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "security.step_up", "owner")
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("owner", "verify", error="Your session expired."))
    try:
        challenge = data.request_owner_step_up(account_id)
        sent, message = send_owner_step_up_otp(
            to_email=challenge["email"], name=challenge["name"], otp_code=challenge["otp_code"]
        )
        if not sent:
            data.cancel_owner_step_up(account_id, challenge["challenge_id"])
            raise ValueError(message)
        request.session["owner_step_up_challenge_id"] = challenge["challenge_id"]
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "verify", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path(
        "owner", "verify", challenge="1", message="A 6-digit verification code was sent to your email."
    ))


@router.post("/portal/owner/verify/step-up/confirm")
def owner_step_up_confirm(request: Request, otp_code: Annotated[str, Form()]):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "security.step_up", "owner")
    account_id = shared.session_account_id(request)
    challenge_id = request.session.get("owner_step_up_challenge_id")
    try:
        if account_id is None or challenge_id is None:
            raise ValueError("Request a new verification code.")
        data.confirm_owner_step_up(account_id, int(challenge_id), otp_code)
    except (TypeError, ValueError) as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "verify", challenge="1", error=str(exc)))
    request.session.pop("owner_step_up_challenge_id", None)
    request.session["owner_step_up_until"] = int((business_now() + timedelta(minutes=10)).timestamp())
    destination = owner_recovery.complete(request)
    return shared.redirect_to(str(URL(destination).include_query_params(
        message="Verification complete—review and submit your action."
    )))


@router.post("/portal/owner/accounts/{account_id}/status")
def owner_account_status(
    request: Request,
    account_id: int,
    active: Annotated[str, Form()],
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    try:
        data.set_account_active(account_id, active == "true", shared.session_account_id(request) or 0, reason)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Account status updated."))


@router.post("/portal/owner/accounts/{account_id}/sessions/revoke")
def owner_account_revoke_sessions(
    request: Request,
    account_id: int,
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    try:
        data.revoke_managed_account_sessions(account_id, shared.session_account_id(request) or 0, reason)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    if account_id == shared.session_account_id(request):
        request.session.clear()
        return shared.redirect_to(shared.path_with_query("/login", message="Your sessions were revoked."))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Account sessions revoked."))


@router.post("/portal/owner/accounts/{account_id}/password-reset")
def owner_account_password_reset(
    request: Request,
    account_id: int,
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    clean_reason = " ".join(reason.strip().split())
    try:
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for the forced password reset.")
        actor_id = shared.session_account_id(request) or 0
        data.queue_forced_password_reset(account_id, actor_id)
        data.record_owner_audit(
            actor_id,
            "forced_account_password_reset",
            "accounts",
            account_id,
            reason=clean_reason,
            after_state={"password_reset_email": "queued", "sessions_revoked_on_completion": True},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Password reset email queued."))


@router.post("/portal/owner/alerts/{alert_id}/status")
def owner_alert_status(
    request: Request,
    alert_id: int,
    status: Annotated[str, Form()],
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inventory.override", "owner")
    step_up = require_owner_step_up(request, "alerts")
    if step_up:
        return step_up
    try:
        data.acknowledge_alert(
            alert_id,
            shared.session_account_id(request) or 0,
            reason,
            resolved=status == "resolved",
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "alerts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "alerts", message="Alert status updated."))


@router.post("/portal/owner/settings")
def owner_settings_update(
    request: Request,
    order_payment_window_minutes: Annotated[int, Form()],
    near_expiry_days: Annotated[int, Form()],
    critical_expiry_days: Annotated[int, Form()],
    forecast_default_horizon_days: Annotated[int, Form()],
    owner_critical_alerts: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    contact_address: Annotated[str, Form()] = "",
    business_hours: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "settings.manage", "owner")
    step_up = require_owner_step_up(request, "settings")
    if step_up:
        return step_up
    try:
        data.update_business_settings(
            {
                "order.payment_window_minutes": order_payment_window_minutes,
                "inventory.near_expiry_days": near_expiry_days,
                "inventory.critical_expiry_days": critical_expiry_days,
                "forecast.default_horizon_days": forecast_default_horizon_days,
                "notifications.owner_critical_alerts": owner_critical_alerts,
                "business.contact_email": contact_email,
                "business.contact_phone": contact_phone,
                "business.contact_address": contact_address,
                "business.hours": business_hours,
            },
            shared.session_account_id(request) or 0,
            reason,
        )
    except (TypeError, ValueError) as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "settings", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "settings", message="Business settings updated."))


@router.post("/portal/owner/orders/{order_id}/{decision}")
def owner_order_override(
    request: Request,
    order_id: int,
    decision: str,
    reason: Annotated[str, Form()],
    rejection_reason: Annotated[str, Form()] = "",
    payment_received: Annotated[str, Form()] = "",
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "orders.override", "owner")
    step_up = require_owner_step_up(request, "orders")
    if step_up:
        return step_up
    if decision not in {"approve", "reject", "fulfill"}:
        raise HTTPException(status_code=404)
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5:
        return shared.redirect_to(shared.safe_portal_path("owner", "orders", error="Enter an override reason."))
    try:
        payment = None if decision != "reject" else payment_received == "yes"
        changed = data.decide_order(
            order_id,
            decision,
            actor_account_id=shared.session_account_id(request),
            rejection_reason=rejection_reason,
            payment_received=payment,
        )
        if not changed:
            raise ValueError("Order not found or the requested transition is unavailable.")
        data.record_owner_audit(
            shared.session_account_id(request) or 0,
            f"owner_{decision}_order_override",
            "orders",
            order_id,
            reason=clean_reason,
            after_state={"decision": decision},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "orders", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "orders", message=f"Order {decision} override recorded."))


@router.post("/portal/owner/inquiries/{inquiry_id}/{decision}")
def owner_inquiry_override(
    request: Request,
    inquiry_id: int,
    decision: str,
    reason: Annotated[str, Form()],
    rejection_reason: Annotated[str, Form()] = "",
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inquiries.override", "owner")
    step_up = require_owner_step_up(request, "inquiries")
    if step_up:
        return step_up
    clean_reason = " ".join(reason.strip().split())
    if len(clean_reason) < 5:
        return shared.redirect_to(shared.safe_portal_path("owner", "inquiries", error="Enter an override reason."))
    try:
        actor_id = shared.session_account_id(request) or 0
        if decision == "approve":
            changed = data.add_reseller_from_inquiry(
                inquiry_id,
                decision_actor_account_id=actor_id,
            )
        elif decision == "reject":
            changed = data.reject_inquiry(
                inquiry_id,
                rejection_reason,
                decision_actor_account_id=actor_id,
            )
        else:
            raise HTTPException(status_code=404)
        if not changed:
            raise ValueError("Inquiry not found or the requested decision is unavailable.")
        data.record_owner_audit(
            actor_id,
            f"owner_{decision}_inquiry_override",
            "inquiries",
            inquiry_id,
            reason=clean_reason,
            after_state={"decision": decision},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "inquiries", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "inquiries", message="Inquiry override recorded."))


@router.post("/portal/owner/accounts/{account_id}/transfer")
def owner_transfer_account_work(
    request: Request,
    account_id: int,
    destination_account_id: Annotated[int, Form()],
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "accounts.manage_all", "owner")
    step_up = require_owner_step_up(request, "accounts")
    if step_up:
        return step_up
    try:
        data.transfer_team_leader_work(
            account_id,
            destination_account_id,
            shared.session_account_id(request) or 0,
            reason,
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "accounts", message="Active work transferred."))


@router.post("/portal/owner/inventory/corrections")
def owner_inventory_correction(
    request: Request,
    item_id: Annotated[int, Form()],
    quantity_delta: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    batch_id: Annotated[int | None, Form()] = None,
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inventory.override", "owner")
    step_up = require_owner_step_up(request, "movements")
    if step_up:
        return step_up
    try:
        data.compensate_inventory(
            item_id,
            quantity_delta,
            shared.session_account_id(request) or 0,
            reason,
            batch_id=batch_id,
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "movements", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "movements", message="Compensating movement recorded."))


@router.post("/portal/owner/materials/{item_id}/receipt")
def owner_material_receipt(
    request: Request,
    item_id: int,
    quantity: Annotated[str, Form()],
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inventory.override", "owner")
    step_up = require_owner_step_up(request, "materials")
    if step_up:
        return step_up
    clean_reason = " ".join(reason.strip().split())
    try:
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for the material receipt.")
        actor_id = shared.session_account_id(request) or 0
        item = data.add_raw_inventory_quantity(item_id, quantity, actor_account_id=actor_id)
        data.record_owner_audit(
            actor_id,
            "owner_raw_material_receipt",
            "inventory_items",
            item_id,
            reason=clean_reason,
            after_state={"available": item.get("available"), "quantity_added": quantity},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "materials", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "materials", message="Material receipt recorded."))


@router.post("/portal/owner/batches/receipt")
def owner_finished_batch_receipt(
    request: Request,
    product_id: Annotated[int, Form()],
    batch_code: Annotated[str, Form()],
    quantity: Annotated[str, Form()],
    expiry_date: Annotated[date, Form()],
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inventory.override", "owner")
    step_up = require_owner_step_up(request, "batches")
    if step_up:
        return step_up
    clean_reason = " ".join(reason.strip().split())
    try:
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for the finished-product receipt.")
        actor_id = shared.session_account_id(request) or 0
        batch = data.add_product_batch(
            product_id, batch_code, quantity, expiry_date, actor_account_id=actor_id
        )
        data.record_owner_audit(
            actor_id,
            "owner_finished_product_receipt",
            "inventory_batches",
            batch.get("product_batch_id"),
            reason=clean_reason,
            after_state={"product_id": product_id, "batch_code": batch_code, "quantity": quantity},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "batches", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "batches", message="Finished-product receipt recorded."))


@router.post("/portal/owner/inventory/production")
def owner_production_override(
    request: Request,
    product_id: Annotated[int, Form()],
    batch_code: Annotated[str, Form()],
    quantity: Annotated[str, Form()],
    expiry_date: Annotated[date, Form()],
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "inventory.override", "owner")
    step_up = require_owner_step_up(request, "inventory")
    if step_up:
        return step_up
    clean_reason = " ".join(reason.strip().split())
    try:
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for the production override.")
        actor_id = shared.session_account_id(request) or 0
        batch = data.produce_product(
            product_id, batch_code, quantity, expiry_date, actor_account_id=actor_id
        )
        data.record_owner_audit(
            actor_id,
            "owner_production_override",
            "inventory_batches",
            batch.get("product_batch_id"),
            reason=clean_reason,
            after_state={"product_id": product_id, "batch_code": batch_code, "quantity": quantity},
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("owner", "inventory", message="Production override recorded."))


@router.post("/portal/owner/forecasts/runs/{forecast_run_id}/{action}")
def owner_forecast_admin(
    request: Request,
    forecast_run_id: int,
    action: str,
    reason: Annotated[str, Form()],
):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "system.jobs.manage", "owner")
    step_up = require_owner_step_up(request, "forecasts")
    if step_up:
        return step_up
    try:
        actor_id = shared.session_account_id(request) or 0
        if action == "retry":
            run = data.retry_forecast_run(forecast_run_id, actor_id, reason)
        elif action == "cancel":
            run = data.cancel_forecast_run(forecast_run_id, actor_id, reason)
        else:
            raise HTTPException(status_code=404)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("owner", "forecasts", error=str(exc)))
    return shared.redirect_to(shared.path_with_query(
        "/portal/owner/forecasts",
        run_id=run["forecast_run_id"],
        message=f"Forecast {action} recorded.",
    ))


def _csv_cell(value: object) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str, sort_keys=True)
    elif value is None:
        text = ""
    else:
        text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@router.post("/portal/owner/exports/{resource}.csv")
def owner_export(request: Request, resource: str):
    guard = shared.require_portal_session(request, "owner")
    if guard:
        return guard
    require_capability(request, "exports.create", "owner")
    try:
        rows = data.owner_export_rows(resource)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    output = io.StringIO(newline="")
    fields = list(rows[0].keys()) if rows else ["no_records"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_cell(value) for key, value in row.items()})
    data.record_owner_audit(
        shared.session_account_id(request) or 0,
        "exported_owner_data",
        resource,
        reason="Owner requested CSV export",
        after_state={"record_count": len(rows)},
    )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="meattrack-{resource}.csv"'},
    )
