# ruff: noqa: F403, F405
from __future__ import annotations

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
    if is_prompt_attack(message):
        return "I can’t provide internal instructions, but I can help with Batangas Premium products, orders, delivery, or reseller inquiries."
    if is_identity_question(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _asks_private_customer_data(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _is_recommendation_follow_up(message, safe_history):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _has_recommendation_intent(message) and _normalized_pack_measurement(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _asks_budget_recommendation(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _asks_generic_recommendation(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _asks_catalog_listing(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _asks_unconfirmed_product_detail(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    catalog_entries = _catalog_entries(catalog)
    requested_product = _product_request(message, catalog_entries)
    direct_matches = (
        _matching_named_product_entries(requested_product, catalog_entries)
        if requested_product
        else []
    )
    if not _conversation_is_related(
        message,
        safe_history,
        has_product_request=bool(requested_product),
    ) and not direct_matches:
        return UNRELATED_REPLY

    # Unknown named products are resolved locally so a hosted model cannot
    # substitute unrelated catalog items or invent a price.
    if (
        catalog_entries
        and requested_product
        and not direct_matches
    ):
        return fallback_reply(message, catalog=catalog, history=safe_history)

    # Exact catalog facts are generated from database values, not model text.
    # This also prevents trailing user instructions from changing a price,
    # pack size, or stock disclaimer.
    # Catalog facts and their pronoun follow-ups are generated locally from the
    # current database snapshot so price, pack, and availability cannot drift.
    if _asks_direct_catalog_fact(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)

    if _requires_grounded_service_reply(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)

    if _asks_reseller_requirements(message):
        return fallback_reply(message, catalog=catalog, history=safe_history)

    # Unsupported composition and food-safety details must always use the
    # approved label/team guidance instead of relying on model classification.
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
                        f"{_catalog_prompt(catalog, {item['name'] for item in direct_matches})}"
                    ),
                },
                *safe_history,
                {"role": "user", "content": message.strip()},
            ]
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=180,
        )
    except Exception:
        if _status_sink is not None:
            _status_sink["answer_status"] = "provider_unavailable"
        return fallback_reply(message, catalog=catalog, history=safe_history)

    content = response.choices[0].message.content if response.choices else ""
    cleaned = clean_reply(content or "")
    if not _model_reply_is_grounded(cleaned, catalog):
        return fallback_reply(message, catalog=catalog, history=safe_history)
    if _has_recommendation_intent(message) and catalog_entries and not any(
        item["name"].lower() in cleaned.lower()
        for item in catalog_entries
    ):
        # A recommendation must name something in the current database
        # catalog. This rejects plausible-sounding products invented by the
        # hosted model even when it avoids quoting a price.
        return fallback_reply(message, catalog=catalog, history=safe_history)
    return cleaned



__all__ = ["ask_chatbot"]

