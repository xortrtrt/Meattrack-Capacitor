from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app import live_chat
from app import reseller_messaging
from app import repositories as data
from app.portal_dependencies import session_account_id, session_team_leader_role
from app.routers._shared import json_payload, rate_limit_response
from app.security_controls import (
    RateLimitExceeded,
    enforce_ably_token,
    enforce_handoff,
    enforce_lead,
    enforce_live_message,
    enforce_live_typing,
)
from app.web import require_email


router = APIRouter()


class ResellerMessagePayload(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    client_message_id: uuid.UUID
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


def _visitor_live_conversation(request: Request, conversation_id: str) -> dict:
    if not live_chat.visitor_can_access(request.session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conversation = live_chat.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


def _live_chat_actor(request: Request, conversation_id: str) -> tuple[str, int | None, dict]:
    """Resolve a shared live-chat endpoint without confusing same-browser visitor and leader tabs."""
    actor_hint = request.headers.get("x-live-chat-actor", "").strip().lower()
    if actor_hint not in {"", "visitor", "team_leader"}:
        raise HTTPException(status_code=400, detail="Invalid live-chat actor.")
    visitor_owned = live_chat.visitor_can_access(request.session, conversation_id)
    if actor_hint == "visitor" or (
        not actor_hint and (visitor_owned or request.session.get("role_key") not in {"team-leader", "sales-supervisor"})
    ):
        return "visitor", None, _visitor_live_conversation(request, conversation_id)

    account_id = _sales_live_account(request)
    if not live_chat.leader_can_access(account_id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conversation = live_chat.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return "team_leader", account_id, conversation


async def _publish_chat_message(message: dict, conversation_id: str) -> str:
    try:
        await asyncio.to_thread(live_chat.publish_message, message, conversation_id)
        return "published"
    except live_chat.AblyUnavailable as exc:
        live_chat.mark_publish_failed(int(message["chat_message_id"]), str(exc))
        return "pending"


@router.post("/api/live-chat/start")
async def live_chat_start(request: Request):
    payload = await json_payload(request)
    try:
        enforce_handoff(request)
        if not bool(payload.get("consent")):
            raise live_chat.LiveChatError("Consent is required before starting live chat.")
        conversation = live_chat.create_conversation(
            str(payload.get("display_name", "")),
            str(payload.get("reason", "requested")),
        )
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        return JSONResponse({"error": "invalid_handoff", "message": str(exc)}, status_code=400)
    request.session["live_chat_conversation_id"] = conversation["conversation_id"]
    await asyncio.to_thread(live_chat.notify_available_leaders, conversation)
    return JSONResponse({"conversation": conversation}, status_code=201)


@router.get("/api/live-chat/{conversation_id}")
async def live_chat_status(request: Request, conversation_id: str):
    conversation = _visitor_live_conversation(request, conversation_id)
    return {"conversation": conversation}


@router.get("/api/live-chat/{conversation_id}/messages")
async def live_chat_messages(request: Request, conversation_id: str, after_id: int = 0):
    _live_chat_actor(request, conversation_id)
    return {"messages": live_chat.list_messages(conversation_id, after_id=after_id)}


@router.post("/api/live-chat/{conversation_id}/messages")
async def live_chat_send_message(request: Request, conversation_id: str):
    payload = await json_payload(request)
    sender_type, account_id, _conversation = _live_chat_actor(request, conversation_id)
    try:
        enforce_live_message(request, account_id=account_id)
        message, created = live_chat.add_message(
            conversation_id,
            client_message_id=str(payload.get("client_message_id", "")),
            sender_type=sender_type,
            sender_account_id=account_id,
            content=str(payload.get("message", "")),
        )
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        return JSONResponse({"error": "invalid_message", "message": str(exc)}, status_code=400)
    delivery = await _publish_chat_message(message, conversation_id) if created else message["publication_status"]
    return {"message": message, "delivery": delivery, "duplicate": not created}


@router.post("/api/live-chat/{conversation_id}/typing")
async def live_chat_typing(request: Request, conversation_id: str):
    payload = await json_payload(request)
    sender_type, account_id, conversation = _live_chat_actor(request, conversation_id)
    sender_name = (
        conversation.get("assigned_team_leader_name")
        if sender_type == "team_leader"
        else conversation.get("display_name")
    )
    if not conversation or conversation.get("status") != "active":
        return JSONResponse(
            {"error": "conversation_not_active", "message": "This live conversation is not active."},
            status_code=409,
        )
    is_typing = payload.get("is_typing")
    if not isinstance(is_typing, bool):
        return JSONResponse(
            {"error": "invalid_typing_state", "message": "Typing state must be true or false."},
            status_code=400,
        )
    try:
        enforce_live_typing(request, account_id=account_id)
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    try:
        live_chat.set_typing_state(conversation_id, sender_type, is_typing)
    except ValueError as exc:
        return JSONResponse({"error": "conversation_not_active", "message": str(exc)}, status_code=409)
    event = {
        "conversation_id": conversation_id,
        "sender_type": sender_type,
        "sender_name": sender_name,
        "is_typing": is_typing,
    }
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "typing.updated",
            event,
        )
    except live_chat.AblyUnavailable:
        return JSONResponse({"typing": event, "delivery": "unavailable"}, status_code=202)
    return {"typing": event, "delivery": "published"}


@router.post("/api/live-chat/{conversation_id}/fallback")
async def live_chat_fallback(request: Request, conversation_id: str):
    _visitor_live_conversation(request, conversation_id)
    payload = await json_payload(request)
    try:
        conversation = live_chat.save_fallback_contact(conversation_id, str(payload.get("contact", "")))
    except ValueError as exc:
        return JSONResponse({"error": "invalid_contact", "message": str(exc)}, status_code=400)
    assigned_id = conversation.get("assigned_team_leader_account_id")
    if assigned_id:
        try:
            await asyncio.to_thread(
                live_chat.publish_event,
                live_chat.leader_channel(int(assigned_id)),
                "follow_up.created",
                {"conversation_id": conversation_id, "display_name": conversation["display_name"]},
            )
        except live_chat.AblyUnavailable:
            pass
    return {"conversation": conversation}


@router.post("/api/live-chat/{conversation_id}/cancel")
async def live_chat_cancel(request: Request, conversation_id: str):
    conversation = _visitor_live_conversation(request, conversation_id)
    if conversation["status"] not in {"queued", "active", "awaiting_contact"}:
        return {"conversation": conversation}
    closed = live_chat.close_conversation(conversation_id, cancelled=True)
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "conversation.cancelled",
            {"conversation_id": conversation_id, "status": "cancelled"},
        )
    except live_chat.AblyUnavailable:
        pass
    return {"conversation": closed}


@router.post("/api/live-chat/{conversation_id}/rating")
async def live_chat_rating(request: Request, conversation_id: str):
    _visitor_live_conversation(request, conversation_id)
    payload = await json_payload(request)
    try:
        raw_rating = payload.get("rating")
        if isinstance(raw_rating, bool) or not isinstance(raw_rating, int):
            raise live_chat.LiveChatError("Choose a customer-service rating from 1 to 5.")
        rating = live_chat.rate_conversation(conversation_id, raw_rating)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": "invalid_rating", "message": str(exc)}, status_code=400)
    return {"rating": rating}


@router.post("/api/live-chat/{conversation_id}/inquiry")
async def live_chat_create_inquiry(request: Request, conversation_id: str):
    conversation = _visitor_live_conversation(request, conversation_id)
    payload = await json_payload(request)
    if conversation.get("status") != "active" or not conversation.get("inquiry_form_requested_at"):
        return JSONResponse(
            {"error": "inquiry_form_unavailable", "message": "The sales team leader has not shared an inquiry form."},
            status_code=409,
        )
    if payload.get("consent_confirmed") is not True:
        return JSONResponse(
            {"error": "consent_required", "message": "Confirm the visitor’s consent before submitting the inquiry."},
            status_code=400,
        )
    try:
        name = live_chat.normalize_display_name(str(payload.get("name", "")))
        business_name = " ".join(str(payload.get("business_name", "")).strip().split())
        if not 2 <= len(business_name) <= 160 or live_chat.CONTROL_CHARACTERS_RE.search(business_name):
            raise ValueError("Enter a business name using 2 to 160 characters.")
        email = require_email(str(payload.get("email", "")))
        if len(email) > 254:
            raise ValueError("Enter an email address under 255 characters.")
        contact_kind, contact_number = live_chat.normalize_contact(str(payload.get("contact_number", "")))
        if contact_kind != "phone":
            raise ValueError("Enter a valid contact number with 7 to 15 digits.")
        location = " ".join(str(payload.get("location", "")).strip().split())
        notes = str(payload.get("notes", "")).strip()
        if not 2 <= len(location) <= 180 or live_chat.CONTROL_CHARACTERS_RE.search(location):
            raise ValueError("Enter a location using 2 to 180 characters.")
        if len(notes) > 500 or live_chat.CONTROL_CHARACTERS_RE.search(notes):
            raise ValueError("Keep inquiry notes under 500 characters.")
        enforce_lead(request, email)
        inquiry = data.add_inquiry(
            name,
            business_name,
            email,
            contact_number,
            "\n".join(filter(None, [f"Location: {location}", notes, "Source: live sales conversation"])),
            chat_conversation_id=conversation_id,
            assigned_team_leader_account_id=int(conversation["assigned_team_leader_account_id"]),
        )
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        status_code = 409 if "already has" in str(exc) else 400
        return JSONResponse({"error": "invalid_inquiry", "message": str(exc)}, status_code=status_code)

    confirmation, _created = live_chat.add_message(
        conversation_id,
        client_message_id=str(uuid.uuid4()),
        sender_type="system",
        content=(
            f"Your reseller inquiry #{inquiry['inquiry_id']} was submitted for review. "
            "No reseller account has been created yet."
        ),
    )
    updated_conversation = live_chat.get_conversation(conversation_id) or conversation
    await _publish_chat_message(confirmation, conversation_id)
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "inquiry.submitted",
            {
                "conversation_id": conversation_id,
                "status": "active",
                "inquiry_id": inquiry["inquiry_id"],
            },
        )
    except live_chat.AblyUnavailable:
        pass
    return {"inquiry": inquiry, "conversation": updated_conversation, "message": confirmation}


@router.post("/api/ably/token")
async def ably_token(request: Request):
    payload = await json_payload(request)
    account_id = session_account_id(request)
    try:
        enforce_ably_token(request, account_id=account_id)
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    scope = str(payload.get("scope", "conversation"))
    if scope == "notifications":
        account_id = _sales_live_account(request)
        capabilities = {live_chat.leader_channel(account_id): ["subscribe"]}
        client_id = f"leader:{account_id}"
    else:
        conversation_id = str(payload.get("conversation_id", ""))
        actor, actor_account_id, _conversation = _live_chat_actor(request, conversation_id)
        if actor == "team_leader":
            assert actor_account_id is not None
            account_id = actor_account_id
            client_id = f"leader:{account_id}"
            capabilities = {
                live_chat.leader_channel(account_id): ["subscribe"],
                live_chat.conversation_channel(conversation_id): ["subscribe"],
            }
        else:
            client_id = f"visitor:{request.session.get('rate_limit_id', 'anonymous')}"
            capabilities = {live_chat.conversation_channel(conversation_id): ["subscribe"]}
    try:
        token = live_chat.issue_ably_jwt(client_id=client_id, capabilities=capabilities)
    except live_chat.AblyUnavailable as exc:
        return JSONResponse({"error": "live_chat_unavailable", "message": str(exc)}, status_code=503)
    return Response(token, media_type="text/plain", headers={"Cache-Control": "no-store"})


@router.get("/api/portal/reseller/inbox")
def reseller_inbox_workspace(request: Request, after_id: int = 0):
    account_id = _reseller_message_account(request)
    return reseller_messaging.reseller_workspace(account_id, after_id=after_id)


@router.post("/api/portal/reseller/inbox/messages")
def reseller_inbox_send(request: Request, payload: ResellerMessagePayload):
    account_id = _reseller_message_account(request)
    try:
        enforce_live_message(request, account_id=account_id)
        if payload.return_id or payload.report_issue:
            message = reseller_messaging.send_from_reseller(account_id, str(payload.client_message_id), payload.message,
                return_id=payload.return_id, report_issue=payload.report_issue)
        else:
            message = reseller_messaging.send_from_reseller(account_id, str(payload.client_message_id), payload.message)
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        return JSONResponse({"error": "message_failed", "message": str(exc)}, status_code=400)
    return {"message": None, **message} if message.get("followup_saved") else {"message": message}


@router.post("/api/portal/reseller/inbox/read")
def reseller_inbox_read(request: Request):
    account_id = _reseller_message_account(request)
    return {"read_count": reseller_messaging.mark_read_for_reseller(account_id)}


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
def sales_reseller_send(request: Request, reseller_account_id: int, payload: ResellerMessagePayload):
    account_id = _sales_live_account(request)
    try:
        if payload.report_issue:
            raise ValueError("Only the reseller can report a replacement issue.")
        enforce_live_message(request, account_id=account_id)
        message = reseller_messaging.send_from_leader(
            account_id,
            reseller_account_id,
            str(payload.client_message_id),
            payload.message,
            **({"return_id": payload.return_id} if payload.return_id else {}),
        )
    except RateLimitExceeded as exc:
        return rate_limit_response(exc)
    except ValueError as exc:
        return JSONResponse({"error": "message_failed", "message": str(exc)}, status_code=400)
    return {"message": message}


@router.post("/api/portal/live-chat/resellers/{reseller_account_id}/read")
def sales_reseller_read(request: Request, reseller_account_id: int):
    account_id = _sales_live_account(request)
    try:
        return {"read_count": reseller_messaging.mark_read_for_leader(account_id, reseller_account_id)}
    except ValueError as exc:
        return JSONResponse({"error": "conversation_unavailable", "message": str(exc)}, status_code=404)


@router.delete("/api/portal/live-chat/resellers/{reseller_account_id}")
def sales_reseller_delete(request: Request, reseller_account_id: int):
    account_id = _sales_live_account(request)
    return {"deleted": reseller_messaging.delete_for_leader(account_id, reseller_account_id)}


@router.get("/api/portal/live-chat/workspace")
async def portal_live_chat_workspace(request: Request):
    account_id = _sales_live_account(request)
    return live_chat.workspace(account_id)


@router.post("/api/portal/live-chat/presence")
async def portal_live_chat_presence(request: Request):
    account_id = _sales_live_account(request)
    payload = await json_payload(request)
    try:
        presence = live_chat.set_presence(account_id, str(payload.get("availability", "")))
    except ValueError as exc:
        return JSONResponse({"error": "invalid_presence", "message": str(exc)}, status_code=400)
    return {"presence": presence}


@router.post("/api/portal/live-chat/heartbeat")
async def portal_live_chat_heartbeat(request: Request):
    account_id = _sales_live_account(request)
    return {"presence": live_chat.heartbeat(account_id)}


@router.post("/api/portal/live-chat/{conversation_id}/claim")
async def portal_live_chat_claim(request: Request, conversation_id: str):
    account_id = _sales_live_account(request)
    try:
        conversation = live_chat.claim_conversation(account_id, conversation_id)
    except ValueError as exc:
        return JSONResponse({"error": "claim_failed", "message": str(exc)}, status_code=409)
    system_message = conversation.pop("system_message", None)
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "conversation.claimed",
            {
                "conversation_id": conversation_id,
                "status": "active",
                "team_leader_account_id": account_id,
                "team_leader_name": conversation.get("assigned_team_leader_name"),
            },
        )
        if system_message:
            await _publish_chat_message(system_message, conversation_id)
    except live_chat.AblyUnavailable:
        pass
    return {"conversation": conversation}


@router.post("/api/portal/live-chat/{conversation_id}/close")
async def portal_live_chat_close(request: Request, conversation_id: str):
    account_id = _sales_live_account(request)
    try:
        conversation = live_chat.close_conversation(conversation_id, account_id=account_id)
    except ValueError as exc:
        return JSONResponse({"error": "close_failed", "message": str(exc)}, status_code=409)
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "conversation.closed",
            {"conversation_id": conversation_id, "status": "closed"},
        )
    except live_chat.AblyUnavailable:
        pass
    return {"conversation": conversation}


@router.post("/api/portal/live-chat/{conversation_id}/transfer")
async def portal_live_chat_transfer(request: Request, conversation_id: str):
    account_id = _sales_live_account(request)
    try:
        conversation = live_chat.transfer_conversation(account_id, conversation_id)
    except ValueError as exc:
        return JSONResponse({"error": "transfer_failed", "message": str(exc)}, status_code=409)
    system_message = conversation.pop("system_message", None)
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "conversation.transferred",
            {
                "conversation_id": conversation_id,
                "status": "queued",
                "previous_team_leader_account_id": account_id,
            },
        )
        if system_message:
            await _publish_chat_message(system_message, conversation_id)
    except live_chat.AblyUnavailable:
        pass
    await asyncio.to_thread(live_chat.notify_available_leaders, conversation)
    return {"conversation": conversation}


@router.post("/api/portal/live-chat/{conversation_id}/inquiry-form")
async def portal_live_chat_inquiry_form(request: Request, conversation_id: str):
    account_id = _sales_live_account(request)
    try:
        conversation = live_chat.request_inquiry_form(account_id, conversation_id)
    except ValueError as exc:
        return JSONResponse({"error": "inquiry_form_failed", "message": str(exc)}, status_code=409)
    system_message = conversation.pop("system_message", None)
    try:
        await asyncio.to_thread(
            live_chat.publish_event,
            live_chat.conversation_channel(conversation_id),
            "inquiry.form_requested",
            {"conversation_id": conversation_id, "requested": True},
        )
        if system_message:
            await _publish_chat_message(system_message, conversation_id)
    except live_chat.AblyUnavailable:
        pass
    return {"conversation": conversation}



