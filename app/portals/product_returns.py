"""Role-scoped return cases and expired-stock custody screens."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from psycopg2.errors import UniqueViolation
from starlette.concurrency import run_in_threadpool

from app.capabilities import capability_role_key, require_capability
from app.database import fetch_all, fetch_one
from app.repositories import product_returns as returns
from app.repositories import return_customer
from app.repositories import replacement_workflow, replacement_messages
from app import receipt_ocr
from app.return_experience import QUEUES, normalize_queue
from app.repositories.inventory import list_products
from app.inventory_measurements import parse_whole_packs
from app.portals import shared

router = APIRouter()


@router.post("/portal/reseller/returns/replacement-preview")
async def replacement_preview(request: Request):
    guard = await run_in_threadpool(_guard,request,"reseller","returns.create")
    if guard:
        return guard
    form = await request.form()
    try:
        return await run_in_threadpool(replacement_workflow.preview,int(str(form.get("order_id","0"))),
            shared.session_account_id(request),_pairs(form,"allocation_id","quantity"),
            _pairs(form,"replacement_product_id","replacement_quantity"))
    except ValueError as exc:
        return JSONResponse({"error":str(exc)},status_code=400)


@router.post("/portal/reseller/returns/{return_id}/receipt-scan")
async def replacement_receipt_scan(request: Request, return_id: int):
    guard = await run_in_threadpool(_guard,request,"reseller","returns.create")
    if guard:
        return guard
    actor = shared.session_account_id(request)
    try:
        case = await run_in_threadpool(returns.get_product_return,return_id,actor)
        if case["resolution"] != "replace" or not case["sales_authorized"] or not case["verified_at"] or case["payment_verified_at"]:
            raise ValueError("Complete receipt and approval are required before payment.")
        form = await request.form()
        attachments = await shared.parse_payment_proof_attachments([u for u in form.getlist("payment_proofs") if getattr(u,"filename","")])
        if not attachments:
            raise ValueError("Choose a payment receipt screenshot.")
        scan = await run_in_threadpool(receipt_ocr.scan_payment_proofs,attachments)
        scan["scan_token"] = receipt_ocr.create_scan_token(actor,attachments,scan,context=f"replacement:{return_id}")
        scan["ok"] = True
        return JSONResponse(scan,headers={"Cache-Control":"no-store"})
    except ValueError as exc:
        return JSONResponse({"error":str(exc)},status_code=400)


@router.get("/api/portal/{role_key}/returns/{return_id}/messages")
async def replacement_conversation(request: Request, role_key: str, return_id: int, after_id: int = 0):
    guard = await run_in_threadpool(_guard,request,role_key,"returns.view")
    if guard:
        return guard
    try:
        return await run_in_threadpool(replacement_messages.messages,return_id,shared.session_account_id(request),after_id)
    except ValueError:
        raise HTTPException(404,detail="Replacement conversation unavailable.") from None


@router.post("/api/portal/{role_key}/returns/{return_id}/messages")
async def replacement_conversation_send(request: Request, role_key: str, return_id: int):
    guard = await run_in_threadpool(_guard,request,role_key,"returns.view")
    if guard:
        return guard
    form = await request.form()
    try:
        return await run_in_threadpool(replacement_messages.send,return_id,shared.session_account_id(request),
            str(form.get("content","")),str(form.get("client_message_id","")))
    except ValueError as exc:
        return JSONResponse({"error":str(exc)},status_code=400)


@router.post("/api/portal/{role_key}/returns/{return_id}/read")
async def replacement_conversation_read(request: Request, role_key: str, return_id: int):
    guard = await run_in_threadpool(_guard,request,role_key,"returns.view")
    if guard:
        return guard
    try:
        payload = await request.json()
        unread = await run_in_threadpool(replacement_messages.mark_read,return_id,shared.session_account_id(request),int(payload.get("through_id",0)))
    except (ValueError,TypeError):
        raise HTTPException(404) from None
    return {"ok":True,"unread_count":unread}


def _guard(request: Request, role_key: str, capability: str):
    if role_key not in {"owner", "team-leader", "sales-supervisor", "reseller"}:
        raise HTTPException(404)
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    require_capability(request, capability, role_key)
    return None


def _returns_context(request: Request) -> dict:
    actor_id = shared.session_account_id(request)
    role = capability_role_key(request)
    inventory_only = role == "team-leader:inventory"
    queue = normalize_queue(request.query_params.get("queue")) if (
        role == "team-leader:sales" or (role == "sales-supervisor" and "queue" in request.query_params)
    ) else None
    case_options = {"queue": queue} if queue is not None else {}
    dashboard_queue = request.query_params.get("dashboard_queue", "")
    if dashboard_queue:
        from app.repositories.dashboard_queues import ALLOWED, LABELS
        queue_role = "inventory" if inventory_only else role
        if dashboard_queue not in ALLOWED.get(queue_role, set()):
            raise HTTPException(400, "This dashboard queue is not available for your role.")
        case_options["dashboard_queue"] = dashboard_queue
    if role == "owner" and request.query_params.get("owner_only") == "1":
        case_options["owner_only"] = True
    if role == "reseller" and request.query_params.get("source_order"):
        case_options["order_id"] = shared.optional_positive_id(request.query_params.get("source_order")) or 0
    inventory_search = request.query_params.get("search", "").strip()[:100] if inventory_only else ""
    updated_from = updated_to = None
    if inventory_only:
        try:
            updated_from = date.fromisoformat(request.query_params["updated_from"]) if request.query_params.get("updated_from") else None
            updated_to = date.fromisoformat(request.query_params["updated_to"]) if request.query_params.get("updated_to") else None
        except ValueError as exc:
            raise HTTPException(400, "Choose valid dates for the updated date range.") from exc
        if updated_from and updated_to and updated_from > updated_to:
            raise HTTPException(400, "The start date must be on or before the end date.")
    cases = returns.list_product_returns(actor_id, request.query_params.get("status", ""),page=shared.requested_page(request.query_params.get("page")),reconciliation_only=request.query_params.get("reconciliation")=="1",search=inventory_search,updated_from=updated_from,updated_to=updated_to, **case_options)
    case_id = shared.optional_positive_id(request.query_params.get("case"))
    selected = None
    if case_id:
        try:
            selected = returns.get_product_return(case_id, actor_id)
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
    order_id = shared.optional_positive_id(request.query_params.get("order"))
    return_order = returns.reseller_return_order(order_id, actor_id) if order_id and role == "reseller" else None
    if order_id and role == "reseller" and not return_order:
        raise HTTPException(404, detail="Order not found or unavailable to your account.")
    available_returns = returns.returnable_allocations(order_id, actor_id) if return_order else []
    legacy = []
    if role in {"owner", "team-leader:inventory"}:
        legacy = fetch_all("""SELECT oi.order_id,oi.order_item_id,oi.product_id,oi.quantity,i.name,
            oi.quantity-COALESCE((SELECT SUM(quantity) FROM fulfillment_allocations a WHERE a.order_item_id=oi.order_item_id),0) unresolved_quantity
            FROM order_items oi JOIN orders o USING(order_id) JOIN inventory_items i ON i.item_id=oi.product_id
            WHERE o.status='fulfilled' AND o.order_type='reseller' AND oi.quantity>COALESCE((SELECT SUM(quantity) FROM fulfillment_allocations a WHERE a.order_item_id=oi.order_item_id),0)
            ORDER BY oi.order_id DESC LIMIT 50;""")
    legacy_batches: dict[int, list[dict]] = {}
    if legacy:
        for batch in fetch_all(
            "SELECT batch_id,item_id,batch_code,expiry_date FROM inventory_batches WHERE item_id=ANY(%s) ORDER BY item_id,expiry_date,batch_id;",
            (list({line["product_id"] for line in legacy}),),
        ):
            legacy_batches.setdefault(batch["item_id"], []).append(batch)
    can_review = role in {"owner", "team-leader:sales", "sales-supervisor"}
    if can_review and selected and role != "owner" and (
        (role == "team-leader:sales" and selected.get("handed_off_at")) or
        (role == "sales-supervisor" and selected.get("handed_off_at") and selected.get("processing_supervisor_account_id") != actor_id) or
        (selected["team_leader_account_id"] != actor_id and selected.get("processing_supervisor_account_id") != actor_id)
    ):
        can_review = False
    return {
        "dashboard_queue_label": LABELS[dashboard_queue] if dashboard_queue else "",
        "dashboard_queue_clear_url": str(request.url.remove_query_params(["dashboard_queue", "page", "case"])),
        "return_queue": queue,
        "return_queue_options": QUEUES,
        "return_queue_counts": returns.return_queue_counts(actor_id) if queue is not None else {},
        "return_cases": cases,
        "return_cases_page": shared.paged(cases,cases[0]["list_total"] if cases else 0,cases[0]["list_page"] if cases else 1,12,status=request.query_params.get("status","")),
        "return_case": selected,
        "return_order_id": order_id,
        "return_order": return_order,
        "returnable_batches": [row for row in available_returns if row["returnable_quantity"] > 0],
        "replacement_products": list_products(active_only=True) if can_review or role=="reseller" else [],
        "return_inventory_only": inventory_only,
        "return_inventory_summary": returns.inventory_return_totals(actor_id) if inventory_only else {},
        "return_inventory_search": inventory_search,
        "return_inventory_updated_from": updated_from.isoformat() if updated_from else "",
        "return_inventory_updated_to": updated_to.isoformat() if updated_to else "",
        "return_summary": returns.return_settlement_totals(actor_id) if role in {"team-leader:sales", "sales-supervisor"} else {},
        "operation_key": lambda: str(uuid4()),
        "return_is_reseller": role == "reseller",
        "return_can_review": can_review,
        "return_can_authorize": bool(can_review and selected and selected["status"] == "settlement_pending"
            and selected.get("verified_at") and selected.get("verified_by_account_id") and not selected.get("sales_authorized")),
        "return_can_receive": role in {"owner", "team-leader:inventory"},
        "legacy_lines": legacy,
        "legacy_batches": legacy_batches,
    }


def reseller_returns_context(request: Request) -> dict:
    context = _returns_context(request)
    return {**context, "return_view": "reseller",
        "return_request_open": bool(context["return_order_id"] or request.query_params.get("request") == "1"),
        "return_order_options": returns.reseller_return_orders(shared.session_account_id(request)) if not context["return_order"] else [],
    }


def sales_returns_context(request: Request) -> dict:
    return {**_returns_context(request), "return_view": "sales"}


def inventory_returns_context(request: Request) -> dict:
    return {**_returns_context(request), "return_view": "inventory"}


def returns_context(request: Request) -> dict:
    role = capability_role_key(request)
    if role == "reseller":
        return reseller_returns_context(request)
    if role == "team-leader:inventory":
        return inventory_returns_context(request)
    return sales_returns_context(request)


def expired_context(request: Request) -> dict:
    workspace = returns.expired_stock_workspace()
    batch_id = shared.optional_positive_id(request.query_params.get("batch_id"))
    focus = returns.expired_pullout_progress(batch_id) if batch_id else None
    if "batch_id" in request.query_params and not focus:
        raise HTTPException(404, "Expired batch was not found.")
    return {
        **workspace,
        "pullout_focus": focus,
        "pullout_from_alerts": request.query_params.get("from_alerts") == "1",
        "return_holdings": [row for row in workspace["holdings"] if row["source"] == "return"],
        "warehouse_holdings": [row for row in workspace["holdings"] if row["source"] != "return"],
        "operation_key": lambda: str(uuid4()),
    }


def product_details_context(request: Request) -> dict:
    product_id = shared.optional_positive_id(request.query_params.get("product_id"))
    product = (
        returns.product_lifecycle_details(
            product_id,
            batch_page=shared.requested_page(request.query_params.get("batch_page")),
            movement_page=shared.requested_page(request.query_params.get("movement_page")),
        )
        if product_id
        else None
    )
    if not product:
        raise HTTPException(404)
    base_url = request.url.include_query_params(
        batch_page=product["batch_pagination"]["page"],
        movement_page=product["movement_pagination"]["page"],
    )
    for key, parameter, anchor in (
        ("batch_pagination", "batch_page", "product-batches"),
        ("movement_pagination", "movement_page", "product-movements"),
    ):
        pagination = product[key]
        page, page_size, total = pagination["page"], pagination["page_size"], pagination["total"]
        first_page = max(1, min(page - 2, pagination["total_pages"] - 4))
        pagination.update(
            start=(page - 1) * page_size + 1 if total else 0,
            end=min(page * page_size, total),
            previous_url=str(base_url.include_query_params(**{parameter: page - 1}).replace(fragment=anchor)),
            next_url=str(base_url.include_query_params(**{parameter: page + 1}).replace(fragment=anchor)),
            links=[
                {
                    "page": number,
                    "url": str(base_url.include_query_params(**{parameter: number}).replace(fragment=anchor)),
                }
                for number in range(first_page, min(pagination["total_pages"], first_page + 4) + 1)
            ],
        )
    from uuid import uuid4
    return {"lifecycle_product": product, "new_reference": lambda: str(uuid4())}


def _pairs(form, id_name: str, quantity_name: str) -> list[tuple[int, str]]:
    ids, quantities = form.getlist(id_name), form.getlist(quantity_name)
    if len(ids) != len(quantities):
        raise ValueError("The item quantities are incomplete.")
    result = []
    for item, quantity in zip(ids, quantities):
        if not str(item).strip() or not str(quantity).strip() or str(quantity).strip() == "0":
            continue
        result.append((int(str(item)), str(quantity)))
    return result


@router.get("/portal/{role_key}/refunds/returns/actions/{action}", include_in_schema=False)
@router.get("/portal/{role_key}/returns/actions/{action}", include_in_schema=False)
def return_action_page(request: Request, role_key: str, action: str):
    """Recover a refreshed form-action address without performing an action."""
    if action not in {
        "submit", "approve", "reject", "receive", "verify", "discrepancy", "accept",
        "reserve", "release", "payment", "verify-payment", "legacy", "proceed", "inspect",
        "acknowledge-replacement", "resolve-issue", "delivered", "convert-to-refund",
    }:
        raise HTTPException(404)
    guard = _guard(request, role_key, "returns.view")
    if guard:
        return guard
    sales_workspace = role_key == "team-leader" and shared.session_team_leader_role(request) == "sales"
    if "/refunds/returns/actions/" in request.url.path and not sales_workspace:
        raise HTTPException(404)
    destination = "history" if role_key == "reseller" else "returns"
    case_id = shared.optional_positive_id(request.query_params.get("case"))
    params = {"case": str(case_id)} if case_id else {}
    if role_key == "reseller" or sales_workspace:
        params["view"] = "returns"
    return shared.redirect_to(shared.safe_portal_path(role_key, destination, **params))


@router.post("/portal/{role_key}/refunds/returns/actions/{action}")
@router.post("/portal/{role_key}/returns/actions/{action}")
async def return_action(request: Request, role_key: str, action: str):
    if "/refunds/returns/actions/" in request.url.path and not (
        role_key == "team-leader" and shared.session_team_leader_role(request) == "sales"
    ):
        raise HTTPException(404)
    capabilities = {
        "submit": "returns.create",
        "approve": "returns.review",
        "reject": "returns.review",
        "receive": "returns.receive",
        "verify": "returns.receive",
        "discrepancy": "returns.receive",
        "accept": "returns.create",
        "reserve": "returns.receive",
        "release": "returns.receive",
        "payment": "returns.create",
        "verify-payment": "returns.review",
        "legacy": "returns.receive",
        "proceed": "returns.authorize",
        "inspect": "returns.receive",
        "acknowledge-replacement": "returns.create",
        "resolve-issue": "returns.review",
        "delivered": "returns.authorize",
        "convert-to-refund": "returns.authorize",
    }
    if action not in capabilities:
        raise HTTPException(404)
    guard = await run_in_threadpool(_guard, request, role_key, capabilities[action])
    if guard:
        return guard
    form = await request.form()
    actor_id = shared.session_account_id(request)
    return_id = shared.optional_positive_id(str(form.get("return_id", "")))
    params = {"case": str(return_id)} if return_id else {}
    sales_workspace = role_key == "team-leader" and shared.session_team_leader_role(request) == "sales"
    destination = "history" if role_key == "reseller" else "returns"
    try:
        if action == "convert-to-refund":
            await run_in_threadpool(returns.convert_product_return_to_refund, return_id, actor_id,
                str(form.get("remarks", "")), str(form.get("customer_message", "")),
                str(form.get("operation_key", "")), str(form.get("review_version", "")),
                unreceived_payment=form.get("unreceived_payment") == "yes")
        elif action == "delivered":
            await run_in_threadpool(replacement_workflow.deliver,return_id,actor_id,str(form.get("remarks","")))
        elif action == "acknowledge-replacement":
            await run_in_threadpool(return_customer.acknowledge_replacement, return_id, actor_id)
        elif action == "resolve-issue":
            await run_in_threadpool(return_customer.resolve_followup, return_id, actor_id,
                int(str(form.get("followup_id", "0"))), str(form.get("customer_message", "")))
        elif action == "submit":
            uploads = [u for u in form.getlist("evidence") if getattr(u, "filename", "")]
            if len(uploads) > 3:
                raise ValueError("Attach up to 3 supporting photos only.")
            attachments = await shared.parse_payment_proof_attachments(uploads)
            return_id = await run_in_threadpool(
                returns.create_product_return,
                int(str(form.get("order_id", "0"))),
                actor_id,
                str(form.get("resolution", "")),
                _pairs(form, "allocation_id", "quantity"),
                str(form.get("remarks", "")),
                attachments,
                str(form.get("operation_key", "")) or None,
                **({"replacement_items":_pairs(form,"replacement_product_id","replacement_quantity"),
                    "comparison_token":str(form.get("comparison_token",""))} if form.get("resolution")=="replace" else {}),
            )
            params = {"case": str(return_id)}
        elif action == "proceed":
            await run_in_threadpool(returns.authorize_return_settlement, return_id, actor_id,
                str(form.get("remarks", "")), str(form.get("operation_key", "")))
        elif action in {"approve", "reject"}:
            await run_in_threadpool(
                returns.review_product_return,
                return_id,
                actor_id,
                action == "approve",
                str(form.get("remarks", "")),
                _pairs(form, "product_id", "replacement_quantity"),
                _pairs(form, "revised_line_id", "approved_quantity"),
                str(form.get("operation_key", "")) or None,
                customer_message=str(form.get("customer_message", "")),
            )
        elif action == "inspect":
            outcome = str(form.get("outcome", ""))
            review_version = str(form.get("review_version", ""))
            if not review_version:
                raise ValueError("Reload this case before recording the receipt decision.")
            if outcome == "complete" and form.get("proof_matches") != "1":
                raise ValueError("Confirm that every approved product, quantity, batch, and supporting photo matches.")
            remarks = ("All approved products, quantities, batches, and supporting proof matched in full."
                       if outcome == "complete" else str(form.get("remarks", "")))
            await run_in_threadpool(returns.inspect_product_return, return_id, actor_id, outcome,
                remarks, str(form.get("operation_key", "")), review_version)
        elif action == "receive":
            case = await run_in_threadpool(returns.get_product_return, return_id, actor_id)
            lines = _pairs(form, "line_id", "quantity")
            supplied = {line_id: parse_whole_packs(quantity) for line_id, quantity in lines}
            outstanding = {line["return_line_id"]: line["quantity"] - line["received_quantity"]
                           for line in case["lines"] if line["quantity"] > line["received_quantity"]}
            if not supplied or len(supplied) != len(lines) or supplied != outstanding:
                raise ValueError("Partial receipts are no longer accepted. Confirm a complete match or report a mismatch to Sales.")
            await run_in_threadpool(returns.inspect_product_return, return_id, actor_id, "complete",
                str(form.get("remarks", "")), str(form.get("operation_key", "")))
        elif action == "discrepancy":
            await run_in_threadpool(returns.inspect_product_return, return_id, actor_id, "mismatch",
                str(form.get("remarks", "")), str(form.get("operation_key", "")) or str(uuid4()))
        elif action == "verify":
            await run_in_threadpool(returns.inspect_product_return, return_id, actor_id, "complete",
                str(form.get("remarks", "")), str(form.get("operation_key", "")))
        elif action == "accept":
            await run_in_threadpool(returns.accept_replacement_quote, return_id, actor_id, int(str(form.get("revision", "0"))))
        elif action == "reserve":
            ready = await run_in_threadpool(returns.reserve_replacement, return_id, actor_id, str(form.get("remarks", "")))
            if ready is False:
                return shared.redirect_to(shared.safe_portal_path(role_key,destination,case=str(return_id),message="Waiting for valid stock. The accepted selection and verified payment are preserved."))
        elif action == "release":
            await run_in_threadpool(returns.release_replacement, return_id, actor_id, str(form.get("remarks", "")))
        elif action == "payment":
            attachments = await shared.parse_payment_proof_attachments(
                [u for u in form.getlist("payment_proofs") if getattr(u, "filename", "")]
            )
            case = await run_in_threadpool(returns.get_product_return,return_id,actor_id)
            details = receipt_ocr.confirmed_payment_details(actor_id,attachments,str(form.get("receipt_scan_token","")),
                str(form.get("transaction_number","")),str(form.get("payment_date","")),str(form.get("payment_time","")),
                str(form.get("payment_amount","")),form.get("payment_details_confirmed")=="yes",context=f"replacement:{return_id}")
            details.update(payment_date=str(form.get("payment_date", "")), payment_time=str(form.get("payment_time", "")))
            await run_in_threadpool(returns.record_return_payment, return_id, actor_id, attachments, payment_details=details)
        elif action == "verify-payment":
            case = await run_in_threadpool(returns.get_product_return, return_id, actor_id)
            submitted = case.get("replacement_payment_details") or {}
            if not submitted.get("transaction_number") or submitted.get("revision") != case["quote_revision"]:
                raise ValueError("The reseller must resubmit the receipt with confirmed payment details before verification.")
            if form.get("funds_received") != "yes":
                raise ValueError("Confirm receipt of funds in the payment provider before verifying this payment.")
            review_version = str(form.get("payment_review_version", ""))
            if not review_version:
                raise ValueError("Reload the case and review the current receipt before confirming funds.")
            await run_in_threadpool(
                returns.verify_return_payment,
                return_id,
                actor_id,
                str(submitted.get("transaction_number", "")),
                str(submitted.get("payment_amount", "")),
                str(form.get("remarks", "")),
                funds_received=form.get("funds_received")=="yes",
                review_version=review_version,
            )
        elif action == "legacy":
            await run_in_threadpool(
                returns.verify_legacy_allocation,
                int(str(form.get("order_id", "0"))),
                int(str(form.get("order_item_id", "0"))),
                int(str(form.get("batch_id", "0"))),
                str(form.get("quantity", "")),
                actor_id,
                str(form.get("remarks", "")),
            )
    except (ValueError, UniqueViolation) as exc:
        if action == "submit":
            params = {"order": str(form.get("order_id", ""))}
        error = (
            str(exc)
            if isinstance(exc, ValueError)
            else "This operation or reference has already been recorded. Refresh the case."
        )
        if capability_role_key(request) == "team-leader:inventory" and any(
            word in error.lower() for word in ("payment", "refund", "credit", "price", "quote")
        ):
            error = "Supervisor/Owner clearance is required. Ask the assigned processor to review this return before stock release."
        if action in {"submit", "approve", "reject", "convert-to-refund"} and "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": error}, status_code=422)
        return shared.redirect_to(shared.safe_portal_path(role_key, destination,
            error=error, **({"view": "returns", **params} if role_key == "reseller" or sales_workspace else params)))
    target = shared.safe_portal_path(role_key, destination,
        message="Return action recorded.", **({"view": "returns", **params} if role_key == "reseller" or sales_workspace else params))
    if action in {"submit", "approve", "reject", "convert-to-refund"} and "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"redirect": target})
    return shared.redirect_to(target)


@router.post("/portal/{role_key}/expired-stock/actions/{action}")
async def expired_action(request: Request, role_key: str, action: str):
    guard = await run_in_threadpool(_guard, request, role_key, "inventory.manage")
    if guard:
        return guard
    form = await request.form()
    actor_id = shared.session_account_id(request)
    focus_params = {}
    if action == "pullout" and form.get("from_alerts") == "1":
        batch_id = shared.optional_positive_id(str(form.get("batch_id", "")))
        if batch_id:
            focus_params = {"batch_id": str(batch_id), "from_alerts": "1"}
    try:
        common = {
            "actor_id": actor_id,
            "quantity": str(form.get("quantity", "")),
            "remarks": str(form.get("remarks", "")),
            "operation_key": str(form.get("operation_key", "")),
        }
        if action == "pullout":
            await run_in_threadpool(
                returns.confirm_expired_pullout,
                int(str(form.get("batch_id", "0"))), location=returns.EXPIRED_HOLDING_LOCATION, **common
            )
        elif action == "dispose":
            await run_in_threadpool(
                returns.dispose_expired_stock,
                int(str(form.get("holding_id", "0"))), method=None, **common
            )
        else:
            raise HTTPException(404)
    except (ValueError, UniqueViolation) as exc:
        return shared.redirect_to(
            shared.safe_portal_path(
                role_key,
                "expired-stock",
                error=str(exc) if isinstance(exc, ValueError) else "This operation has already been recorded.",
                **focus_params,
            )
        )
    return shared.redirect_to(shared.safe_portal_path(
        role_key, "expired-stock", message="Physical pull-out recorded." if focus_params else "Stock custody updated.",
        **focus_params,
    ))


@router.get("/portal/return-attachments/{attachment_id}")
def return_attachment(request: Request, attachment_id: int):
    actor_id = shared.session_account_id(request)
    if not actor_id:
        raise HTTPException(404)
    attachment = fetch_one("SELECT * FROM return_attachments WHERE attachment_id=%s;", (attachment_id,))
    if not attachment:
        raise HTTPException(404)
    try:
        case = returns.get_product_return(attachment["return_id"], actor_id)
        if attachment["purpose"] == "payment" and not any(
            row["attachment_id"] == attachment_id for row in case["attachments"]
        ):
            raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(404) from exc
    return Response(
        content=bytes(attachment["content"]),
        media_type=attachment["content_type"],
        headers={"Cache-Control": "private, no-store"},
    )


SECTION_LOADERS = {
    (role, "returns"): returns_context for role in ("owner", "team-leader", "sales-supervisor", "reseller")
}
SECTION_LOADERS.update({(role, "expired-stock"): expired_context for role in ("owner", "team-leader")})
SECTION_LOADERS.update({(role, "product-details"): product_details_context for role in ("owner", "team-leader")})
