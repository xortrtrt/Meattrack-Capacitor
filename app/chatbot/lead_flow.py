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
        "handoff": {"offered": False, "reason": None},
        "state": {
            "mode": "support",
            "history": next_history[-MAX_HISTORY_MESSAGES:],
            "miss_count": miss_count,
        },
        "suggestions": [
            "View products",
            "Delivery details",
            "How to Become a Reseller?",
        ],
    }


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

    # Retired lead/handoff sessions must never resume data collection.
    if state.get("mode") in {"lead", "handoff"}:
        state = {}

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
        status_sink: dict = {}
        reply = ask_chatbot(text, history=history, catalog=best_sellers if best_sellers is not None else catalog, _status_sink=status_sink)
        result = _support_result(reply, text, history, previous_state=state, answer_status=status_sink.get("answer_status"))
        result["product_preview"] = {
            "title": "Top 3 best sellers",
            "products": products,
            "view_all_url": "/products",
        }
        return result
    history = state.get("history") if state.get("mode") == "support" else []
    status_sink = {}
    reply = ask_chatbot(text, history=history, catalog=catalog, _status_sink=status_sink)
    result = _support_result(
        reply, text, history, previous_state=state,
        answer_status=status_sink.get("answer_status"),
    )
    if is_reseller_signup_intent(text):
        result["state"] = {}
        result["suggestions"] = ["View products", "Delivery details"]
    return result


__all__ = ["process_chatbot_message"]
