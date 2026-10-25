from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import repositories as data
from app.business_time import BUSINESS_ZONE
from app.capabilities import require_capability
from app.portals import shared


router = APIRouter()


class RefundMessagePayload(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def _allowed_role(request: Request, role_key: str) -> tuple[int, object | None]:
    if role_key not in {"owner", "sales-supervisor"}:
        raise HTTPException(status_code=404)
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return 0, guard
    require_capability(
        request,
        "refunds.manage_all" if role_key == "owner" else "refunds.manage_assigned",
        role_key,
    )
    if role_key == "sales-supervisor":
        role_guard = shared.require_team_leader_role(request, "sales-supervisor")
        if role_guard:
            return 0, role_guard
    account_id = shared.session_account_id(request)
    if account_id is None:
        raise HTTPException(status_code=404)
    return account_id, None


def _refund_message_actor(request: Request, role_key: str) -> int:
    if role_key not in {"owner", "sales-supervisor", "reseller"}:
        raise HTTPException(status_code=404)
    guard = shared.require_portal_session(request, role_key)
    if guard:
        raise HTTPException(status_code=401, detail="Sign in to view this refund conversation.")
    if role_key == "sales-supervisor" and shared.require_team_leader_role(request, "sales-supervisor"):
        raise HTTPException(status_code=403, detail="This refund conversation is unavailable.")
    account_id = shared.session_account_id(request)
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in to view this refund conversation.")
    return account_id


def refunds_context(request: Request) -> dict:
    role_key = request.path_params.get("role_key", "owner")
    account_id = shared.session_account_id(request)
    read_only = role_key == "team-leader"
    require_capability(request,"refunds.view_assigned" if read_only else ("refunds.manage_all" if role_key == "owner" else "refunds.manage_assigned"),role_key)
    filters = shared.portal_filters(request)
    status = filters["status"] if filters["status"] in data.REFUND_STATUSES else ""
    total = data.count_order_refunds(role_key, account_id, status=status, q=filters["q"])
    filters["page"] = shared.clamp_page(total, filters["page"], 12)
    items = data.list_order_refunds(
        role_key, account_id, status=status, q=filters["q"], page=filters["page"], page_size=12,
    )
    selected = None
    refund_value = request.query_params.get("refund", "")
    if refund_value:
        try:
            refund_id = shared.optional_positive_id(refund_value)
            selected = data.get_order_refund(refund_id, role_key, account_id) if refund_id else None
        except ValueError as exc:
            raise HTTPException(404) from exc
        if not selected:
            raise HTTPException(404,detail="Refund not found or unavailable to your account.")
    pane = request.query_params.get("pane", "activity").strip().lower()
    if pane == "conversation" and selected and not read_only:
        pass
    elif (
        pane == "transfer"
        and not read_only
        and selected
        and selected.get("status") == "processing"
        and selected.get("processing_by_account_id") == account_id
    ):
        pass
    else:
        pane = "activity"
    return {
        "refunds_page": shared.paged(items, total, filters["page"], 12, q=filters["q"], status=status),
        "refunds": items,
        "refund_counts": data.refund_status_counts(role_key, account_id),
        "selected_refund": selected,
        "refund_statuses": ("pending", "processing", "completed", "failed"),
        "refund_pane": pane,
        "refund_viewer_account_id": account_id,
        "refund_read_only": read_only,
        "operation_key": lambda: str(uuid4()),
        "refund_totals": data.refund_financial_summary(role_key,account_id),
    }


def _refund_redirect(
    role_key: str,
    refund_id: int,
    *,
    message: str = "",
    error: str = "",
    pane: str = "",
):
    return shared.redirect_to(shared.safe_portal_path(
        role_key, "refunds", message=message, error=error, refund=str(refund_id), pane=pane,
    ))


@router.post("/portal/{role_key}/refunds/{refund_id}/start")
async def start_refund(request: Request, role_key: str, refund_id: int, operation_key: str = Form("")):
    account_id, guard = await run_in_threadpool(_allowed_role, request, role_key)
    if guard:
        return guard
    try:
        await run_in_threadpool(data.start_order_refund, refund_id, account_id, role_key, operation_key or None)
    except ValueError as exc:
        return _refund_redirect(role_key, refund_id, error=str(exc))
    return _refund_redirect(role_key, refund_id, message="Refund processing started.")


@router.post("/portal/{role_key}/refunds/{refund_id}/retry")
async def retry_refund(request: Request, role_key: str, refund_id: int, operation_key: str = Form("")):
    account_id, guard = await run_in_threadpool(_allowed_role, request, role_key)
    if guard:
        return guard
    try:
        await run_in_threadpool(data.start_order_refund, refund_id, account_id, role_key, operation_key or None)
    except ValueError as exc:
        return _refund_redirect(role_key, refund_id, error=str(exc))
    return _refund_redirect(role_key, refund_id, message="A new refund attempt was started.")


@router.post("/portal/{role_key}/refunds/{refund_id}/take-over")
async def take_over_refund(request: Request, role_key: str, refund_id: int, reason: str = Form(...)):
    account_id, guard = await run_in_threadpool(_allowed_role, request, role_key)
    if guard:
        return guard
    if role_key != "owner":
        raise HTTPException(status_code=404)
    from app.portals.owner import require_owner_step_up

    step_up = await run_in_threadpool(require_owner_step_up, request, "refunds")
    if step_up:
        return step_up
    try:
        clean_reason = " ".join(reason.strip().split())
        if len(clean_reason) < 5:
            raise ValueError("Enter a reason for taking over this refund.")
        await run_in_threadpool(data.take_over_order_refund, refund_id, account_id)
        await run_in_threadpool(
            data.record_owner_audit,
            account_id,
            "took_over_refund",
            "order_refunds",
            refund_id,
            reason=clean_reason,
            after_state={"processing_by_account_id": account_id},
        )
    except ValueError as exc:
        return _refund_redirect(role_key, refund_id, error=str(exc))
    return _refund_redirect(role_key, refund_id, message="You are now processing this refund.")


@router.post("/portal/{role_key}/refunds/{refund_id}/fail")
async def fail_refund(
    request: Request,
    role_key: str,
    refund_id: int,
    failure_reason: str = Form(...),
    operation_key: str = Form(""),
):
    account_id, guard = await run_in_threadpool(_allowed_role, request, role_key)
    if guard:
        return guard
    try:
        await run_in_threadpool(data.fail_order_refund, refund_id, account_id, role_key, failure_reason, operation_key or None)
    except ValueError as exc:
        return _refund_redirect(role_key, refund_id, error=str(exc), pane="transfer")
    return _refund_redirect(role_key, refund_id, message="Refund attempt marked failed. Its history was preserved.")


@router.post("/portal/{role_key}/refunds/{refund_id}/complete")
async def complete_refund(
    request: Request,
    role_key: str,
    refund_id: int,
    refund_method: str = Form(...),
    transaction_reference: str = Form(...),
    transferred_at: str = Form(...),
    internal_note: str = Form(""),
    proof: UploadFile | None = File(None),
    operation_key: str = Form(""),
):
    account_id, guard = await run_in_threadpool(_allowed_role, request, role_key)
    if guard:
        return guard
    try:
        try:
            transfer_time = datetime.fromisoformat(transferred_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("Enter a valid transfer date and time.") from exc
        if transfer_time.tzinfo is None:
            transfer_time = transfer_time.replace(tzinfo=BUSINESS_ZONE)
        attachments = await shared.parse_payment_proof_attachments([proof] if proof else [])
        if len(attachments) > 1:
            raise ValueError("Attach only one refund screenshot.")
        await run_in_threadpool(
            data.complete_order_refund,
            refund_id, account_id, role_key,
            refund_method=refund_method,
            transaction_reference=transaction_reference,
            transferred_at=transfer_time,
            internal_note=internal_note,
            proof=attachments[0] if attachments else None,
            operation_key=operation_key or None,
        )
    except ValueError as exc:
        return _refund_redirect(role_key, refund_id, error=str(exc), pane="transfer")
    return _refund_redirect(role_key, refund_id, message="Refund completed and the reseller was notified.")


@router.post("/portal/{role_key}/refunds/{refund_id}/messages")
async def send_internal_refund_message(
    request: Request,
    role_key: str,
    refund_id: int,
    message: str = Form(...),
):
    if role_key == "reseller":
        guard = shared.require_portal_session(request, "reseller")
        if guard:
            return guard
        account_id = shared.session_account_id(request)
        if account_id is None:
            raise HTTPException(status_code=404)
        try:
            await run_in_threadpool(data.send_order_refund_message, refund_id, account_id, "reseller", message)
        except ValueError as exc:
            return shared.redirect_to(shared.safe_portal_path(
                "reseller", "inbox", refund=str(refund_id), error=str(exc),
            ))
        return shared.redirect_to(shared.safe_portal_path(
            "reseller", "inbox", refund=str(refund_id),
        ))
    account_id, guard = await run_in_threadpool(_allowed_role, request, role_key)
    if guard:
        return guard
    try:
        await run_in_threadpool(data.send_order_refund_message, refund_id, account_id, role_key, message)
    except ValueError as exc:
        return _refund_redirect(role_key, refund_id, error=str(exc), pane="conversation")
    return _refund_redirect(role_key, refund_id, pane="conversation")


@router.get("/api/portal/{role_key}/refunds/{refund_id}/messages")
def refund_messages(request: Request, role_key: str, refund_id: int, after_id: int = 0):
    account_id = _refund_message_actor(request, role_key)
    try:
        messages = data.list_order_refund_messages(
            refund_id, account_id, role_key, after_id=max(after_id, 0),
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": "conversation_unavailable", "message": str(exc)}, status_code=404,
        )
    return {"messages": messages}


@router.post("/api/portal/{role_key}/refunds/{refund_id}/messages")
def send_refund_message(
    request: Request,
    role_key: str,
    refund_id: int,
    payload: RefundMessagePayload,
):
    account_id = _refund_message_actor(request, role_key)
    try:
        created = data.send_order_refund_message(refund_id, account_id, role_key, payload.message)
        messages = data.list_order_refund_messages(
            refund_id,
            account_id,
            role_key,
            after_id=max(int(created["order_refund_message_id"]) - 1, 0),
        )
    except ValueError as exc:
        return JSONResponse({"error": "message_failed", "message": str(exc)}, status_code=400)
    message = next(
        (
            item for item in messages
            if int(item["order_refund_message_id"]) == int(created["order_refund_message_id"])
        ),
        created,
    )
    return {"message": message}


@router.get("/portal/refund-proofs/{proof_id}")
async def refund_proof(request: Request, proof_id: int):
    role_key = request.session.get("role_key")
    account_id = shared.session_account_id(request)
    proof = await run_in_threadpool(data.get_order_refund_proof, proof_id, role_key, account_id)
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


SECTION_LOADERS = {
    ("team-leader", "refunds"): refunds_context,
    ("owner", "refunds"): refunds_context,
    ("sales-supervisor", "refunds"): refunds_context,
}

