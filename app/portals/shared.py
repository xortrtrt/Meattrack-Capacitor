from __future__ import annotations

from datetime import date
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app import repositories as data
from app import portal_config
from app import portal_dependencies
from app import uploads as uploads_module
from app import web
from app.capabilities import portal_section_capability, require_capability
from app.emailer import send_password_change_otp as deliver_password_change_otp


router = APIRouter()

PORTAL_TEMPLATES = {
    "owner": "portals/owner.html",
    "team-leader": "portals/team_leader.html",
    "sales-supervisor": "portals/team_leader.html",
    "reseller": "portals/reseller.html",
}

PORTAL_SECTION_LOADERS = {}

NOTIFICATION_CATEGORIES = (
    ("account", "Accounts"),
    ("forecast", "Forecasts"),
    ("inquiry", "Inquiries"),
    ("inventory", "Inventory"),
    ("order", "Orders"),
    ("report", "Reports"),
)


def templates():
    return web.templates


def redirect_to(path: str) -> RedirectResponse:
    return web.redirect_to(path)


def session_account_id(request: Request) -> int | None:
    return portal_dependencies.session_account_id(request)


def session_team_leader_role(request: Request) -> str | None:
    return portal_dependencies.session_team_leader_role(request)


def require_portal_session(request: Request, role_key: str) -> RedirectResponse | None:
    return portal_dependencies.require_portal_session(request, role_key)


def require_team_leader_role(request: Request, required_role: str) -> RedirectResponse | None:
    current_role = session_team_leader_role(request)
    if current_role == required_role or (
        required_role == "sales" and current_role == "sales-supervisor"
    ):
        return None
    role_key = (
        request.session.get("role_key")
        if request.session.get("role_key") in {"team-leader", "sales-supervisor"}
        else "team-leader"
    )
    return redirect_to(
        safe_portal_path(
            role_key,
            "dashboard",
            error="Your team leader account cannot access that action.",
        )
    )


def require_email(email: str) -> str:
    return web.require_email(email)


def require_date_range(period_start: date, period_end: date) -> None:
    web.require_date_range(period_start, period_end)


def send_password_change_otp(**kwargs):
    return deliver_password_change_otp(**kwargs)


async def parse_payment_proof_attachments(uploads) -> list[dict]:
    return await uploads_module.parse_payment_proof_attachments(uploads)


async def save_profile_image_upload(account_id: int, upload: UploadFile) -> str:
    return await uploads_module.save_profile_image_upload(account_id, upload)


def remove_profile_image_file(relative_path: str | None) -> None:
    uploads_module.remove_profile_image_file(relative_path)


def safe_portal_path(role_key: str, section: str, message: str = "", error: str = "", **extra_params: str) -> str:
    return portal_dependencies.safe_portal_path(role_key, section, message=message, error=error, **extra_params)


def path_with_query(path: str, **params: str) -> str:
    return web.path_with_query(path, **params)


def positive_int(value: str | None, default: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        return default
    return max(1, min(parsed, maximum))


def optional_positive_id(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw or not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
        return None
    parsed = int(raw)
    return parsed if 0 < parsed <= 9223372036854775807 else None


def requested_page(value: str | None) -> int:
    return optional_positive_id(value) or 1


def clamp_page(total: int, page: int, page_size: int) -> int:
    return data.pagination_meta(total, page, page_size)["page"]


def portal_filters(request: Request) -> dict:
    return {
        "q": request.query_params.get("q", "").strip(),
        "status": request.query_params.get("status", "").strip(),
        "type": request.query_params.get("type", "").strip(),
        "sort": request.query_params.get("sort", "").strip(),
        "page": requested_page(request.query_params.get("page")),
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


def product_page(request: Request, page_size: int = 12, active_only: bool = False) -> dict:
    filters = portal_filters(request)
    total = data.count_products(q=filters["q"], category=filters["type"], active_only=active_only)
    filters["page"] = clamp_page(total, filters["page"], page_size)
    items = data.list_products(
        q=filters["q"],
        category=filters["type"],
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
        active_only=active_only,
    )
    return paged(items, total, filters["page"], page_size, q=filters["q"], type=filters["type"], sort=filters["sort"])


def orders_page(
    request: Request,
    order_type: str,
    page_size: int = 10,
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
    status_override: str | None = None,
) -> dict:
    filters = portal_filters(request)
    status = filters["status"] if status_override is None else status_override
    list_kwargs = {
        "order_type": order_type,
        "q": filters["q"],
        "status": status,
        "team_leader_account_id": team_leader_account_id,
        "reseller_account_id": reseller_account_id,
        "page": filters["page"],
        "page_size": page_size,
        "sort": filters["sort"],
    }
    count_kwargs = {
        "order_type": order_type,
        "q": filters["q"],
        "status": status,
        "team_leader_account_id": team_leader_account_id,
        "reseller_account_id": reseller_account_id,
    }
    total = data.count_orders(**count_kwargs)
    filters["page"] = clamp_page(total, filters["page"], page_size)
    list_kwargs["page"] = filters["page"]
    items = data.list_orders(**list_kwargs)
    return paged(
        items,
        total,
        filters["page"],
        page_size,
        q=filters["q"],
        status=status,
        sort=filters["sort"],
    )


def reports_page(
    request: Request,
    report_source: str | None = None,
    page_size: int = 10,
    team_leader_account_id: int | None = None,
    reseller_account_id: int | None = None,
) -> dict:
    filters = portal_filters(request)
    total = data.count_sales_reports(
        report_source=report_source,
        q=filters["q"],
        team_leader_account_id=team_leader_account_id,
        reseller_account_id=reseller_account_id,
    )
    filters["page"] = clamp_page(total, filters["page"], page_size)
    items = data.list_sales_reports(
        report_source=report_source,
        q=filters["q"],
        team_leader_account_id=team_leader_account_id,
        reseller_account_id=reseller_account_id,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    return paged(items, total, filters["page"], page_size, q=filters["q"], sort=filters["sort"])


def inquiries_page(
    request: Request,
    page_size: int = 10,
    assigned_team_leader_account_id: int | None = None,
    include_unassigned: bool = False,
) -> dict:
    filters = portal_filters(request)
    total = data.count_inquiries(
        q=filters["q"],
        status=filters["status"],
        assigned_team_leader_account_id=assigned_team_leader_account_id,
        include_unassigned=include_unassigned,
    )
    filters["page"] = clamp_page(total, filters["page"], page_size)
    items = data.list_inquiries(
        q=filters["q"],
        status=filters["status"],
        assigned_team_leader_account_id=assigned_team_leader_account_id,
        include_unassigned=include_unassigned,
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
    )
    return paged(items, total, filters["page"], page_size, q=filters["q"], status=filters["status"], sort=filters["sort"])


def notifications_context(request: Request, page_size: int = 15) -> dict:
    account_id = session_account_id(request)
    role_key = str(request.path_params.get("role_key") or request.session.get("role_key") or "")
    requested_status = request.query_params.get("status", "").strip().lower()
    status = requested_status if requested_status in {"unread", "read"} else ""
    requested_category = request.query_params.get("type", "").strip().lower()
    category_values = {value for value, _label in NOTIFICATION_CATEGORIES}
    category = requested_category if requested_category in category_values else ""
    total = data.count_notification_history(account_id, status=status, category=category)
    page_number = clamp_page(total, requested_page(request.query_params.get("page")), page_size)
    items = normalize_notification_targets(
        data.list_notification_history(
            account_id,
            status=status,
            category=category,
            page=page_number,
            page_size=page_size,
        ),
        role_key,
        session_team_leader_role(request),
    )
    return {
        "notifications_page": paged(
            items,
            total,
            page_number,
            page_size,
            status=status,
            type=category,
        ),
        "notification_categories": NOTIFICATION_CATEGORIES,
    }


def notification_target_for(
    role_key: str,
    team_leader_role: str | None,
    target_url: str | None,
) -> str:
    fallback = f"/portal/{role_key}/notifications"
    if role_key not in portal_config.roles or not target_url:
        return fallback

    parsed = urlsplit(str(target_url).strip())
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/portal/"):
        return fallback

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3:
        return fallback
    _portal, target_role, section = parts
    current_sections = {
        item[0] for item in portal_config.portal_nav_for(role_key, team_leader_role)
    }
    current_sections.update(portal_config.portal_secondary_sections.get(role_key, {}))
    if section not in current_sections:
        return fallback

    leader_roles = {"team-leader", "sales-supervisor"}
    same_portal = target_role == role_key
    same_leader_family = role_key in leader_roles and target_role in leader_roles
    if not same_portal and not same_leader_family:
        return fallback

    safe_path = f"/portal/{role_key}/{section}"
    return urlunsplit(("", "", safe_path, parsed.query, parsed.fragment))


def normalize_notification_targets(
    items: list[dict],
    role_key: str,
    team_leader_role: str | None,
) -> list[dict]:
    if role_key == "team-leader" and team_leader_role == "inventory":
        items = [
            {**item, "title": "Returned stock update", "message": "Review returned quantities, batches, verification, and replacement stock."}
            if "/returns" in str(item.get("target_url") or "") else item
            for item in items
            if not any(path in str(item.get("target_url") or "") for path in ("/refunds", "/orders", "/reports"))
        ]
    return [
        {
            **item,
            "target_url": notification_target_for(
                role_key,
                team_leader_role,
                item.get("target_url"),
            ),
        }
        for item in items
    ]


def portal_nav_for_request(role_key: str, request: Request) -> list[tuple[str, str, str]]:
    return portal_config.portal_nav_for(role_key, session_team_leader_role(request))


def portal_role_for_request(role_key: str, request: Request) -> dict:
    role = dict(portal_config.roles[role_key])
    account_name = request.session.get("account_name")
    account_email = request.session.get("account_email")
    if account_name:
        role["name"] = account_name
    if account_email:
        role["email"] = account_email
    role["profile_image_path"] = request.session.get("profile_image_path") or ""
    role["profile_image_position_x"] = request.session.get("profile_image_position_x", 50)
    role["profile_image_position_y"] = request.session.get("profile_image_position_y", 50)
    role["profile_image_scale"] = request.session.get("profile_image_scale", 1)
    if role_key in {"team-leader", "sales-supervisor"}:
        leader_role = session_team_leader_role(request) or "sales"
        role["label"] = portal_config.team_leader_role_labels.get(leader_role, role["label"])
        role["team_leader_role"] = leader_role
    return role


def portal_section_context(role_key: str, section: str, request: Request) -> dict:
    context = PORTAL_SECTION_LOADERS[(role_key, section)](request)
    if role_key == "team-leader" and session_team_leader_role(request) == "inventory":
        from app.repositories.return_views import inventory_context_view
        context = inventory_context_view(context)
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


@router.get("/portal/order-payment-proofs/{proof_id}")
async def portal_order_payment_proof(request: Request, proof_id: int):
    role_key = request.session.get("role_key")
    account_id = session_account_id(request)
    if role_key == "team-leader" and session_team_leader_role(request) == "inventory":
        raise HTTPException(status_code=404)
    if role_key not in {"owner", "team-leader", "sales-supervisor", "reseller"} or account_id is None:
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


@router.get("/portal/{role_key}")
async def portal_default(request: Request, role_key: str):
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    return redirect_to(f"/portal/{role_key}/{portal_config.default_section_for(role_key, session_team_leader_role(request))}")


@router.get("/portal/{role_key}/{section}")
async def portal(request: Request, role_key: str, section: str, message: str = "", error: str = ""):
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    if role_key == "reseller" and section == "returns":
        return redirect_to("/portal/reseller/history?" + urlencode({**request.query_params, "view": "returns"}))
    nav = portal_nav_for_request(role_key, request)
    nav_sections = {item[0]: item for item in nav}
    secondary_section = portal_config.portal_secondary_sections.get(role_key, {}).get(section)
    if section not in nav_sections and secondary_section is None:
        raise HTTPException(status_code=404)
    capability_role = (
        f"team-leader:{session_team_leader_role(request) or 'sales'}"
        if role_key == "team-leader"
        else role_key
    )
    require_capability(
        request,
        portal_section_capability(request, capability_role, section),
        capability_role,
    )
    section_title = nav_sections[section][1] if section in nav_sections else secondary_section[0]

    context = {
        "request": request,
        "role_key": role_key,
        "portal_css_key": "team-leader" if role_key == "sales-supervisor" else role_key,
        "portal_body_roles": (
            "role-team-leader role-sales-supervisor leader-role-sales"
            if role_key == "sales-supervisor"
            else f"role-{role_key}"
        ),
        "can_create_owner_account": role_key == "owner",
        "can_manage_team_leader_roles": role_key == "owner",
        "show_reseller_team_assignments": role_key in {"owner", "sales-supervisor"},
        "accounts_form_action": f"/portal/{role_key}/accounts",
        "role": portal_role_for_request(role_key, request),
        "nav": nav,
        "nav_groups": (
            [
                {"label": label, "items": [item for item in nav if item[0] in slugs]}
                for label, slugs in portal_config.owner_nav_groups
            ]
            if role_key == "owner"
            else []
        ),
        "section": section,
        "section_title": section_title,
        "message": message,
        "error": error,
        "cart_count": data.reseller_cart_count(session_account_id(request)) if role_key == "reseller" else 0,
        "notifications": normalize_notification_targets(
            data.list_notifications(role_key, request.session.get("account_id")),
            role_key,
            session_team_leader_role(request),
        ),
        "unread_notifications": data.unread_notification_count(role_key, request.session.get("account_id")),
    }
    context.update(portal_section_context(role_key, section, request))
    if role_key == "owner":
        from app import owner_recovery
        context["owner_resume"] = owner_recovery.consume(request)
    return templates().TemplateResponse(
        request,
        PORTAL_TEMPLATES[role_key],
        context,
    )


@router.post("/portal/{role_key}/notifications/read")
async def portal_notifications_read(request: Request, role_key: str):
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    data.mark_notifications_read(role_key, request.session.get("account_id"))
    section = request.query_params.get("section") or portal_config.roles[role_key]["default_section"]
    return redirect_to(safe_portal_path(role_key, section))


@router.post("/portal/{role_key}/notifications/{notification_id}/open")
async def portal_notification_open(request: Request, role_key: str, notification_id: int):
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    notification = data.mark_notification_read(session_account_id(request), notification_id)
    if not notification:
        raise HTTPException(status_code=404)
    if (
        role_key == "reseller"
        and notification.get("source_type") == "order_system_messages"
        and notification.get("source_id") is not None
    ):
        system_message_id = int(notification["source_id"])
        return redirect_to(
            f"/portal/reseller/inbox?system_message={system_message_id}"
            f"#system-message-{system_message_id}"
        )
    target_url = notification_target_for(
        role_key,
        session_team_leader_role(request),
        notification.get("target_url"),
    )
    return redirect_to(target_url)


@router.post("/portal/{role_key}/profile/picture")
async def portal_profile_picture(
    request: Request,
    role_key: str,
    profile_picture: UploadFile = File(...),
    profile_image_position_x: str = Form("50"),
    profile_image_position_y: str = Form("50"),
    profile_image_scale: str = Form("1"),
):
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path(role_key, "profile", error="Your session expired. Please sign in again."))
    previous_path = request.session.get("profile_image_path")
    saved_path = None
    try:
        saved_path = await save_profile_image_upload(account_id, profile_picture)
        account = data.update_account_profile_image(
            account_id,
            saved_path,
            position_x=profile_image_position_x,
            position_y=profile_image_position_y,
            scale=profile_image_scale,
        )
    except ValueError as exc:
        remove_profile_image_file(saved_path)
        return redirect_to(safe_portal_path(role_key, "profile", error=str(exc)))
    request.session["profile_image_path"] = account.get("profile_image_path") or ""
    request.session["profile_image_position_x"] = account.get("profile_image_position_x", 50)
    request.session["profile_image_position_y"] = account.get("profile_image_position_y", 50)
    request.session["profile_image_scale"] = account.get("profile_image_scale", 1)
    remove_profile_image_file(previous_path if previous_path != saved_path else None)
    return redirect_to(safe_portal_path(role_key, "profile", message="Profile picture updated."))


@router.post("/portal/{role_key}/profile/picture/remove")
async def portal_profile_picture_remove(request: Request, role_key: str):
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    guard = require_portal_session(request, role_key)
    if guard:
        return guard
    account_id = session_account_id(request)
    if account_id is None:
        return redirect_to(safe_portal_path(role_key, "profile", error="Your session expired. Please sign in again."))
    previous_path = request.session.get("profile_image_path")
    try:
        data.update_account_profile_image(account_id, None)
    except ValueError as exc:
        return redirect_to(safe_portal_path(role_key, "profile", error=str(exc)))
    request.session["profile_image_path"] = ""
    request.session["profile_image_position_x"] = 50
    request.session["profile_image_position_y"] = 50
    request.session["profile_image_scale"] = 1
    remove_profile_image_file(previous_path)
    return redirect_to(safe_portal_path(role_key, "profile", message="Profile picture removed."))
