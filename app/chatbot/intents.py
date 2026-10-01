# ruff: noqa: F403, F405
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .configuration import *


def clean_reply(reply: str) -> str:
    text = re.sub(r"\s+", " ", reply).strip()
    if not text:
        return CONTACT_REPLY
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:3]).strip()


def is_related(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & ALLOWED_TERMS)


def is_greeting(message: str) -> bool:
    return bool(GREETING_RE.match(message.strip()))


def prefers_filipino(message: str, history: Iterable[dict] | None = None) -> bool:
    combined = " ".join(
        [message]
        + [
            str(item.get("content", ""))
            for item in list(history or [])[-4:]
            if isinstance(item, dict) and item.get("role") == "user"
        ]
    )
    words = set(re.findall(r"[a-z]+", combined.lower()))
    return len(words & FILIPINO_MARKERS) >= 1


def is_prompt_attack(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", message.lower()).strip()
    if any(phrase in normalized for phrase in PROMPT_ATTACK_PHRASES):
        return True
    instruction_override = re.search(
        r"\b(?:disregard|forget|ignore|override)\b.{0,45}"
        r"\b(?:earlier|previous|prior|all|instructions?|directions?|prompt|rules?)\b",
        normalized,
    )
    secret_extraction = re.search(
        r"\b(?:api\s*key|credentials?|developer\s+message|hidden\s+(?:prompt|instructions?)|"
        r"password|system\s+prompt)\b",
        normalized,
    )
    return bool(instruction_override or secret_extraction)


def is_identity_question(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:who are you|what are you|are you (?:a |an )?(?:bot|chatbot|ai|human))\b",
            message.lower(),
        )
    )


def is_human_handoff_intent(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", message.lower()).strip()
    return bool(
        re.search(
            r"\b(?:talk|speak|chat|connect|transfer)\b.{0,35}\b(?:agent|human|person|someone|representative|team leader|staff)\b",
            normalized,
        )
        or normalized in {"agent", "human", "live chat", "talk to a team leader", "talk to an agent"}
    )


def is_reseller_intent(message: str) -> bool:
    lower = message.lower()
    return any(term in lower for term in ("reseller", "partner", "partnership", "distributor", "business opportunity", "be a seller"))


def is_reseller_signup_intent(message: str) -> bool:
    lower = re.sub(r"\s+", " ", message.lower()).strip()
    if any(
        term in lower
        for term in (
            "capital",
            "document",
            "fee",
            "how much",
            "minimum",
            "qualification",
            "qualify",
            "requirement",
        )
    ):
        return False
    return any(
        phrase in lower
        for phrase in (
            "become a reseller",
            "be a reseller",
            "want to be a reseller",
            "apply as a reseller",
            "apply to be a reseller",
            "sign up as a reseller",
            "interested in becoming a reseller",
            "become a distributor",
            "be a distributor",
            "want to be a distributor",
            "apply as a distributor",
            "business opportunity",
            "be a seller",
        )
    )


def is_yes(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    if words & NO_WORDS or re.search(r"\b(?:do not|don'?t|dont|never)\b", message.lower()):
        return False
    return bool(words & YES_WORDS)


def is_no(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & NO_WORDS) or bool(re.search(r"\b(?:do not|don'?t|dont|never)\b", message.lower()))


def _contains_phrase(message: str, phrases: set[str]) -> bool:
    normalized = re.sub(r"\s+", " ", message.lower()).strip(" .!?")
    if normalized in phrases:
        return True
    command_fillers = {"form", "go", "inquiry", "kindly", "now", "please", "po", "request", "the"}
    meaningful_words = [
        word for word in re.findall(r"[a-z]+", normalized) if word not in command_fillers
    ]
    return " ".join(meaningful_words) in phrases


def _is_valid_email(value: str) -> bool:
    email = value.strip().lower()
    if len(email) > LEAD_FIELD_MAX_LENGTHS["email"] or not EMAIL_RE.fullmatch(email):
        return False
    local, domain = email.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in local or ".." in domain:
        return False
    labels = domain.split(".")
    if len(labels[-1]) < 2 or not labels[-1].isalpha():
        return False
    return all(
        label
        and not label.startswith("-")
        and not label.endswith("-")
        and re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    )


def _is_valid_contact_number(value: str) -> bool:
    phone = value.strip()
    if not re.fullmatch(r"[0-9+().\-\s]+", phone):
        return False
    if "+" in phone and (phone.count("+") != 1 or not phone.startswith("+")):
        return False
    digits = re.sub(r"\D+", "", phone)
    return 7 <= len(digits) <= 15


def _normalized_product_name_tokens(text: str) -> set[str]:
    tokens = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in {
            "a",
            "an",
            "and",
            "ang",
            "ano",
            "anong",
            "any",
            "ba",
            "item",
            "items",
            "itong",
            "iyong",
            "kayo",
            "kayong",
            "magkano",
            "may",
            "meron",
            "ng",
            "of",
            "po",
            "presyo",
            "product",
            "products",
            "the",
            "yung",
            "your",
        }:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        tokens.add(word)
    return tokens



def _safe_history(history: Iterable[dict] | None) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for item in list(history or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()[:MAX_HISTORY_CONTENT_LENGTH]
        if content:
            safe.append({"role": item["role"], "content": content})
    return safe


def _conversation_is_related(
    message: str,
    history: Iterable[dict] | None = None,
    *,
    has_product_request: bool = False,
) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    explicitly_supported = bool(words & SUPPORTED_SERVICE_TERMS) or "how much" in message.lower()
    if (
        has_product_request
        or explicitly_supported
        or is_reseller_intent(message)
        or is_greeting(message)
        or _asks_business_hours(message)
        or _asks_business_location(message)
        or _asks_delivery(message)
        or _asks_ordering(message)
    ):
        return True
    normalized = re.sub(r"\s+", " ", message.lower()).strip()
    contextual_follow_up = bool(
        re.match(
            r"^(?:and\b|what about\b|how about\b|how much (?:is|are) (?:it|this|that|they|those)\b|(?:is|are|does|do|can|could|would) (?:it|this|that|they|those)\b|what(?:'s| is) (?:it|this|that|they|those)\b)",
            normalized,
        )
    )
    if not contextual_follow_up:
        return False
    return any(item["role"] == "user" and is_related(item["content"]) for item in _safe_history(history))



def _asks_unconfirmed_product_detail(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    if "unrefrigerated" in words or "room temperature" in message.lower():
        return True
    return any(
        any(word == term or word.startswith(f"{term}s") for word in words)
        for term in UNCONFIRMED_PRODUCT_DETAIL_TERMS
    )


def _asks_business_hours(message: str) -> bool:
    lower = message.lower()
    return bool(
        re.search(r"\b(?:hours?|open|opening|close|closes|closed|closing)\b", lower)
        or re.search(r"\bwhat\s+time\b", lower)
        or re.search(r"\b(?:oras|sarado)\b", lower)
        or re.search(r"\bbukas\s+(?:ba\s+)?(?:kayo|ang\s+(?:shop|store|tindahan))\b", lower)
    )


def _asks_business_location(message: str) -> bool:
    lower = message.lower()
    if re.search(r"\b(?:address|located|location|nasaan)\b", lower):
        return True
    return bool(
        re.search(r"\bsaan\b", lower)
        and re.search(r"\b(?:kayo|shop|store|tindahan|office|branch)\b", lower)
    )


def _asks_delivery(message: str) -> bool:
    return bool(re.search(r"\b(?:deliver|delivery|shipping|padeliver|magpadeliver)\w*\b", message.lower()))


def _asks_ordering(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:order|ordering|umorder|mag-?order|buy|purchase|bili|bumili|website)\w*\b",
            message.lower(),
        )
    )


def _requires_grounded_service_reply(message: str) -> bool:
    lower = message.lower()
    if _asks_business_hours(message):
        return True
    if any(term in lower for term in ("phone", "email", "contact", "hotline")):
        return True
    if _asks_delivery(message) or any(term in lower for term in ("schedule", "fee")):
        return True
    if any(term in lower for term in ("payment", "pay", "bayad", "gcash")):
        return True
    if any(term in lower for term in ("discount", "promo", "bulk", "wholesale")):
        return True
    if any(term in lower for term in ("refund", "return", "complaint")):
        return True
    if _asks_ordering(message):
        return True
    return _asks_business_location(message) and not _asks_delivery(message)


def _asks_direct_catalog_fact(message: str) -> bool:
    lower = message.lower()
    filipino_availability = bool(
        re.search(r"^(?:may|meron)(?:\s+(?:ba|po|kayo|kayong))?\b", lower)
        and re.search(r"\bba\b", lower)
    )
    pack_fact = bool(
        re.search(
            r"\bpack\b.{0,35}\b(?:is|size|weighs?|weight|\d+(?:\.\d+)?\s*(?:g|kg|grams?|kilograms?))\b",
            lower,
        )
    )
    return filipino_availability or bool(re.match(r"^\s*are there any\b", lower)) or pack_fact or any(
        term in lower
        for term in (
            "how much",
            "cost",
            "price",
            "presyo",
            "magkano",
            "pack size",
            "weight",
            "grams",
            "in stock",
            "available",
            "availability",
            "cheap",
            "expensive",
            "compare",
            "comparison",
        )
    )


def _asks_private_customer_data(message: str) -> bool:
    lower = message.lower()
    people = re.search(r"\b(?:customer|customers|reseller|resellers|client|clients|user|users)\b", lower)
    private_fields = re.search(
        r"\b(?:address|addresses|email|emails|name|names|order|orders|phone|phones|"
        r"contact|contacts|account|accounts|data|details|information|list)\b",
        lower,
    )
    ownership = re.search(r"\b(?:all|another|other|their|private|personal)\b", lower)
    return bool(people and private_fields and ownership)


def _recommendation_context_active(history: Iterable[dict] | None) -> bool:
    """Return whether recent history is selecting a recommendation preference."""
    for item in _safe_history(history):
        content = item["content"].lower()
        if item["role"] == "user" and _has_recommendation_intent(content):
            return True
        if item["role"] == "assistant" and any(
            marker in content
            for marker in (
                "for your recommendation",
                "maximum amount you want to spend",
                "which catalog category do you prefer",
                "what pack size are you looking for",
            )
        ):
            return True
    return False


def _normalized_pack_measurement(value: str) -> str | None:
    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(g|kg|gram|grams|kilogram|kilograms)\b",
        value.lower(),
    )
    if not match:
        return None
    amount = Decimal(match.group(1)).normalize()
    amount_text = format(amount, "f").rstrip("0").rstrip(".") if "." in format(amount, "f") else format(amount, "f")
    unit = {
        "gram": "g",
        "grams": "g",
        "kilogram": "kg",
        "kilograms": "kg",
    }.get(match.group(2), match.group(2))
    return f"{amount_text} {unit}"


def _recommendation_budget(message: str, allow_bare_amount: bool = False) -> tuple[Decimal, bool] | None:
    lower = message.lower()
    patterns: tuple[tuple[str, bool], ...] = (
        (r"\b(?:under|below|less\s+than)\s*(?:php|₱)?\s*([\d,]+(?:\.\d{1,2})?)\b", False),
        (r"\b(?:up\s+to|maximum|max|budget(?:\s+is|\s+of)?)\s*(?:php|₱)?\s*([\d,]+(?:\.\d{1,2})?)\b", True),
        (r"(?:\bphp\b|₱)\s*([\d,]+(?:\.\d{1,2})?)\s+budget\b", True),
    )
    if allow_bare_amount:
        patterns += (
            (r"^\s*(?:budget\s*)?(?:php\s*|₱\s*)?([\d,]+(?:\.\d{1,2})?)\s*$", True),
        )
    for pattern, inclusive in patterns:
        match = re.search(pattern, lower)
        if not match:
            continue
        try:
            return Decimal(match.group(1).replace(",", "")), inclusive
        except InvalidOperation:
            return None
    return None


def _recommendation_category(
    message: str,
    history: Iterable[dict] | None = None,
) -> str | None:
    match = re.search(r"\b(beef|chicken|pork)\b", message.lower())
    if match:
        return match.group(1)
    for item in reversed(_safe_history(history)):
        if item["role"] != "user":
            continue
        normalized = re.sub(r"\s+", " ", item["content"].lower()).strip()
        match = re.fullmatch(
            r"(beef|chicken|pork)(?:\s+(?:category|products?))?",
            normalized,
        )
        if match:
            return match.group(1)
    return None


def _has_recommendation_intent(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:recommend|recommendation|suggest|best\s+(?:product|seller))\b",
            message.lower(),
        )
    )


def _asks_generic_recommendation(message: str) -> bool:
    lower = message.lower()
    if not _has_recommendation_intent(message):
        return False
    has_preference = bool(
        re.search(
            r"\b(?:avoid|budget|cheap|cheaper|price|pack|beef|chicken|pork|"
            r"cannot|can't|cant|without|allergy|allergic)\b",
            lower,
        )
    )
    return not has_preference and _recommendation_budget(message) is None


def _asks_budget_recommendation(message: str) -> bool:
    return _has_recommendation_intent(message) and _recommendation_budget(message) is not None


def _is_recommendation_follow_up(message: str, history: Iterable[dict] | None) -> bool:
    if not _recommendation_context_active(history):
        return False
    lower = re.sub(r"\s+", " ", message.lower()).strip()
    if _recommendation_budget(message, allow_bare_amount=True) is not None:
        return True
    return bool(
        re.fullmatch(r"(?:meat\s+)?category|budget|(?:pack\s+)?size|pack\s+size", lower)
        or re.fullmatch(r"(?:beef|chicken|pork)(?:\s+(?:category|products?))?", lower)
        or re.fullmatch(r"\d+(?:\.\d+)?\s*(?:g|kg|grams?|kilograms?)", lower)
    )


def _asks_catalog_listing(message: str) -> bool:
    lower = re.sub(r"\s+", " ", message.lower()).strip()
    if re.search(r"\b(?:show|view|list|browse)\b.*\b(?:catalog|products?)\b", lower):
        return True
    if re.search(r"\bwhat\b.*\bproducts?\b.*\b(?:available|carry|have|offer|sell)\b", lower):
        return True
    return bool(
        re.search(r"\b(?:ano|anong)\b.*\b(?:product|products|produkto)\b", lower)
        and re.search(r"\b(?:available|may|meron)\b", lower)
    )


def _asks_reseller_requirements(message: str) -> bool:
    lower = message.lower()
    return is_reseller_intent(lower) and any(
        term in lower
        for term in (
            "capital",
            "document",
            "fee",
            "minimum",
            "qualification",
            "qualify",
            "requirement",
        )
    )



__all__ = [
    "clean_reply",
    "is_related",
    "is_greeting",
    "prefers_filipino",
    "is_prompt_attack",
    "is_identity_question",
    "is_human_handoff_intent",
    "is_reseller_intent",
    "is_reseller_signup_intent",
    "is_yes",
    "is_no",
    "_contains_phrase",
    "_is_valid_email",
    "_is_valid_contact_number",
    "_normalized_product_name_tokens",
    "_safe_history",
    "_conversation_is_related",
    "_asks_unconfirmed_product_detail",
    "_asks_business_hours",
    "_asks_business_location",
    "_asks_delivery",
    "_asks_ordering",
    "_requires_grounded_service_reply",
    "_asks_direct_catalog_fact",
    "_asks_private_customer_data",
    "_recommendation_context_active",
    "_normalized_pack_measurement",
    "_recommendation_budget",
    "_recommendation_category",
    "_has_recommendation_intent",
    "_asks_generic_recommendation",
    "_asks_budget_recommendation",
    "_is_recommendation_follow_up",
    "_asks_catalog_listing",
    "_asks_reseller_requirements",
]

