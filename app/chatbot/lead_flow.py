# ruff: noqa: F403, F405
from __future__ import annotations

import re
from typing import Iterable

from .configuration import *
from .intents import *
from .provider import ask_chatbot


def _classify_answer_status(reply: str) -> str:
    lower = reply.lower()
    if reply == UNRELATED_REPLY or "can’t provide internal instructions" in lower or "can't provide internal instructions" in lower:
        return "out_of_scope"
    unconfirmed_markers = (
        "not in my approved information",
        "not listed",
        "not confirmed",
        "couldn't find",
        "could not find",
        "team member can confirm",
        "team member should review",
        "team ang",
        "maaaring mag-confirm",
        "wala sa approved information",
        "do not have a confirmed",
    )
    return "unconfirmed" if any(marker in lower for marker in unconfirmed_markers) else "answered"


def _support_result(
    reply: str,
    message: str,
    history: Iterable[dict] | None = None,
    *,
    previous_state: dict | None = None,
    answer_status: str | None = None,
) -> dict:
    answer_status = answer_status or _classify_answer_status(reply)
    previous_misses = int((previous_state or {}).get("miss_count") or 0)
    miss_count = previous_misses + 1 if answer_status in {"unconfirmed", "out_of_scope"} else 0
    handoff_offered = answer_status == "provider_unavailable" or miss_count >= 2
    handoff_reason = (
        "provider_unavailable" if answer_status == "provider_unavailable"
        else "repeated_unanswered" if miss_count >= 2
        else None
    )
    next_history = _safe_history(history)
    next_history.extend(
        [
            {"role": "user", "content": message.strip()[:MAX_HISTORY_CONTENT_LENGTH]},
            {"role": "assistant", "content": reply[:MAX_HISTORY_CONTENT_LENGTH]},
        ]
    )
    return {
        "reply": reply,
        "answer_status": answer_status,
        "handoff": {"offered": handoff_offered, "reason": handoff_reason},
        "state": {
            "mode": "support",
            "history": next_history[-MAX_HISTORY_MESSAGES:],
            "miss_count": miss_count,
        },
        "suggestions": [
            "View products",
            "Delivery details",
            "Become a reseller",
            *(["Talk to a team leader"] if handoff_offered else []),
        ],
    }


def _empty_lead_state() -> dict:
    return {"mode": "lead", "step": "collect", "field_index": 0, "data": {}}


def _next_missing_field(state: dict) -> tuple[int, str, str] | None:
    data = state.setdefault("data", {})
    for index, (field, prompt) in enumerate(LEAD_FIELDS):
        if not str(data.get(field, "")).strip():
            return index, field, prompt
    return None


def _lead_summary(data: dict) -> str:
    return (
        "Please confirm these details: "
        f"Name: {data.get('name')}; "
        f"Business: {data.get('business_name')}; "
        f"Email: {data.get('email')}; "
        f"Phone: {data.get('contact_number')}; "
        f"Location: {data.get('location')}; "
        f"Interest: {data.get('interest')}. "
        "Are these correct?"
    )


def _field_from_message(message: str) -> str | None:
    lower = re.sub(r"\s+", " ", message.lower()).strip()
    matches = [
        (len(alias), field)
        for field, aliases in FIELD_ALIASES.items()
        for alias in aliases
        if alias in lower
    ]
    if matches:
        return max(matches)[1]
    return None


def _looks_like_question(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", message.lower()).strip()
    if "?" in normalized:
        return True
    question_start = bool(
        re.match(
            r"^(?:who|what|when|where|why|how|do|does|did|is|are|can|could|would|should|will)\b",
            normalized,
        )
    )
    return question_start and (is_related(message) or is_identity_question(message) or is_prompt_attack(message))


def _requests_field_change(message: str, field: str | None) -> bool:
    if not field:
        return False
    return bool(re.search(r"\b(?:change|edit|update|correct|wrong|incorrect|replace)\b", message.lower()))


def _lead_back(state: dict) -> dict:
    data = state.setdefault("data", {})
    completed = [field for field, _ in LEAD_FIELDS if str(data.get(field, "")).strip()]
    if not completed:
        return {"reply": LEAD_FIELDS[0][1], "state": state}
    field = completed[-1]
    data.pop(field, None)
    state["step"] = "collect"
    prompt = dict(LEAD_FIELDS)[field]
    return {"reply": f"Sure—let's change that. {prompt}", "state": state}


def process_chatbot_message(
    message: str,
    state: dict | None = None,
    catalog: Iterable[dict] | None = None,
    best_sellers: Iterable[dict] | None = None,
) -> dict:
    text = message.strip()[:MAX_MESSAGE_LENGTH]
    state = dict(state or {})
    if not text:
        return {"reply": CONTACT_REPLY, "state": state}

    normalized_quick_reply = re.sub(r"\s+", " ", text.lower()).strip()

    if state.get("mode") == "handoff":
        if _contains_phrase(text, CANCEL_WORDS) or is_no(text):
            return {
                "reply": "No problem. We can continue here with the chatbot.",
                "answer_status": "answered",
                "handoff": {"offered": False, "reason": None},
                "state": {},
                "suggestions": ["View products", "Delivery details", "Become a reseller"],
            }
        if state.get("step") == "offer":
            if is_yes(text) or normalized_quick_reply in {
                "connect me",
                "talk to a team leader",
                "talk to an agent",
                "continue",
            }:
                state["step"] = "name"
                return {
                    "reply": "What display name should we use for the live conversation?",
                    "answer_status": "answered",
                    "handoff": {"offered": True, "reason": state.get("reason")},
                    "state": state,
                    "suggestions": ["Cancel"],
                }
            return {
                "reply": "Choose Connect me to start, or Not now to continue with the chatbot.",
                "answer_status": "answered",
                "handoff": {"offered": True, "reason": state.get("reason")},
                "state": state,
                "suggestions": ["Connect me", "Not now"],
            }
        if state.get("step") == "name":
            cleaned_name = re.sub(r"\s+", " ", text).strip()
            if not 2 <= len(cleaned_name) <= 80 or not any(character.isalpha() for character in cleaned_name):
                return {
                    "reply": "Please enter a display name using 2 to 80 characters, or choose Cancel.",
                    "answer_status": "answered",
                    "handoff": {"offered": True, "reason": state.get("reason")},
                    "state": state,
                    "suggestions": ["Cancel"],
                }
            state["display_name"] = cleaned_name
            state["step"] = "consent"
            return {
                "reply": (
                    "Before I connect you: your display name and live-chat messages will be stored for up to 30 days "
                    "and transmitted through Ably for real-time delivery. Do you agree?"
                ),
                "answer_status": "answered",
                "handoff": {"offered": True, "reason": state.get("reason")},
                "state": state,
                "suggestions": ["I agree", "Cancel"],
            }
        if state.get("step") == "consent":
            if is_yes(text) or normalized_quick_reply in {"i agree", "agree", "consent"}:
                state["step"] = "starting"
                return {
                    "reply": "Thanks. I’m looking for an available sales team leader now.",
                    "answer_status": "answered",
                    "handoff": {
                        "offered": True,
                        "start": True,
                        "reason": state.get("reason") or "requested",
                        "display_name": state.get("display_name"),
                    },
                    "state": state,
                    "suggestions": ["Cancel"],
                }
            return {
                "reply": "Please choose I agree to start live chat, or Cancel to keep using the chatbot.",
                "answer_status": "answered",
                "handoff": {"offered": True, "reason": state.get("reason")},
                "state": state,
                "suggestions": ["I agree", "Cancel"],
            }

    if is_human_handoff_intent(text):
        handoff_state = {"mode": "handoff", "step": "offer", "reason": "requested"}
        return {
            "reply": "I can connect you with an available sales team leader. Would you like to start a live conversation?",
            "answer_status": "answered",
            "handoff": {"offered": True, "reason": "requested"},
            "state": handoff_state,
            "suggestions": ["Connect me", "Not now"],
        }

    if normalized_quick_reply == "view products":
        history = state.get("history") if state.get("mode") == "support" else []
        products = []
        for product in list(best_sellers if best_sellers is not None else catalog or [])[:3]:
            name = str(product.get("name") or "").strip()
            if not name:
                continue
            try:
                price = float(product.get("base_price") or 0)
            except (TypeError, ValueError):
                price = 0
            products.append(
                {
                    "name": name[:120],
                    "category": str(product.get("category") or "").strip()[:80],
                    "base_price": price,
                    "pack_size": str(product.get("pack_size") or "").strip()[:30],
                    "pack_size_unit": str(product.get("pack_size_unit") or "").strip()[:20],
                }
            )
        reply = "Here are our top 3 best-selling products." if products else "Browse our complete product catalog."
        result = _support_result(reply, text, history, previous_state=state, answer_status="answered")
        result["product_preview"] = {
            "title": "Top 3 best sellers",
            "products": products,
            "view_all_url": "/products",
        }
        return result
    if normalized_quick_reply == "delivery details":
        history = state.get("history") if state.get("mode") == "support" else []
        status_sink: dict = {}
        reply = ask_chatbot(text, history=history, catalog=catalog, _status_sink=status_sink)
        return _support_result(
            reply, text, history, previous_state=state,
            answer_status=status_sink.get("answer_status"),
        )
    if normalized_quick_reply == "become a reseller":
        state = _empty_lead_state()
        return {
            "reply": "I can help with reseller inquiries. What is your full name? You can type ‘cancel’ anytime.",
            "state": state,
            "suggestions": ["Cancel"],
        }

    if state.get("mode") != "lead":
        if is_reseller_signup_intent(text):
            state = _empty_lead_state()
            return {
                "reply": "I can help with reseller inquiries. What is your full name? You can type ‘cancel’ anytime.",
                "state": state,
                "suggestions": ["Cancel"],
            }
        history = state.get("history") if state.get("mode") == "support" else []
        status_sink = {}
        reply = ask_chatbot(text, history=history, catalog=catalog, _status_sink=status_sink)
        return _support_result(
            reply, text, history, previous_state=state,
            answer_status=status_sink.get("answer_status"),
        )

    if _contains_phrase(text, CANCEL_WORDS):
        return {
            "reply": "No problem—the reseller inquiry was cancelled. How else can I help?",
            "state": {},
            "suggestions": ["View products", "Delivery details", "Become a reseller"],
        }
    if _contains_phrase(text, RESTART_WORDS):
        new_state = _empty_lead_state()
        return {"reply": "Let's start over. What is your full name?", "state": new_state, "suggestions": ["Cancel"]}
    if _contains_phrase(text, BACK_WORDS):
        result = _lead_back(state)
        result["suggestions"] = ["Cancel"]
        return result

    step = state.get("step", "collect")
    data = state.setdefault("data", {})

    if step == "collect":
        missing = _next_missing_field(state)
        if missing is None:
            state["step"] = "confirm"
            return {"reply": _lead_summary(data), "state": state, "suggestions": ["Yes", "Change email", "Cancel"]}

        index, field, prompt = missing
        if _looks_like_question(text):
            support_reply = ask_chatbot(text, catalog=catalog)
            return {
                "reply": f"{support_reply} To continue the reseller inquiry: {prompt}",
                "state": state,
                "suggestions": ["Back", "Cancel"],
            }
        max_length = LEAD_FIELD_MAX_LENGTHS[field]
        if len(text) > max_length:
            return {
                "reply": f"Please keep that detail under {max_length} characters.",
                "state": state,
                "suggestions": ["Back", "Cancel"],
            }
        if field == "name" and not any(character.isalpha() for character in text):
            return {"reply": "Please enter a valid full name using letters.", "state": state, "suggestions": ["Back", "Cancel"]}
        if field == "email" and not _is_valid_email(text):
            return {"reply": "Please enter a valid email address, or type ‘back’ or ‘cancel’.", "state": state, "suggestions": ["Back", "Cancel"]}
        if field == "contact_number" and not _is_valid_contact_number(text):
            return {"reply": "Please enter a valid contact number with 7 to 15 digits.", "state": state, "suggestions": ["Back", "Cancel"]}
        if len(text) < 2:
            return {"reply": prompt, "state": state}

        data[field] = text
        next_field = _next_missing_field(state)
        if next_field is None:
            state["step"] = "confirm"
            return {"reply": _lead_summary(data), "state": state, "suggestions": ["Yes", "Change email", "Cancel"]}
        state["field_index"] = next_field[0]
        return {"reply": next_field[2], "state": state, "suggestions": ["Back", "Cancel"]}

    if step == "confirm":
        edit_field = _field_from_message(text)
        if _requests_field_change(text, edit_field):
            assert edit_field is not None
            data.pop(edit_field, None)
            state["step"] = "collect"
            return {"reply": dict(LEAD_FIELDS)[edit_field], "state": state, "suggestions": ["Cancel"]}
        if is_yes(text):
            state["step"] = "connect"
            return {"reply": "Do you want me to connect you with an available sales team leader now?", "state": state, "suggestions": ["Yes", "No"]}
        if is_no(text):
            state["step"] = "edit"
            return {
                "reply": "Which detail should I change: name, business, email, phone, location, or interest?",
                "state": state,
                "suggestions": ["Name", "Email", "Phone", "Location", "Cancel"],
            }
        if edit_field:
            data.pop(edit_field, None)
            state["step"] = "collect"
            return {"reply": dict(LEAD_FIELDS)[edit_field], "state": state, "suggestions": ["Cancel"]}
        return {"reply": "Please answer yes, or tell me which detail you want to change.", "state": state, "suggestions": ["Yes", "Change email", "Cancel"]}

    if step == "edit":
        edit_field = _field_from_message(text)
        if not edit_field:
            return {
                "reply": "Please choose name, business, email, phone, location, or interest.",
                "state": state,
                "suggestions": ["Name", "Email", "Phone", "Location", "Cancel"],
            }
        data.pop(edit_field, None)
        state["step"] = "collect"
        return {"reply": dict(LEAD_FIELDS)[edit_field], "state": state, "suggestions": ["Cancel"]}

    if step == "connect":
        edit_field = _field_from_message(text)
        if _requests_field_change(text, edit_field):
            assert edit_field is not None
            data.pop(edit_field, None)
            state["step"] = "collect"
            return {"reply": dict(LEAD_FIELDS)[edit_field], "state": state, "suggestions": ["Cancel"]}
        if is_yes(text):
            state["step"] = "submitted"
            return {
                "reply": "Thank you. I will send your details to an available sales team leader.",
                "state": state,
                "action": "create_lead",
                "lead": data,
                "suggestions": ["View products"],
            }
        if is_no(text):
            state["step"] = "done"
            return {
                "reply": "Understood. Your details were not submitted. You can message me again when you are ready.",
                "state": state,
                "suggestions": ["View products", "Become a reseller"],
            }
        return {"reply": "Please answer yes if you want to talk to an available team leader, or no to cancel.", "state": state, "suggestions": ["Yes", "No"]}

    if step in {"submitted", "done"}:
        status_sink = {}
        reply = ask_chatbot(text, catalog=catalog, _status_sink=status_sink)
        return _support_result(reply, text, answer_status=status_sink.get("answer_status"))

    status_sink = {}
    reply = ask_chatbot(text, catalog=catalog, _status_sink=status_sink)
    return _support_result(reply, text, answer_status=status_sink.get("answer_status"))

__all__ = ["process_chatbot_message"]

