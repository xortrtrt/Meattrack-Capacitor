from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app import repositories as data
from app import portal_config
from app.business_time import business_now


router = APIRouter()

PROFILE_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
PROFILE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
PROFILE_IMAGE_DIR = Path(__file__).resolve().parents[1] / "static" / "uploads" / "profiles"

PORTAL_TEMPLATES = {
    "owner": "portals/owner.html",
    "team-leader": "portals/team_leader.html",
    "sales-supervisor": "portals/team_leader.html",
    "reseller": "portals/reseller.html",
}

PORTAL_SECTION_LOADERS = {}


def main_app():
    from app import main

    return main


def templates():
    return main_app().templates


def redirect_to(path: str) -> RedirectResponse:
    return main_app().redirect_to(path)


def session_account_id(request: Request) -> int | None:
    return main_app().session_account_id(request)


def session_team_leader_role(request: Request) -> str | None:
    return main_app().session_team_leader_role(request)


def require_portal_session(request: Request, role_key: str) -> RedirectResponse | None:
    return main_app().require_portal_session(request, role_key)


def require_team_leader_role(request: Request, required_role: str) -> RedirectResponse | None:
    return main_app().require_team_leader_role(request, required_role)


def require_email(email: str) -> str:
    return main_app().require_email(email)


def require_date_range(period_start: date, period_end: date) -> None:
    main_app().require_date_range(period_start, period_end)


def send_password_change_otp(**kwargs):
    return main_app().send_password_change_otp(**kwargs)


async def parse_payment_proof_attachments(uploads) -> list[dict]:
    return await main_app().parse_payment_proof_attachments(uploads)


def _profile_image_extension(content_type: str, content: bytes) -> str:
    extension = PROFILE_IMAGE_TYPES.get(content_type)
    if extension == ".jpg" and content.startswith(b"\xff\xd8\xff"):
        return extension
    if extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return extension
    if extension == ".webp" and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return extension
    raise ValueError("Profile picture must be a valid JPG, PNG, or WebP image.")


async def save_profile_image_upload(account_id: int, upload: UploadFile) -> str:
    if not upload or not upload.filename:
        raise ValueError("Choose a profile picture to upload.")
    content = await upload.read(PROFILE_IMAGE_MAX_BYTES + 1)
    if not content:
        raise ValueError("Choose a profile picture to upload.")
    if len(content) > PROFILE_IMAGE_MAX_BYTES:
        raise ValueError("Profile picture must be 2 MB or smaller.")
    content_type = upload.content_type or "application/octet-stream"
    extension = _profile_image_extension(content_type, content)
    PROFILE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"account-{account_id}-{uuid4().hex}{extension}"
    target = PROFILE_IMAGE_DIR / filename
    target.write_bytes(content)
    return f"uploads/profiles/{filename}"


def remove_profile_image_file(profile_image_path: str | None) -> None:
    if not profile_image_path or not profile_image_path.startswith("uploads/profiles/"):
        return
    target = (Path(__file__).resolve().parents[1] / "static" / profile_image_path).resolve()
    try:
        target.relative_to(PROFILE_IMAGE_DIR.resolve())
    except ValueError:
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def safe_portal_path(role_key: str, section: str, message: str = "", error: str = "", **extra_params: str) -> str:
    return main_app().safe_portal_path(role_key, section, message=message, error=error, **extra_params)


def path_with_query(path: str, **params: str) -> str:
    return main_app().path_with_query(path, **params)


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


def product_page(request: Request, page_size: int = 12, active_only: bool = False) -> dict:
    filters = portal_filters(request)
    items = data.list_products(
        q=filters["q"],
        category=filters["type"],
        page=filters["page"],
        page_size=page_size,
        sort=filters["sort"],
        active_only=active_only,
    )
    total = data.count_products(q=filters["q"], category=filters["type"], active_only=active_only)
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
    nav = portal_nav_for_request(role_key, request)
    nav_sections = {item[0]: item for item in nav}
    if section not in nav_sections:
        raise HTTPException(status_code=404)

    context = {
        "request": request,
        "role_key": role_key,
        "portal_css_key": "team-leader" if role_key == "sales-supervisor" else role_key,
        "portal_body_roles": "role-team-leader role-sales-supervisor" if role_key == "sales-supervisor" else f"role-{role_key}",
        "can_create_owner_account": role_key == "owner",
        "can_manage_team_leader_roles": role_key == "owner",
        "show_reseller_team_assignments": role_key == "sales-supervisor",
        "accounts_form_action": f"/portal/{role_key}/accounts",
        "role": portal_role_for_request(role_key, request),
        "nav": nav,
        "section": section,
        "section_title": nav_sections[section][1],
        "message": message,
        "error": error,
        "cart_count": main_app().reseller_cart_count(request) if role_key == "reseller" else 0,
        "notifications": data.list_notifications(role_key, request.session.get("account_id")),
        "unread_notifications": data.unread_notification_count(role_key, request.session.get("account_id")),
    }
    context.update(portal_section_context(role_key, section, request))
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
