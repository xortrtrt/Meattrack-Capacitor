from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import repositories as data
from app.chatbot import process_chatbot_message
from app.routers.public import public_best_selling_products, public_products
from app.security_controls import (
    RateLimitExceeded,
    enforce_chatbot,
    enforce_chatbot_reset,
)
from app.chatbot.configuration import BUSINESS_EMAIL


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
        request.session.pop("live_chat_conversation_id", None)
        request.session["chatbot_state"] = {}
        return JSONResponse(
            {
                "reply": "Conversation reset. How can I help with Batangas Premium today?",
                "lead_created": False,
                "assigned_team_leader": None,
                "suggestions": ["View products", "Delivery details", "How to Become a Reseller?"],
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
    is_product_preview = " ".join(message.casefold().split()) == "view products"
    result = process_chatbot_message(
        message,
        request.session.get("chatbot_state") or {},
        catalog=None if is_product_preview else public_products(),
        best_sellers=public_best_selling_products(limit=3) if is_product_preview else None,
    )
    lead_created = False
    assigned_team_leader = None
    if result.get("action") == "create_lead":
        request.session["chatbot_state"] = {}
        reply = f"For reseller opportunities, please contact the Business Owner at {BUSINESS_EMAIL}. Please discuss the opportunity directly with the owner."
    else:
        request.session["chatbot_state"] = result.get("state") or {}
        reply = result["reply"]
    try:
        data.add_log("Website visitor", "used_chatbot", "Public support widget")
    except Exception:
        logging.getLogger(__name__).warning("Unable to record non-critical chatbot activity log.")
    response_payload = {
        "reply": reply,
        "answer_status": result.get("answer_status") or "answered",
        "handoff": {"offered": False, "reason": None},
        "conversation": None,
        "lead_created": lead_created,
        "assigned_team_leader": assigned_team_leader,
        "suggestions": result.get("suggestions") or [],
    }
    if result.get("product_preview"):
        response_payload["product_preview"] = result["product_preview"]
    return JSONResponse(response_payload)



