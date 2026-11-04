from __future__ import annotations

from datetime import date
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from app.uploads import parse_product_image
from app import reseller_messaging
from app import repositories as data
from app.business_time import business_today
from app.repositories.portal_dashboard import leader_work_summary
from app.portals import owner as owner_portal
from app.portals import shared


router = APIRouter()


def logs_page(request: Request, page_size: int = 10, inventory_only: bool = False) -> dict:
    filters = shared.portal_filters(request)
    movement_category = filters["type"] if filters["type"] in {"opening", "receipts", "production", "sales", "returns", "reservations", "removals", "corrections"} else ""
    if inventory_only:
        total = data.count_inventory_movements(q=filters["q"], category=movement_category)
        filters["page"] = shared.clamp_page(total, filters["page"], page_size)
        items = data.list_inventory_movements(
            q=filters["q"],
            category=movement_category,
            page=filters["page"],
            page_size=page_size,
            sort=filters["sort"],
        )
    else:
        total = data.count_activity_logs(q=filters["q"], inventory_only=False)
        filters["page"] = shared.clamp_page(total, filters["page"], page_size)
        items = data.list_activity_logs(q=filters["q"], page=filters["page"], page_size=page_size, inventory_only=False, sort=filters["sort"])
    page = shared.paged(
        items,
        total,
        filters["page"],
        page_size,
        q=filters["q"],
        type=movement_category if inventory_only else "",
        sort=filters["sort"],
    )
    page["inventory_movements"] = inventory_only
    current_page = page["pagination"]["page"]
    total_pages = page["pagination"]["total_pages"]
    first_page = max(1, min(current_page - 2, total_pages - 4))
    page["pagination"]["page_numbers"] = list(range(first_page, min(total_pages, first_page + 4) + 1))
    return page


def finished_inventory_products_page(request: Request, page_size: int = 8) -> dict:
    filters = shared.portal_filters(request)
    category = f"finished_product:{filters['type']}" if filters["type"] else "finished_product"
    total = data.count_inventory_items(q=filters["q"], category=category)
    filters["page"] = shared.clamp_page(total, filters["page"], page_size)
    items = data.list_inventory_items(q=filters["q"], category=category, page=filters["page"], page_size=page_size, sort=filters["sort"])
    return shared.paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def raw_materials_inventory_page(request: Request, page_size: int = 10) -> dict:
    filters = shared.portal_filters(request)
    material_category = filters["type"] if filters["type"] in (*data.raw_material_categories, "removed") else ""
    category = f"raw_material:{material_category}" if material_category else "raw_material"
    total = data.count_inventory_items(q=filters["q"], category=category)
    filters["page"] = shared.clamp_page(total, filters["page"], page_size)
    items = data.list_inventory_items(q=filters["q"], category=category, page=filters["page"], page_size=page_size, sort=filters["sort"])
    page = shared.paged(
        items,
        total,
        filters["page"],
        page_size,
        q=filters["q"],
        type=material_category,
        sort=filters["sort"],
    )
    current_page = page["pagination"]["page"]
    total_pages = page["pagination"]["total_pages"]
    first_page = max(1, min(current_page - 2, total_pages - 4))
    page["pagination"]["page_numbers"] = list(range(first_page, min(total_pages, first_page + 4) + 1))
    return page


def raw_materials_inventory_context(request: Request) -> dict:
    raw_materials_page = raw_materials_inventory_page(request)
    items = raw_materials_page["items"]
    maximum_available = max((float(item.get("available") or 0) for item in items), default=0)
    raw_items = [
        {
            **item,
            "stock_percent": round((float(item.get("available") or 0) / maximum_available) * 100)
            if maximum_available
            else 0,
        }
        for item in items
    ]
    return {
        "raw_materials_page": raw_materials_page,
        "raw_items": raw_items,
        "raw_material_tabs": (
            ("", "Active materials"),
            ("meat", "Meat"),
            ("raw_material", "Other supplies"),
            ("removed", "Removed materials"),
        ),
    }


def finished_products_inventory_context(request: Request) -> dict:
    products_page = finished_inventory_products_page(request)
    finished_products = [
        product
        for product in products_page["items"]
        if str(product.get("category") or "").strip().lower() != "package"
    ]
    product_packages = [
        product
        for product in products_page["items"]
        if str(product.get("category") or "").strip().lower() == "package"
    ]
    return {
        "inventory_items_page": products_page,
        "inventory_products_page": products_page,
        "finished_products": finished_products,
        "finished_product_packages": product_packages,
        "inventory_item_tabs": [
            {"label": "All products", "value": ""},
            *[
                {"label": category, "value": category}
                for category in data.product_categories
            ],
            {"label": "Packages", "value": "Package"},
        ],
    }


def inventory_batches_page(request: Request, page_size: int = 10) -> dict:
    filters = shared.portal_filters(request)
    categories = [*data.product_categories, "Package"]
    category = filters["type"] if filters["type"] in categories else ""
    total = data.count_inventory_batches(q=filters["q"], category=category)
    filters["page"] = shared.clamp_page(total, filters["page"], page_size)
    items = data.list_inventory_batches(
        q=filters["q"],
        category=category,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    page = shared.paged(
        items,
        total,
        filters["page"],
        page_size,
        q=filters["q"],
        type=category,
        sort=filters["sort"],
    )
    current_page = page["pagination"]["page"]
    total_pages = page["pagination"]["total_pages"]
    first_page = max(1, min(current_page - 2, total_pages - 4))
    page["pagination"]["page_numbers"] = list(range(first_page, min(total_pages, first_page + 4) + 1))
    return page


def profile_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    account = data.account_portal_profile(account_id) if account_id is not None else None
    return {
        "account_profile": account,
    }


def dashboard_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    leader_role = shared.session_team_leader_role(request) or "sales"
    work_summary = leader_work_summary(account_id or 0,leader_role)
    if leader_role == "inventory":
        return {
            "work_summary":work_summary,"dashboard_date":business_today(),
            "team_leader_role": leader_role,
            "metrics": data.inventory_stock_metrics(),
            "products": data.list_products(),
            "movement_analytics": data.inventory_product_movement_analytics(days=30, limit=8),
        }
    return {
        "team_leader_role": leader_role,
        "metrics": data.current_metrics(team_leader_account_id=account_id),
        "settlement_summary": data.settlement_summary(account_id),
        "work_summary":work_summary,"dashboard_date":business_today(),
        "orders": data.list_orders(order_type="reseller", limit=5, team_leader_account_id=account_id,exclude_to_pay=True),
    }


def sales_context(request: Request) -> dict:
    page = shared.orders_page(request, "walk_in", team_leader_account_id=shared.session_account_id(request))
    return {"products": data.list_products(), "orders_page": page, "orders": page["items"]}


def inventory_context(request: Request) -> dict:
    recipe_products_page = shared.product_page(request, page_size=8, individual_only=True)
    recipe_product_ids = [product["product_id"] for product in recipe_products_page["items"]]
    return {
        "new_reference": lambda: str(uuid4()),
        "stock_action_draft": request.session.pop("inventory_stock_draft", None),
        "products": data.list_products(active_only=True, individual_only=True),
        "dispatch_products": data.list_products(active_only=True),
        "recipe_products_page": recipe_products_page,
        "recipe_products": recipe_products_page["items"],
        "raw_materials": data.list_raw_materials(),
        "product_recipes": data.list_product_recipes(product_ids=recipe_product_ids),
        "raw_material_categories": data.raw_material_categories,
        "product_categories": data.product_categories,
        "stock_units": data.stock_units,
        "content_units": data.content_units,
        "recipe_units": data.recipe_units,
        "recipe_edit_draft": request.session.pop("recipe_edit_draft", None),
    }


def alerts_context(request: Request) -> dict:
    from app.repositories.stock_alerts import stock_alert_workspace
    category = request.query_params.get("category", "")
    if category not in {"", "expired", "expiring", "low-stock"}:
        raise HTTPException(status_code=404)
    return {**stock_alert_workspace(), "stock_alert_category": category}


def batches_context(request: Request) -> dict:
    page = inventory_batches_page(request)
    return {
        "inventory_batches_page": page,
        "inventory_batches": page["items"],
        "batch_categories": (
            ("", "All batches"),
            *[(category, category) for category in data.product_categories],
            ("Package", "Packages"),
        ),
    }


def logs_context(request: Request) -> dict:
    page = logs_page(request, inventory_only=shared.session_team_leader_role(request) == "inventory")
    return {
        "activity_logs_page": page,
        "activity_logs": page["items"],
        "movement_categories": (
            ("", "All movements"),
            ("receipts", "Receipts"),
            ("removals", "Removals & restores"),
            ("returns", "Returns"),
            ("production", "Production"),
            ("sales", "Sales"),
            ("reservations", "Reservations"),
        ),
    }


def resellers_context(request: Request) -> dict:
    filters = shared.portal_filters(request)
    status = filters["status"] if filters["status"] in {"pending", "active", "suspended", "inactive"} else ""
    account_id = shared.session_account_id(request)
    total = data.count_team_resellers(
        team_leader_account_id=account_id,
        q=filters["q"],
        status=status,
    )
    filters["page"] = shared.clamp_page(total, filters["page"], 10)
    items = data.list_team_resellers(
        team_leader_account_id=account_id,
        q=filters["q"],
        status=status,
        page=filters["page"],
        page_size=10,
        sort=filters["sort"],
    )
    page = shared.paged(
        items,
        total,
        filters["page"],
        10,
        q=filters["q"],
        status=status,
        sort=filters["sort"],
    )
    return {"team_resellers_page": page, "team_resellers": page["items"]}


def reseller_messages_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    return {
        "reseller_message_threads": reseller_messaging.leader_conversations(account_id),
    }


def orders_context(request: Request) -> dict:
    page = shared.orders_page(request, "reseller", team_leader_account_id=shared.session_account_id(request))
    return {"orders_page": page, "orders": page["items"]}


def reports_context(request: Request) -> dict:
    from .activity_reports import workspace_context
    page = shared.reports_page(request, "team_leader", team_leader_account_id=shared.session_account_id(request))
    return {"sales_reports_page": page, "sales_reports": page["items"], **workspace_context(request)}


def supervisor_accounts_context(request: Request) -> dict:
    return {
        "team_leaders": data.list_team_leader_accounts(),
        "reseller_assignments": data.list_reseller_assignments(),
    }


SECTION_LOADERS = {
    ("team-leader", "dashboard"): dashboard_context,
    ("team-leader", "sales"): sales_context,
    ("team-leader", "inventory"): inventory_context,
    ("team-leader", "raw-materials"): raw_materials_inventory_context,
    ("team-leader", "finished-products"): finished_products_inventory_context,
    ("team-leader", "batches"): batches_context,
    ("team-leader", "alerts"): alerts_context,
    ("team-leader", "logs"): logs_context,
    ("team-leader", "messages"): reseller_messages_context,
    ("team-leader", "resellers"): resellers_context,
    ("team-leader", "orders"): orders_context,
    ("team-leader", "reports"): reports_context,
    ("team-leader", "profile"): profile_context,
    ("sales-supervisor", "dashboard"): dashboard_context,
    ("sales-supervisor", "messages"): reseller_messages_context,
    ("sales-supervisor", "orders"): orders_context,
    ("sales-supervisor", "reports"): reports_context,
    ("sales-supervisor", "accounts"): supervisor_accounts_context,
    ("sales-supervisor", "resellers"): resellers_context,
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
    finished_products = [
        product
        for product in products_page["items"]
        if str(product.get("category") or "").strip().lower() != "package"
    ]
    product_packages = [
        product
        for product in products_page["items"]
        if str(product.get("category") or "").strip().lower() == "package"
    ]
    return shared.templates().TemplateResponse(
        request,
        "portals/team-leader/_inventory_products.html",
        {
            "request": request,
            "inventory_items_page": products_page,
            "inventory_products_page": products_page,
            "finished_products": finished_products,
            "finished_product_packages": product_packages,
            "inventory_item_tabs": [
                {"label": "All products", "value": ""},
                *[
                    {"label": category, "value": category}
                    for category in data.product_categories
                ],
                {"label": "Packages", "value": "Package"},
            ],
        },
    )


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
        upload = form.get("photo")
        photo = await parse_product_image(upload if isinstance(upload, UploadFile) else None)
        await run_in_threadpool(data.create_product_with_recipe,
            str(form.get("name", "")),
            str(form.get("category", "")),
            "0",
            material_item_ids,
            quantity_required,
            quantity_required_units,
            form.get("pack_size", ""),
            str(form.get("pack_size_unit", "")),
            actor_account_id=shared.session_account_id(request),
            product_photo=photo,
        )
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("team-leader", "inventory", message="Product recipe created."))


@router.post("/portal/team-leader/products/{product_id}/photo")
async def team_product_photo(request: Request, product_id: int):
    guard = shared.require_portal_session(request, "team-leader")
    if guard:
        return guard
    role_guard = shared.require_team_leader_role(request, "inventory")
    if role_guard:
        return role_guard

    form = await request.form()
    try:
        upload = form.get("photo")
        photo = await parse_product_image(upload if isinstance(upload, UploadFile) else None)
        if photo is None:
            raise ValueError("Choose a product photo.")
        await run_in_threadpool(data.save_product_image, product_id, photo,
                                actor_account_id=shared.session_account_id(request))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path(
            "team-leader", "product-details", error=str(exc), product_id=str(product_id)))
    return shared.redirect_to(shared.safe_portal_path(
        "team-leader", "product-details", message="Product photo updated.", product_id=str(product_id)))


@router.post("/portal/team-leader/production")
async def team_production(
    request: Request,
    product_id: int = Form(...),
    batch_code: str = Form(...),
    quantity: str = Form(...),
    expiry_date: date = Form(...),
    production_date: Annotated[date | None, Form()] = None,
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
            production_date=production_date,
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


@router.post("/portal/sales-supervisor/orders/{order_id}/{decision}")
@router.post("/portal/team-leader/orders/{order_id}/{decision}")
async def team_order_decision(
    request: Request,
    order_id: int,
    decision: str,
    rejection_reason: Annotated[str, Form()] = "",
    payment_received: Annotated[str, Form()] = "",
):
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
        payment_determination = None
        if decision == "reject":
            if payment_received not in {"yes", "no"}:
                raise ValueError("Indicate whether payment was received before rejecting the order.")
            payment_determination = payment_received == "yes"
        if not data.decide_order(
            order_id,
            decision,
            team_leader_account_id=shared.session_account_id(request),
            rejection_reason=rejection_reason,
            payment_received=payment_determination,
        ):
            return shared.redirect_to(shared.safe_portal_path(role_key, "orders", error="Order not found or already finalized."))
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path(role_key, "orders", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path(role_key, "orders", message=f"Order {decision} action recorded."))


@router.post("/portal/sales-supervisor/reports")
@router.post("/portal/team-leader/reports")
async def team_report(request: Request):
    from .activity_reports import submit
    role_key = "sales-supervisor" if request.session.get("role_key") == "sales-supervisor" else "team-leader"
    return await submit(request, role_key)


@router.post("/portal/sales-supervisor/accounts")
async def supervisor_account(
    request: Request,
):
    guard = shared.require_portal_session(request, "sales-supervisor")
    if guard:
        return guard
    raise HTTPException(status_code=403, detail="Sales supervisors cannot create accounts.")


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


@router.post('/portal/team-leader/stock-actions/{action}')
async def manual_stock_action(request: Request, action: str):
    from app.capabilities import require_capability
    from app.repositories.inventory import manual_stock
    guard = shared.require_portal_session(request, 'team-leader')
    if guard:
        return guard
    require_capability(request, 'inventory.manage')
    form = await request.form()
    actor = shared.session_account_id(request)
    target = 'inventory'
    extra = {}
    try:
        if action == 'opening-balance':
            await run_in_threadpool(manual_stock.opening_balance, int(str(form.get('product_id', ''))),
                form.get('batch_code', ''), form.get('quantity', ''), form.get('expiry_date', ''),
                form.get('reason', ''), form.get('reference', ''), actor)
        elif action == 'deduct-material':
            await run_in_threadpool(manual_stock.adjust_raw_material, int(str(form.get('item_id', ''))),
                form.get('quantity', ''), form.get('reason', ''), form.get('reference', ''), actor)
        elif action == 'dispatch':
            await run_in_threadpool(manual_stock.dispatch_stock, int(str(form.get('product_id', ''))),
                form.get('quantity', ''), form.get('reason', ''), form.get('reference', ''), actor)
        elif action in {'remove-material', 'restore-material'}:
            target = 'raw-materials'
            await run_in_threadpool(manual_stock.set_material_active, int(str(form.get('item_id', ''))),
                action == 'restore-material', actor, form.get('confirmed') == 'yes')
        elif action == 'remove-batch':
            target = 'product-details'
            product_id = int(str(form.get('product_id', '')))
            extra = {'product_id': str(product_id)}
            await run_in_threadpool(manual_stock.remove_batch, product_id, int(str(form.get('batch_id', ''))),
                form.get('expected_quantity', ''), form.get('reason', ''), form.get('reference', ''), actor,
                form.get('confirmed') == 'yes')
        else:
            raise HTTPException(404)
    except ValueError as exc:
        if target == 'inventory' and action in {'opening-balance', 'deduct-material', 'dispatch'}:
            request.session['inventory_stock_draft'] = {'action': action, 'values': {
                key: str(form.get(key, ''))[:500] for key in
                ('product_id', 'item_id', 'batch_code', 'quantity', 'expiry_date', 'reason', 'reference')
            }}
            extra['modal'] = action
        return shared.redirect_to(shared.safe_portal_path('team-leader', target, error=str(exc), **extra))
    return shared.redirect_to(shared.safe_portal_path('team-leader', target, message='Stock action recorded.', **extra))


@router.post('/portal/team-leader/products/{product_id}/recipe')
async def team_update_recipe(request: Request, product_id: int):
    from app.capabilities import require_capability
    from app.repositories.inventory.products import update_product_recipe
    guard = shared.require_portal_session(request, 'team-leader')
    if guard:
        return guard
    require_capability(request, 'inventory.manage')
    form = await request.form()
    ids = form.getlist('material_item_id[]')
    quantities = form.getlist('quantity_required[]')
    units = form.getlist('quantity_required_unit[]')
    extra = {'modal': 'product-recipes', 'q': str(form.get('q', ''))[:200], 'page': str(form.get('page', '1'))[:8]}
    try:
        await run_in_threadpool(update_product_recipe, product_id, ids, quantities, units,
            form.get('pack_size', ''), form.get('pack_size_unit', ''), shared.session_account_id(request))
    except ValueError as exc:
        request.session['recipe_edit_draft'] = {
            'product_id': product_id, 'error': str(exc),
            'pack_size': str(form.get('pack_size', ''))[:40], 'pack_size_unit': str(form.get('pack_size_unit', ''))[:10],
            'rows': [{'raw_material_id': str(i)[:20], 'quantity_required': str(q)[:40], 'unit': str(u)[:10]}
                     for i,q,u in list(zip(ids,quantities,units))[:200]],
        }
        return shared.redirect_to(shared.safe_portal_path('team-leader','inventory',error=str(exc),**extra))
    return shared.redirect_to(shared.safe_portal_path('team-leader','inventory',message='Recipe and declared net content updated.',**extra))
