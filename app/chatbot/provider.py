# ruff: noqa: F403, F405
from __future__ import annotations

import json
from typing import Iterable

from openai import OpenAI

from app.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from .catalog import *
from .configuration import *
from .intents import *


def ask_chatbot(
    message: str,
    history: Iterable[dict] | None = None,
    catalog: Iterable[dict] | None = None,
    _status_sink: dict | None = None,
) -> str:
    if not message.strip():
        return CONTACT_REPLY

    safe_history = _safe_history(history)
    # Let the model interpret language and intent before local product heuristics.
    # Local rules remain the fallback when no hosted answer can be used.
    if not OPENROUTER_API_KEY:
        return fallback_reply(message, catalog=catalog, history=safe_history)

    try:
        client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            timeout=OPENROUTER_TIMEOUT_SECONDS,
            max_retries=1,
        )
        messages: list = [
                {
                    "role": "system",
                    "content": (
                        f"{SYSTEM_PROMPT.strip()}\n\n"
                        f"{VERIFIED_BUSINESS_PROMPT.strip()}\n\n"
                        f"{_catalog_prompt(catalog)}"
                    ),
                },
                *safe_history,
                {"role": "user", "content": message.strip()},
            ]
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=240,
            response_format={"type": "json_object"},
        )
    except Exception:
        if _status_sink is not None:
            _status_sink["answer_status"] = "provider_unavailable"
        return fallback_reply(message, catalog=catalog, history=safe_history)

    content = response.choices[0].message.content if response.choices else ""
    if not content or not content.strip():
        return fallback_reply(message, catalog=catalog, history=safe_history)
    try:
        result = json.loads(content)
    except (TypeError, ValueError):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if not isinstance(result, dict) or result.get("scope") not in ("in_scope", "out_of_scope"):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if result["scope"] == "out_of_scope":
        return UNRELATED_REPLY
    reply = result.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return fallback_reply(message, catalog=catalog, history=safe_history)
    # Validate only after the hosted model has interpreted the request.
    if is_prompt_attack(message) or _asks_private_customer_data(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _asks_unconfirmed_product_detail(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    cleaned = clean_reply(reply)
    if not _model_reply_is_grounded(cleaned, catalog):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _has_recommendation_intent(message) and _catalog_entries(catalog) and not any(
        item["name"].lower() in cleaned.lower()
        for item in _catalog_entries(catalog)
    ):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    return cleaned


__all__ = ["ask_chatbot"]
