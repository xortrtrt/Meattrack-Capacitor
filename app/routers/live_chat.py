from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from pydantic import ValidationError
from app import internal_conversation_media, conversation_notifications
from pydantic import BaseModel, Field

from app import reseller_messaging
from app import repositories as data
from app.portal_dependencies import session_account_id, session_team_leader_role
from app.routers._shared import rate_limit_response
from app.security_controls import (
    RateLimitExceeded,
    enforce_live_message,
)


router = APIRouter()


class ResellerMessagePayload(BaseModel):
    message: str = Field(default="", max_length=500)
    client_message_id: uuid.UUID
    order_id: int | None = Field(default=None, gt=0)
    return_id: int | None = Field(default=None, gt=0)
    report_issue: bool = False


def _sales_live_account(request: Request) -> int:
    account_id = session_account_id(request)
    if request.session.get("role_key") not in {"team-leader", "sales-supervisor"} or account_id is None:
        raise HTTPException(status_code=401, detail="Sign in as a sales team leader.")
    if session_team_leader_role(request) not in {"sales", "sales-supervisor"}:
        raise HTTPException(status_code=403, detail="Live chat is available only to sales team leaders.")
    return account_id


def _reseller_message_account(request: Request) -> int:
    account_id = session_account_id(request)
    if request.session.get("role_key") != "reseller" or account_id is None:
        raise HTTPException(status_code=401, detail="Sign in as a reseller.")
    return account_id


@router.get("/api/portal/reseller/inbox")
def reseller_inbox_workspace(request: Request, after_id: int = 0):
    account_id = _reseller_message_account(request)
    return reseller_messaging.reseller_workspace(account_id, after_id=after_id)


@router.get("/api/portal/reseller/inbox/orders")
def reseller_message_orders(request: Request):
    from app.reseller_contact_history import orders
    return {"orders": orders(_reseller_message_account(request))}


@router.get("/api/portal/reseller/inbox/orders/{order_id}")
def reseller_message_order_card(request: Request, order_id: int):
    from app.reseller_contact_history import order_card
    try:
        return order_card(_reseller_message_account(request), order_id)
    except ValueError as exc:
        raise HTTPException(404, detail="Order unavailable.") from exc


@router.get("/api/portal/reseller/inbox/contacts/{return_id}")
def reseller_contact_history(request: Request, return_id: int, after_id: int = 0, direct_after_id: int = 0):
    from app.reseller_contact_history import workspace
    try:
        return workspace(_reseller_message_account(request), return_id, after_id, direct_after_id)
    except ValueError as exc:
        raise HTTPException(404, detail="This conversation is unavailable.") from exc


async def _internal_message_payload(request: Request) -> tuple[ResellerMessagePayload,list[dict]]:
    try:
        if request.headers.get("content-type","").startswith("multipart/form-data"):
            form = await request.form(max_files=3,max_fields=10)
            payload = ResellerMessagePayload.model_validate(dict(form))
            attachments = await internal_conversation_media.parse_attachments([f for f in form.getlist("attachments") if isinstance(f,UploadFile)])
        else:
            payload = ResellerMessagePayload.model_validate(await request.json())
            attachments = []
    except (ValidationError,ValueError) as exc:
        raise HTTPException(400,detail="Check your message and attachments. " + str(exc)) from exc
    return payload,attachments


class MessageReadPayload(BaseModel):
    through_id: int = Field(default=0,ge=0,le=9223372036854775807)


@router.post("/api/portal/reseller/inbox/messages")
async def reseller_inbox_send(request: Request):
    account_id = _reseller_message_account(request)
    payload,attachments = await _internal_message_payload(request)
    try:
        enforce_live_message(request, account_id=account_id)
        message = await run_in_threadpool(reseller_messaging.send_from_reseller,account_id,
            str(payload.client_message_id),payload.message,return_id=payload.return_id,
            report_issue=payload.report_issue,attachments=attachments,order_id=payload.order_id)
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        return JSONResponse({"error":"message_failed","message":str(exc)},status_code=400)
    return {"message":None,**message} if message.get("followup_saved") else {"message":message}


@router.post("/api/portal/reseller/inbox/read")
def reseller_inbox_read(request: Request, payload: MessageReadPayload):
    account_id = _reseller_message_account(request)
    return {"read_count": reseller_messaging.mark_read_for_reseller(account_id,payload.through_id)}


@router.delete("/api/portal/reseller/inbox")
def reseller_inbox_delete(request: Request):
    account_id = _reseller_message_account(request)
    return {"deleted": reseller_messaging.delete_for_reseller(account_id)}


@router.get("/api/portal/live-chat/resellers")
def sales_reseller_conversations(request: Request):
    account_id = _sales_live_account(request)
    return {"conversations": reseller_messaging.leader_conversations(account_id)}


@router.get("/api/portal/live-chat/resellers/{reseller_account_id}")
def sales_reseller_thread(request: Request, reseller_account_id: int, after_id: int = 0):
    account_id = _sales_live_account(request)
    try:
        return reseller_messaging.leader_thread(account_id, reseller_account_id, after_id=after_id)
    except ValueError as exc:
        return JSONResponse({"error": "conversation_unavailable", "message": str(exc)}, status_code=404)


@router.post("/api/portal/live-chat/resellers/{reseller_account_id}/messages")
async def sales_reseller_send(request: Request, reseller_account_id: int):
    account_id = _sales_live_account(request)
    payload,attachments = await _internal_message_payload(request)
    try:
        if payload.report_issue:
            raise ValueError("Only the reseller can report a replacement issue.")
        enforce_live_message(request, account_id=account_id)
        message = await run_in_threadpool(reseller_messaging.send_from_leader,account_id,
            reseller_account_id,str(payload.client_message_id),payload.message,
            return_id=payload.return_id,attachments=attachments)
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        return JSONResponse({"error":"message_failed","message":str(exc)},status_code=400)
    return {"message":message}


@router.post("/api/portal/live-chat/resellers/{reseller_account_id}/read")
def sales_reseller_read(request: Request, reseller_account_id: int, payload: MessageReadPayload):
    account_id = _sales_live_account(request)
    try:
        return {"read_count": reseller_messaging.mark_read_for_leader(account_id,reseller_account_id,payload.through_id)}
    except ValueError as exc:
        return JSONResponse({"error": "conversation_unavailable", "message": str(exc)}, status_code=404)


@router.delete("/api/portal/live-chat/resellers/{reseller_account_id}")
def sales_reseller_delete(request: Request, reseller_account_id: int):
    account_id = _sales_live_account(request)
    return {"deleted": reseller_messaging.delete_for_leader(account_id, reseller_account_id)}


@router.get("/api/portal/conversations/unread")
def internal_unread(request: Request):
    role_key = request.session.get("role_key")
    account_id = _reseller_message_account(request) if role_key == "reseller" else _sales_live_account(request)
    return JSONResponse(conversation_notifications.unread_summary(account_id,str(role_key)),headers={"Cache-Control":"no-store"})


@router.get("/portal/conversation-attachments/{attachment_id}")
async def internal_attachment(request: Request, attachment_id: int):
    role_key = request.session.get("role_key")
    account_id = _reseller_message_account(request) if role_key == "reseller" else _sales_live_account(request)
    try:
        attachment = await run_in_threadpool(reseller_messaging.get_attachment,account_id,str(role_key),attachment_id)
    except ValueError as exc:
        raise HTTPException(404,detail="Attachment not found.") from exc
    return internal_conversation_media.attachment_response(request,attachment)
