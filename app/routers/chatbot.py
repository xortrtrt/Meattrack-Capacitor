from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import live_chat
from app import repositories as data
from app.chatbot import process_chatbot_message
from app.routers._shared import rate_limit_response
from app.routers.public import public_products
from app.security_controls import (
    RateLimitExceeded,
    enforce_chatbot,
    enforce_chatbot_reset,
    enforce_handoff,
    enforce_lead,
)
from app.web import require_email


router = APIRouter()


@router.post("/api/chatbot")
async def chatbot_api(request: Request):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return JSONResponse({"reply": "Please send chat requests as JSON.", "error": "unsupported_media_type"}, status_code=415)
    try:
        payload = await request.json()
    except (TypeError, ValueError):
        return JSONResponse({"reply": "Please send a valid chat message."}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"reply": "Please send a valid chat message."}, status_code=400)
    if payload.get("action") == "reset":
        try:
            enforce_chatbot_reset(request)
        except RateLimitExceeded as exc:
            return JSONResponse(
                {"reply": str(exc), "error": "rate_limited", "retry_after": exc.retry_after},
                status_code=429,
                headers={"Retry-After": str(exc.retry_after)},
            )
        active_conversation = request.session.get("live_chat_conversation_id")
        if active_conversation:
            conversation = live_chat.get_conversation(str(active_conversation))
            if conversation and conversation.get("status") in {"queued", "active"}:
                return JSONResponse(
                    {
                        "reply": "End the current live conversation before starting a new chat.",
                        "error": "live_chat_active",
                        "conversation": conversation,
                    },
                    status_code=409,
                )
            request.session.pop("live_chat_conversation_id", None)
        request.session["chatbot_state"] = {}
        return JSONResponse(
            {
                "reply": "Conversation reset. How can I help with Batangas Premium today?",
                "lead_created": False,
                "assigned_team_leader": None,
                "suggestions": ["View products", "Delivery details", "Become a reseller"],
            }
        )
    try:
        enforce_chatbot(request)
    except RateLimitExceeded as exc:
        return JSONResponse(
            {"reply": str(exc), "error": "rate_limited", "retry_after": exc.retry_after},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    message = str(payload.get("message", "")).strip()
    if len(message) < 2:
        return JSONResponse({"reply": "Please contact Batangas Premium directly for complete details."})
    if len(message) > 500:
        return JSONResponse({"reply": "Please keep your message under 500 characters."}, status_code=400)
    result = process_chatbot_message(
        message,
        request.session.get("chatbot_state") or {},
        catalog=public_products(),
    )
    lead_created = False
    assigned_team_leader = None
    conversation = None
    handoff = result.get("handoff") or {"offered": False, "reason": None}
    if handoff.get("start"):
        try:
            enforce_handoff(request)
            conversation = live_chat.create_conversation(
                str(handoff.get("display_name") or ""),
                str(handoff.get("reason") or "requested"),
            )
        except RateLimitExceeded as exc:
            return JSONResponse(
                {"reply": str(exc), "error": "rate_limited", "retry_after": exc.retry_after},
                status_code=429,
                headers={"Retry-After": str(exc.retry_after)},
            )
        except ValueError as exc:
            return JSONResponse({"reply": str(exc), "error": "invalid_handoff"}, status_code=400)
        request.session["live_chat_conversation_id"] = conversation["conversation_id"]
        request.session["chatbot_state"] = {}
        handoff = {
            "offered": True,
            "started": True,
            "reason": handoff.get("reason"),
            "conversation_id": conversation["conversation_id"],
            "status": conversation["status"],
        }
        await asyncio.to_thread(live_chat.notify_available_leaders, conversation)
    if result.get("action") == "create_lead":
        lead = result.get("lead") or {}
        lead_email = require_email(str(lead.get("email", "")).strip())
        try:
            enforce_lead(request, lead_email)
        except RateLimitExceeded as exc:
            return rate_limit_response(exc)
        inquiry = data.add_inquiry(
            str(lead.get("name", "")).strip(),
            str(lead.get("business_name", "")).strip(),
            lead_email,
            str(lead.get("contact_number", "")).strip(),
            "\n".join(
                [
                    f"Location: {lead.get('location', '')}",
                    f"Interest: {lead.get('interest', '')}",
                    "Source: chatbot lead capture",
                ]
            ),
            chat_conversation_id=request.session.get("live_chat_conversation_id"),
        )
        lead_created = True
        request.session["chatbot_state"] = {}
        assigned_id = inquiry.get("assigned_team_leader_account_id")
        if assigned_id:
            assigned = data.account_portal_profile(int(assigned_id))
            assigned_team_leader = assigned.get("name") if assigned else None
        reply = result["reply"]
        if assigned_team_leader:
            reply += f" Your assigned sales team leader is {assigned_team_leader}."
    elif not conversation:
        request.session["chatbot_state"] = result.get("state") or {}
        reply = result["reply"]
    else:
        reply = result["reply"]
    try:
        data.add_log("Website visitor", "used_chatbot", "Public support widget")
    except Exception:
        logging.getLogger(__name__).warning("Unable to record non-critical chatbot activity log.")
    return JSONResponse(
        {
            "reply": reply,
            "answer_status": result.get("answer_status") or "answered",
            "handoff": handoff,
            "conversation": conversation,
            "lead_created": lead_created,
            "assigned_team_leader": assigned_team_leader,
            "suggestions": result.get("suggestions") or [],
        }
    )



