from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app import repositories as data
from app.business_time import business_today
from app.portals import shared


router = APIRouter()


def cart_count(request: Request) -> float:
    account_id = shared.session_account_id(request)
    if account_id is None:
        return 0
    return data.reseller_cart_count(account_id)


def cart_context(request: Request) -> dict:
    products = data.list_products(active_only=True)
    account_id = shared.session_account_id(request)
    items = data.list_reseller_cart_items(account_id) if account_id is not None else []
    total = sum((Decimal(str(item.get("line_total") or 0)) for item in items), Decimal("0.00"))
    return {
        "products": products,
        "cart_items": items,
        "cart_total": total.quantize(Decimal("0.01")),
        "cart_count": sum(item["cart_quantity"] for item in items),
    }


def cart_payload(account_id: int, message: str = "") -> JSONResponse:
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


def dashboard_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    reseller_profile = None
    if account_id is not None:
        try:
            reseller_profile = data.reseller_account_profile(account_id)
        except ValueError:
            reseller_profile = None
    default_end = business_today()
    default_start = default_end - timedelta(days=29)
    start_date = shared.date_filter(request.query_params.get("start_date"), default_start)
    end_date = shared.date_filter(request.query_params.get("end_date"), default_end)
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


def profile_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
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


def order_context(request: Request) -> dict:
    page = shared.product_page(request, page_size=8, active_only=True)
    return {
        "products_page": page,
        "products": page["items"],
        "product_categories": data.product_categories,
    }


def history_context(request: Request) -> dict:
    page = shared.orders_page(request, "reseller", reseller_account_id=shared.session_account_id(request))
    return {"orders_page": page, "orders": page["items"]}


SECTION_LOADERS = {
    ("reseller", "dashboard"): dashboard_context,
    ("reseller", "order"): order_context,
    ("reseller", "cart"): cart_context,
    ("reseller", "history"): history_context,
    ("reseller", "profile"): profile_context,
}


@router.post("/portal/reseller/order")
async def reseller_order(request: Request, product_id: int = Form(...), quantity: str = Form(...), notes: str = Form("")):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "order", error="Your session expired. Please sign in again."))
    try:
        data.add_reseller_cart_item(account_id, product_id, quantity)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("reseller", "order", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("reseller", "order", message="Product added to cart."))


@router.post("/portal/reseller/cart")
async def reseller_cart_update(
    request: Request,
    product_id: int = Form(...),
    quantity: str = Form("0"),
    action: str = Form("update"),
):
    wants_json = request.headers.get("x-requested-with") == "fetch" or "application/json" in request.headers.get("accept", "")
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
        return shared.redirect_to(shared.safe_portal_path("reseller", "cart", error="Your session expired. Please sign in again."))
    if action == "remove" or quantity.strip() == "0":
        data.remove_reseller_cart_item(account_id, product_id)
        if wants_json:
            return cart_payload(account_id, "Product removed from cart.")
        return shared.redirect_to(shared.safe_portal_path("reseller", "cart", message="Product removed from cart."))
    try:
        data.update_reseller_cart_item(account_id, product_id, quantity)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return shared.redirect_to(shared.safe_portal_path("reseller", "cart", error=str(exc)))
    if wants_json:
        return cart_payload(account_id, "Cart updated.")
    return shared.redirect_to(shared.safe_portal_path("reseller", "cart", message="Cart updated."))


@router.post("/portal/reseller/cart/checkout")
async def reseller_cart_checkout(request: Request, notes: str = Form("")):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "cart", error="Your session expired. Please sign in again."))
    try:
        data.checkout_reseller_cart(account_id, notes.strip())
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("reseller", "cart", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("reseller", "history", message="Order submitted for team leader approval."))


@router.post("/portal/reseller/orders/{order_id}/payment-proof")
async def reseller_order_payment_proof(
    request: Request,
    order_id: int,
    payment_proofs: list[UploadFile] = File(default=[]),
):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "history", error="Your session expired. Please sign in again."))
    try:
        attachments = await shared.parse_payment_proof_attachments(payment_proofs)
        count = data.add_order_payment_proofs(account_id, order_id, attachments)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("reseller", "history", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("reseller", "history", message=f"{count} payment proof screenshot uploaded."))


@router.post("/portal/reseller/profile/password")
async def reseller_profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "profile", error="Your session expired. Please sign in again."))
    try:
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
        pending = data.request_reseller_password_change(account_id, current_password, new_password)
        sent, email_message = shared.send_password_change_otp(
            to_email=pending["email"],
            name=pending["name"],
            otp_code=pending["otp_code"],
        )
        if not sent:
            data.cancel_reseller_password_change(account_id, pending.get("otp_id"))
            raise ValueError(email_message)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("reseller", "profile", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("reseller", "profile", otp="1", message="OTP sent to your account email. Enter it below to confirm your password change."))


@router.post("/portal/reseller/profile/password/confirm")
async def reseller_profile_password_confirm(request: Request, otp_code: str = Form(...)):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "profile", error="Your session expired. Please sign in again."))
    try:
        data.confirm_reseller_password_change(account_id, otp_code)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("reseller", "profile", otp="1", error=str(exc)))
    return shared.redirect_to(shared.safe_portal_path("reseller", "profile", message="Password updated."))


@router.post("/portal/reseller/profile")
async def reseller_profile_update(
    request: Request,
    name: str = Form(...),
    business_name: str = Form(...),
    contact_number: str = Form(...),
    address: str = Form(...),
):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "profile", error="Your session expired. Please sign in again."))
    try:
        data.update_reseller_profile(account_id, name, business_name, contact_number, address)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path("reseller", "profile", error=str(exc)))
    request.session["account_name"] = " ".join(name.strip().split())
    return shared.redirect_to(shared.safe_portal_path("reseller", "profile", message="Profile updated."))
