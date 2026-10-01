from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request

from app import live_chat
from app import repositories as data
from app.config import LIVE_CHAT_ENABLED
from app.portals import owner as owner_portal
from app.portals import shared


router = APIRouter()


def logs_page(request: Request, page_size: int = 10, inventory_only: bool = False) -> dict:
    filters = shared.portal_filters(request)
    if inventory_only:
        items = data.list_inventory_movements(q=filters["q"], page=filters["page"], page_size=page_size, sort=filters["sort"])
        total = data.count_inventory_movements(q=filters["q"])
    else:
        items = data.list_activity_logs(q=filters["q"], page=filters["page"], page_size=page_size, inventory_only=False, sort=filters["sort"])
        total = data.count_activity_logs(q=filters["q"], inventory_only=False)
    page = shared.paged(items, total, filters["page"], page_size, q=filters["q"], sort=filters["sort"])
    page["inventory_movements"] = inventory_only
    return page


def finished_inventory_products_page(request: Request, page_size: int = 8) -> dict:
    filters = shared.portal_filters(request)
    category = f"finished_product:{filters['type']}" if filters["type"] else "finished_product"
    items = data.list_inventory_items(q=filters["q"], category=category, page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_items(q=filters["q"], category=category)
    return shared.paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def raw_materials_inventory_page(request: Request, page_size: int = 10) -> dict:
    filters = shared.portal_filters(request)
    category = f"raw_material:{filters['type']}" if filters["type"] else "raw_material"
    items = data.list_inventory_items(q=filters["q"], category=category, page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_items(q=filters["q"], category=category)
    return shared.paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


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
    filters = shared.portal_filters(request)
    items = data.list_inventory_batches(q=filters["q"], category=filters["type"], page=filters["page"], page_size=page_size, sort=filters["sort"])
    total = data.count_inventory_batches(q=filters["q"], category=filters["type"])
    return shared.paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def profile_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    account = data.account_portal_profile(account_id) if account_id is not None else None
    return {
        "account_profile": account,
        "password_otp_pending": request.query_params.get("otp") == "1",
    }


def dashboard_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    leader_role = shared.session_team_leader_role(request) or "sales"
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


def sales_context(request: Request) -> dict:
    page = shared.orders_page(request, "walk_in", team_leader_account_id=shared.session_account_id(request))
    return {"products": data.list_products(), "orders_page": page, "orders": page["items"]}


def inventory_context(request: Request) -> dict:
    recipe_products_page = shared.product_page(request, page_size=8)
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


def batches_context(request: Request) -> dict:
    page = inventory_batches_page(request)
    return {
        "inventory_batches_page": page,
        "inventory_batches": page["items"],
        "product_categories": data.product_categories,
    }


def logs_context(request: Request) -> dict:
    page = logs_page(request, inventory_only=shared.session_team_leader_role(request) == "inventory")
    return {"activity_logs_page": page, "activity_logs": page["items"]}


def inquiries_context(request: Request) -> dict:
    page = shared.inquiries_page(request, assigned_team_leader_account_id=shared.session_account_id(request))
    return {"inquiries_page": page, "inquiries": page["items"]}


def live_chat_context(request: Request) -> dict:
    return {
        "live_chat_workspace": live_chat.workspace(shared.session_account_id(request)),
        "live_chat_enabled": LIVE_CHAT_ENABLED,
    }


def orders_context(request: Request) -> dict:
    page = shared.orders_page(request, "reseller", team_leader_account_id=shared.session_account_id(request))
    return {"orders_page": page, "orders": page["items"]}


def reports_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    page = shared.reports_page(request, "team_leader", team_leader_account_id=account_id)
    return {
        "sales_reports_page": page,
        "sales_reports": page["items"],
        "reseller_purchase_summary": data.team_reseller_purchase_summary(team_leader_account_id=account_id),
        "team_report_sales": data.team_sales_report_entries(team_leader_account_id=account_id),
        "team_rejected_orders": data.team_rejected_order_entries(team_leader_account_id=account_id),
    }


SECTION_LOADERS = {
    ("team-leader", "dashboard"): dashboard_context,
    ("team-leader", "sales"): sales_context,
    ("team-leader", "inventory"): inventory_context,
    ("team-leader", "raw-materials"): raw_materials_inventory_context,
    ("team-leader", "finished-products"): finished_products_inventory_context,
    ("team-leader", "batches"): batches_context,
    ("team-leader", "logs"): logs_context,
    ("team-leader", "inquiries"): inquiries_context,
    ("team-leader", "live-chat"): live_chat_context,
    ("team-leader", "orders"): orders_context,
    ("team-leader", "reports"): reports_context,
    ("team-leader", "profile"): profile_context,
    ("sales-supervisor", "dashboard"): dashboard_context,
    ("sales-supervisor", "live-chat"): live_chat_context,
    ("sales-supervisor", "inquiries"): inquiries_context,
    ("sales-supervisor", "orders"): orders_context,
    ("sales-supervisor", "reports"): reports_context,
    ("sales-supervisor", "accounts"): owner_portal.accounts_context,
    ("sales-supervisor", "profile"): profile_context,
}


@router.get("/portal/team-leader/inventory-items")
async def team_inventory_items_legacy_redirect(request: Request):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    query = request.url.query
    suffix = f"?{query}" if query else ""
    return shared.redirect_to(f"/portal/team-leader/raw-materials{suffix}")


@router.get("/portal/team-leader/inventory-items/products")
async def team_inventory_products_legacy_partial_redirect(request: Request):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    query = request.url.query
    suffix = f"?{query}" if query else ""
    return shared.redirect_to(f"/portal/team-leader/finished-products/products{suffix}")


@router.get("/portal/team-leader/finished-products/products")
async def team_inventory_products_partial(request: Request):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    products_page = finished_inventory_products_page(request)
    return shared.templates().TemplateResponse(
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


@router.post("/portal/sales-supervisor/profile/password")
@router.post("/portal/team-leader/profile/password")
async def team_leader_profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    role_key = request.session.get("role_key") if request.session.get("role_key") in {"team-leader", "sales-supervisor"} else "team-leader"
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path(role_key, "profile", error="Your session expired. Please sign in again."))
    try:
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
        pending = data.request_account_password_change(account_id, current_password, new_password, ("team_leader",))
        sent, email_message = shared.send_password_change_otp(
            to_email=pending["email"],
            name=pending["name"],
            otp_code=pending["otp_code"],
        )
        if not sent:
            data.cancel_account_password_change(account_id, pending.get("otp_id"))
            raise ValueError(email_message)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path(role_key, "profile", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path(role_key, "profile", otp="1", message="OTP sent to your account email. Enter it below to confirm your password change."))


@router.post("/portal/sales-supervisor/profile/password/confirm")
@router.post("/portal/team-leader/profile/password/confirm")
async def team_leader_profile_password_confirm(request: Request, otp_code: str = Form(...)):
    role_key = request.session.get("role_key") if request.session.get("role_key") in {"team-leader", "sales-supervisor"} else "team-leader"
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path(role_key, "profile", error="Your session expired. Please sign in again."))
    try:
        data.confirm_account_password_change(account_id, otp_code, ("team_leader",))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path(role_key, "profile", otp="1", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path(role_key, "profile", message="Password updated."))


@router.post("/portal/sales-supervisor/sales")
@router.post("/portal/team-leader/sales")
async def team_walk_in_sale(request: Request, product_id: int = Form(...), quantity: str = Form(...), notes: str = Form("")):
    role_key = request.session.get("role_key") if request.session.get("role_key") in {"team-leader", "sales-supervisor"} else "team-leader"
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    return shared.redirect_to(shared.safe_portal_path(role_key, "dashboard", error="Walk-in sales are disabled for team leader demo accounts."))


@router.post("/portal/team-leader/inventory-items")
async def team_inventory_item(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    unit: str = Form(...),
    quantity: str = Form(...),
):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        data.add_raw_inventory_item(name, category, unit, quantity, actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", message="Raw inventory updated."))


@router.post("/portal/team-leader/inventory-items/quantity")
async def team_inventory_item_quantity(
    request: Request,
    raw_material_id: int = Form(...),
    quantity: str = Form(...),
):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        data.add_raw_inventory_quantity(raw_material_id, quantity, actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", message="Raw inventory quantity added."))


@router.post("/portal/team-leader/products")
async def team_product(request: Request):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
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
            actor_account_id=shared.session_account_id(request),
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", message="Product recipe created."))


@router.post("/portal/team-leader/production")
async def team_production(
    request: Request,
    product_id: int = Form(...),
    batch_code: str = Form(...),
    quantity: str = Form(...),
    expiry_date: date = Form(...),
):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        data.produce_product(
            product_id,
            batch_code,
            quantity,
            expiry_date,
            actor_account_id=shared.session_account_id(request),
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", message="Product produced and raw materials deducted."))


@router.post("/portal/team-leader/products/{product_id}/pack-content")
async def team_product_pack_content(
    request: Request,
    product_id: int,
    pack_size: str = Form(...),
    pack_size_unit: str = Form(...),
):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard
    try:
        data.update_product_pack_content(
            product_id,
            pack_size,
            pack_size_unit,
            actor_account_id=shared.session_account_id(request),
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", message="Pack content updated."))


@router.post("/portal/sales-supervisor/inquiries/{inquiry_id}/{decision}")
@router.post("/portal/team-leader/inquiries/{inquiry_id}/{decision}")
async def team_inquiry_decision(
    request: Request,
    inquiry_id: int,
    decision: str,
    rejection_reason: str = Form(""),
):
    role_key = request.session.get("role_key") if request.session.get("role_key") in {"team-leader", "sales-supervisor"} else "team-leader"
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "sales")
    if role_guard:
        return role_guard
    if decision == "approve":
        try:
            reseller = data.add_reseller_from_inquiry(inquiry_id, approving_team_leader_account_id=shared.session_account_id(request))
            if reseller is None:
                return shared.redirect_to(shared.safe_portal_path(role_key, "inquiries", error="Inquiry not found."))
        except ValueError as exc:
            return shared.redirect_to(shared.safe_portal_path(role_key, "inquiries", error=str(exc)))
        return shared.redirect_to(shared.safe_portal_path(role_key, "inquiries", message="Inquiry approved. A single-use activation email was queued."))
    if decision == "reject":
        try:
            if not data.reject_inquiry(
                inquiry_id,
                rejection_reason,
                reviewing_team_leader_account_id=shared.session_account_id(request),
            ):
                return shared.redirect_to(shared.safe_portal_path(role_key, "inquiries", error="Inquiry not found."))
        except ValueError as exc:
            return shared.redirect_to(shared.safe_portal_path(role_key, "inquiries", error=str(exc)))
        return shared.redirect_to(shared.safe_portal_path(role_key, "inquiries", message="Inquiry rejected. The applicant email was queued."))
    raise HTTPException(status_code=404)


@router.post("/portal/sales-supervisor/orders/{order_id}/{decision}")
@router.post("/portal/team-leader/orders/{order_id}/{decision}")
async def team_order_decision(request: Request, order_id: int, decision: str):
    role_key = request.session.get("role_key") if request.session.get("role_key") in {"team-leader", "sales-supervisor"} else "team-leader"
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "sales")
    if role_guard:
        return role_guard
    if decision not in {"approve", "reject", "fulfill"}:
        raise HTTPException(status_code=404)
    try:
        if not data.decide_order(order_id, decision, team_leader_account_id=shared.session_account_id(request)):
            return shared.redirect_to(shared.safe_portal_path(role_key, "orders", error="Order not found or already finalized."))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path(role_key, "orders", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path(role_key, "orders", message=f"Order {decision} action recorded."))


@router.post("/portal/sales-supervisor/reports")
@router.post("/portal/team-leader/reports")
async def team_report(
    request: Request,
    period_start: date = Form(...),
    period_end: date = Form(...),
    notes: str = Form(""),
):
    role_key = request.session.get("role_key") if request.session.get("role_key") in {"team-leader", "sales-supervisor"} else "team-leader"
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "sales")
    if role_guard:
        return role_guard
    try:
        shared.require_date_range(period_start, period_end)
        account_id = shared.session_account_id(request)
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
        return shared.redirect_to(shared.safe_portal_path(role_key, "reports", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path(role_key, "reports", message="Team leader report submitted with current fulfilled sales totals."))


@router.post("/portal/sales-supervisor/accounts")
async def supervisor_account(
    request: Request,
    account_type: str = Form("team_leader"),
    name: str = Form(...),
    email: str = Form(...),
    team_leader_role: str = Form("sales"),
):
    guard = shared.require_portal_session(request, "sales-supervisor")
    if guard:
        return guard
    try:
        if account_type != "team_leader":
            raise ValueError("Sales supervisors can create team leader accounts only.")
        email = shared.require_email(email)
        data.add_account("team_leader", name, email, team_leader_role=team_leader_role)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("sales-supervisor", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("sales-supervisor", "accounts", message="Account created. A single-use activation email was queued."))


@router.post("/portal/sales-supervisor/accounts/{account_id}/activation")
async def supervisor_resend_activation(request: Request, account_id: int):
    guard = shared.require_portal_session(request, "sales-supervisor")
    if guard:
        return guard
    try:
        data.resend_activation(
            account_id,
            shared.session_account_id(request) or 0,
            account_types=owner_portal.SUPERVISOR_ACCOUNT_TYPES,
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("sales-supervisor", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("sales-supervisor", "accounts", message="A new activation link was queued."))


@router.post("/portal/sales-supervisor/resellers/{reseller_id}/team-leader")
async def supervisor_reseller_team_leader(request: Request, reseller_id: int, team_leader_account_id: int = Form(...)):
    guard = shared.require_portal_session(request, "sales-supervisor")
    if guard:
        return guard
    try:
        data.set_reseller_team_leader(reseller_id, team_leader_account_id, actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("sales-supervisor", "accounts", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("sales-supervisor", "accounts", message="Reseller team leader updated."))
