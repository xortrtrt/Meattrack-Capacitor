from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import repositories as data
from app import receipt_ocr
from app.business_time import business_today
from app.portals import shared
from app.repositories.product_returns import return_request_window


router = APIRouter()


def cart_count(request: Request) -> float:
    account_id = shared.session_account_id(request)
    if account_id is None:
        return 0
    return data.reseller_cart_count(account_id)


def cart_summary_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    items = data.list_reseller_cart_items(account_id) if account_id is not None else []
    total = sum((Decimal(str(item.get("line_total") or 0)) for item in items), Decimal("0.00"))
    return {
        "cart_items": items,
        "cart_total": total.quantize(Decimal("0.01")),
        "cart_count": sum(item["cart_quantity"] for item in items),
    }


def cart_context(request: Request) -> dict:
    return cart_summary_context(request)


def checkout_context(request: Request) -> dict:
    context = cart_summary_context(request)
    draft = request.session.pop("checkout_error_draft", None) if request.query_params.get("restore") == "1" else None
    placed_order_id = request.query_params.get("placed")
    context["placed_order_id"] = None
    context["placed_order"] = None
    clean_placed_order_id = shared.optional_positive_id(placed_order_id)
    account_id = shared.session_account_id(request)
    if clean_placed_order_id is not None and account_id is not None:
        confirmation = data.reseller_order_confirmation(account_id, clean_placed_order_id)
        if confirmation:
            context["placed_order_id"] = int(confirmation["order_id"])
            context["placed_order"] = confirmation

    selected_product_ids: list[int] = []
    selected_values = draft.get("product_ids", []) if isinstance(draft, dict) else request.query_params.getlist("product_id")
    for value in selected_values:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id not in selected_product_ids:
            selected_product_ids.append(product_id)

    selected_id_set = set(selected_product_ids)
    checkout_items = [
        item for item in context["cart_items"]
        if int(item["product_id"]) in selected_id_set
    ]
    selected_product_ids = [int(item["product_id"]) for item in checkout_items]
    context.update({
        "checkout_items": checkout_items,
        "checkout_total": sum(
            (Decimal(str(item.get("line_total") or 0)) for item in checkout_items),
            Decimal("0.00"),
        ).quantize(Decimal("0.01")),
        "checkout_count": sum(item["cart_quantity"] for item in checkout_items),
        "selected_product_ids": selected_product_ids,
        "checkout_notes": str(draft.get("notes", "")) if isinstance(draft, dict) else "",
        "checkout_error": str(draft.get("error", "")) if isinstance(draft, dict) else "",
    })
    return context


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
    account_id = shared.session_account_id(request)
    if request.query_params.get("view", "").strip().lower() == "returns":
        from app.portals.product_returns import reseller_returns_context
        context = reseller_returns_context(request)
        pagination = context["return_cases_page"]["pagination"]
        return {**context, "orders": [],
            "orders_page": shared.paged([], pagination["total"], pagination["page"], 12,
                view="returns", q="", sort=""),
        }
    if account_id is not None:
        data.expire_unpaid_orders(account_id=account_id)
    history_view = request.query_params.get("view", "all").strip().lower()
    if history_view not in {
        "all", "to_pay", "pending", "approved", "fulfilled", "refunded", "cancelled",
    }:
        history_view = "all"
    status_override = (
        history_view
        if history_view in {
            "to_pay", "pending", "approved", "fulfilled", "refunded", "cancelled",
        }
        else None
    )
    page = shared.orders_page(
        request,
        "reseller",
        reseller_account_id=account_id,
        status_override=status_override,
    )
    page["filters"]["view"] = history_view
    for order in page["items"]:
        if order.get("status") == "fulfilled":
            order.update(return_request_window(order))
    return {
        "orders_page": page,
        "orders": page["items"],
        "cancellation_reasons": data.RESELLER_ORDER_CANCELLATION_REASONS,
    }


def _group_order_system_messages(
    system_messages: list[dict],
    refund_conversations: list[dict] | None = None,
    selected_refund_id: int | None = None,
) -> list[dict]:
    refunds_by_order: dict[int, list[dict]] = {}
    for refund in refund_conversations or []:
        refunds_by_order.setdefault(int(refund["order_id"]), []).append(refund)
    conversations: dict[str, dict] = {}
    for item in system_messages:
        message_id = int(item["order_system_message_id"])
        order_id = item.get("order_id")
        key = f"order-{order_id}" if order_id is not None else f"message-{message_id}"
        conversation = conversations.setdefault(
            key,
            {
                "key": key,
                "order_id": order_id,
                "latest": item,
                "messages": [],
                "unread_count": 0,
                "severity": item.get("severity", "info"),
                "refunds": refunds_by_order.get(int(order_id), []) if order_id is not None else [],
                "refund": next((r for r in refunds_by_order.get(int(order_id), []) if r["order_refund_id"] == selected_refund_id), next(iter(refunds_by_order.get(int(order_id), [])), None)) if order_id is not None else None,
            },
        )
        conversation["messages"].append(item)
        if not item.get("read_at"):
            conversation["unread_count"] += 1
        if item.get("severity") == "critical":
            conversation["severity"] = "critical"
        if item["created_at"] > conversation["latest"]["created_at"]:
            conversation["latest"] = item

    grouped = list(conversations.values())
    for conversation in grouped:
        conversation["messages"].sort(key=lambda item: item["created_at"])
    grouped.sort(key=lambda conversation: conversation["latest"]["created_at"], reverse=True)
    return grouped


def inbox_context(request: Request) -> dict:
    account_id = shared.session_account_id(request)
    return_contact = None
    return_id = shared.optional_positive_id(request.query_params.get("return_id"))
    if return_id:
        from app.repositories.product_returns import get_product_return
        try:
            return_contact = get_product_return(return_id, account_id)
        except ValueError as exc:
            raise HTTPException(404) from exc
    open_system_message_id = shared.optional_positive_id(request.query_params.get("system_message"))
    open_refund_id = shared.optional_positive_id(request.query_params.get("refund"))
    refund_conversations = data.list_reseller_refund_conversations(account_id)
    if open_refund_id and not any(
        int(item["order_refund_id"]) == open_refund_id
        for item in refund_conversations
    ):
        open_refund_id = None
    system_messages = data.list_order_system_messages(account_id, limit=20)
    if open_refund_id:
        opened_refund = next(
            item for item in refund_conversations
            if int(item["order_refund_id"]) == open_refund_id
        )
        order_id = int(opened_refund["order_id"])
        if not any(int(item["order_id"]) == order_id for item in system_messages):
            linked_order_message = data.get_latest_order_system_message(account_id, order_id)
            if linked_order_message:
                system_messages.insert(0, linked_order_message)
    if open_system_message_id and not any(
        int(item["order_system_message_id"]) == open_system_message_id
        for item in system_messages
    ):
        linked_message = data.get_order_system_message(account_id, open_system_message_id)
        if linked_message:
            system_messages.insert(0, linked_message)
        else:
            open_system_message_id = None
    return {
        "return_contact": return_contact,
        "return_report_issue": bool(return_contact and return_contact.get("replacement_released_at") and request.query_params.get("report_issue") == "1"),
        "system_messages": system_messages,
        "system_conversations": _group_order_system_messages(system_messages, refund_conversations, open_refund_id),
        "system_message_unread_count": data.unread_order_system_message_count(account_id),
        "open_system_message_id": open_system_message_id,
        "open_refund_id": open_refund_id,
    }


SECTION_LOADERS = {
    ("reseller", "dashboard"): dashboard_context,
    ("reseller", "order"): order_context,
    ("reseller", "cart"): cart_context,
    ("reseller", "checkout"): checkout_context,
    ("reseller", "history"): history_context,
    ("reseller", "inbox"): inbox_context,
    ("reseller", "profile"): profile_context,
}


@router.post("/portal/reseller/system-messages/{system_message_id}/read")
async def reseller_system_message_read(request: Request, system_message_id: int):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
    if not data.mark_order_system_message_read(
        shared.session_account_id(request),
        system_message_id,
    ):
        return JSONResponse({"ok": False, "error": "System message not found."}, status_code=404)
    return JSONResponse({"ok": True})


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
@router.post("/portal/reseller/checkout")
async def reseller_cart_checkout(
    request: Request,
    notes: str = Form(""),
    product_id: list[int] | None = Form(None),
):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(shared.safe_portal_path("reseller", "cart", error="Your session expired. Please sign in again."))
    if not product_id:
        return shared.redirect_to(
            shared.safe_portal_path("reseller", "cart", error="Select at least one cart item to continue.")
        )
    selected_product_ids = list(dict.fromkeys(product_id))
    try:
        order = data.checkout_reseller_cart(
            account_id,
            notes.strip(),
            selected_product_ids,
        )
    except ValueError as exc:
        error_section = "checkout" if request.url.path == "/portal/reseller/checkout" else "cart"
        if error_section == "checkout":
            request.session["checkout_error_draft"] = {
                "product_ids": selected_product_ids,
                "notes": notes.strip(),
                "error": str(exc),
            }
            return shared.redirect_to("/portal/reseller/checkout?restore=1")
        return shared.redirect_to(shared.safe_portal_path("reseller", error_section, error=str(exc)))
    return shared.redirect_to(f"/portal/reseller/checkout?placed={int(order['order_id'])}")


@router.post("/portal/reseller/orders/receipt-scan")
@router.post("/portal/reseller/checkout/receipt-scan")
async def reseller_receipt_scan(
    request: Request,
    payment_proofs: list[UploadFile] = File(default=[]),
):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
    account_id = shared.session_account_id(request)
    if account_id is None:
        return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
    try:
        attachments = await shared.parse_payment_proof_attachments(payment_proofs)
        if not attachments:
            raise ValueError("Choose at least one payment receipt screenshot.")
        scan = await run_in_threadpool(receipt_ocr.scan_payment_proofs, attachments)
        scan["scan_token"] = receipt_ocr.create_scan_token(account_id, attachments, scan)
        scan["ok"] = True
        return JSONResponse(scan)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/portal/reseller/orders/{order_id}/payment")
async def reseller_order_payment(
    request: Request,
    order_id: int,
    payment_proofs: list[UploadFile] = File(default=[]),
    receipt_scan_token: str = Form(""),
    transaction_number: str = Form(""),
    payment_date: str = Form(""),
    payment_time: str = Form(""),
    payment_amount: str = Form(""),
    payment_details_confirmed: str = Form(""),
):
    wants_json = "application/json" in request.headers.get("accept", "")
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Your session expired. Please sign in again."}, status_code=401)
        return shared.redirect_to(shared.safe_portal_path("reseller", "history", error="Your session expired. Please sign in again."))
    try:
        attachments = await shared.parse_payment_proof_attachments(payment_proofs)
        if not attachments:
            raise ValueError("Attach at least one proof of payment screenshot.")
        payment_details = receipt_ocr.confirmed_payment_details(
            account_id,
            attachments,
            receipt_scan_token,
            transaction_number,
            payment_date,
            payment_time,
            payment_amount,
            payment_details_confirmed == "yes",
        )
        await run_in_threadpool(data.submit_order_payment, account_id, order_id, attachments, payment_details)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return shared.redirect_to(
            shared.safe_portal_path("reseller", "history", view="to_pay", error=str(exc))
        )
    redirect_url = shared.safe_portal_path(
        "reseller", "history", view="pending",
        message=f"Payment for order #{order_id} was submitted for approval.",
    )
    if wants_json:
        return JSONResponse({"ok": True, "redirect_url": redirect_url})
    return shared.redirect_to(redirect_url)


@router.post("/portal/reseller/orders/{order_id}/cancel")
def reseller_cancel_order(
    request: Request,
    order_id: int,
    cancellation_reason: Annotated[str, Form()] = "",
    cancellation_details: Annotated[str, Form()] = "",
):
    guard = shared.require_portal_session(request, "reseller")
    if guard:
        return guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        return shared.redirect_to(
            shared.safe_portal_path(
                "reseller", "history", error="Your session expired. Please sign in again."
            )
        )
    try:
        data.cancel_reseller_order(
            account_id,
            order_id,
            cancellation_reason,
            cancellation_details,
        )
    except ValueError as exc:
        return shared.redirect_to(
            shared.safe_portal_path("reseller", "history", view="to_pay", error=str(exc))
        )
    return shared.redirect_to(
        shared.safe_portal_path(
            "reseller",
            "history",
            view="cancelled",
            message=f"Order #{order_id} was cancelled.",
        )
    )


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
