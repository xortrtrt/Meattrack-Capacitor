from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from openai import OpenAI

from app.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

UNRELATED_REPLY = "Sorry, I can only assist with Batangas Premium-related concerns."
CONTACT_REPLY = "Please contact Batangas Premium directly for complete details."

SYSTEM_PROMPT = """
You are the official Batangas Premium customer support assistant.

Your job is to answer FAQs about Batangas Premium only:
- products
- prices
- ordering
- delivery
- reseller inquiries

Rules:
- Answer in 1 to 3 short sentences only.
- Reply in the same language as the customer when they use English, Filipino, or Taglish.
- Use the supplied current catalog as the source of truth for product names and prices.
- Use conversation history to understand short follow-up questions such as "how much is it?".
- Be professional, clear, and customer-service focused.
- Do not answer unrelated questions.
- Do not invent products, prices, requirements, schedules, or policies.
- If details are unavailable, say: "Please contact Batangas Premium directly for complete details."
- If the question is not about Batangas Premium, products, prices, ordering, delivery, or reseller inquiries, reply exactly:
"Sorry, I can only assist with Batangas Premium-related concerns."
"""

MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CONTENT_LENGTH = 300
MAX_MESSAGE_LENGTH = 500

PRODUCT_PRICES = {
    "cheesy overload sausage": "Cheesy Overload Sausage is PHP 129 per pack.",
    "deli beef": "Deli Beef is PHP 125 per pack.",
    "hungarian sausage": "Hungarian Sausage is PHP 109 per pack.",
    "bacon": "Bacon is PHP 129 per pack.",
    "pork rebusado": "Pork Rebusado is PHP 90 per pack.",
    "chicken rebusado": "Chicken Rebusado is PHP 85 per pack.",
    "trial package": "Trial Package is PHP 1,399.",
    "reseller package": "Reseller Package is PHP 4,997.",
    "pork garlic longganisa": "Pork Garlic Longganisa is PHP 60 per pack.",
    "spicy garlic longganisa": "Spicy Garlic Longganisa is PHP 64 per pack.",
    "triple garlic longganisa": "Triple Garlic Longganisa is PHP 64 per pack.",
    "beef longganisa": "Beef Longganisa is PHP 75 per pack.",
    "tocino ala eh": "Tocino Ala Eh is PHP 70 per pack.",
    "tocino": "Tocino Ala Eh is PHP 70 per pack.",
    "chicken tocino": "Chicken Tocino is PHP 70 per pack.",
    "pork tapa": "Pork Tapa is PHP 79 per pack.",
    "beef tapa": "Beef Tapa Ala Eh is PHP 99 per pack.",
    "beef tapa ala eh": "Beef Tapa Ala Eh is PHP 99 per pack.",
    "hamon ala eh": "Hamon Ala Eh is PHP 99 per pack.",
    "area distributor": "Area Distributor's Package is PHP 27,997.",
}

ALLOWED_TERMS = {
    "batangas",
    "premium",
    "product",
    "products",
    "price",
    "prices",
    "order",
    "ordering",
    "delivery",
    "deliver",
    "reseller",
    "resellers",
    "inquiry",
    "partner",
    "partnership",
    "distributor",
    "business",
    "tocino",
    "longganisa",
    "tapa",
    "sausage",
    "bacon",
    "hungarian",
    "hamon",
    "rebusado",
    "hotline",
    "website",
    "availability",
    "available",
    "fee",
    "fees",
    "schedule",
    "schedules",
    "catalog",
    "pack",
    "packs",
    "stock",
    "buy",
    "purchase",
    "magkano",
    "presyo",
    "produkto",
    "bili",
    "padeliver",
    "negosyo",
}

LEAD_FIELDS = [
    ("name", "What is your full name?"),
    ("business_name", "What is your business name?"),
    ("email", "What email address should our team use?"),
    ("contact_number", "What phone number can our team contact?"),
    ("location", "Where is your store or selling area located?"),
    ("interest", "What are you interested in: reseller, distributor, or specific products?"),
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
YES_WORDS = {"yes", "y", "sure", "okay", "ok", "opo", "oo", "connect", "confirm", "go"}
NO_WORDS = {"no", "n", "not", "hindi", "cancel", "stop"}
CANCEL_WORDS = {"cancel", "stop", "quit", "exit", "nevermind", "ayoko", "tigil"}
BACK_WORDS = {"back", "previous", "undo", "balik"}
RESTART_WORDS = {"restart", "start over", "reset", "ulit"}
GREETING_RE = re.compile(r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|kumusta|kamusta|mabuhay)[!.\s]*$", re.I)

FIELD_ALIASES = {
    "name": {"name", "full name", "pangalan"},
    "business_name": {"business", "business name", "store", "store name", "shop"},
    "email": {"email", "email address"},
    "contact_number": {"phone", "phone number", "contact", "contact number", "mobile", "number"},
    "location": {"location", "address", "area", "lugar"},
    "interest": {"interest", "package", "product", "products"},
}

LEAD_FIELD_MAX_LENGTHS = {
    "name": 120,
    "business_name": 160,
    "email": 254,
    "contact_number": 40,
    "location": 200,
    "interest": 240,
}


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


def is_reseller_intent(message: str) -> bool:
    lower = message.lower()
    return any(term in lower for term in ("reseller", "partner", "partnership", "distributor", "business opportunity", "be a seller"))


def is_yes(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & YES_WORDS)


def is_no(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & NO_WORDS)


def _contains_phrase(message: str, phrases: set[str]) -> bool:
    normalized = re.sub(r"\s+", " ", message.lower()).strip()
    words = set(re.findall(r"[a-z]+", normalized))
    return normalized in phrases or bool(words & phrases)


def _safe_history(history: Iterable[dict] | None) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for item in list(history or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()[:MAX_HISTORY_CONTENT_LENGTH]
        if content:
            safe.append({"role": item["role"], "content": content})
    return safe


def _conversation_is_related(message: str, history: Iterable[dict] | None = None) -> bool:
    if is_related(message) or is_greeting(message):
        return True
    return any(item["role"] == "user" and is_related(item["content"]) for item in _safe_history(history))


def _format_price(value: object) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if amount == amount.to_integral_value():
        return f"PHP {amount:,.0f}"
    return f"PHP {amount:,.2f}"


def _catalog_entries(catalog: Iterable[dict] | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in list(catalog or [])[:60]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        price = _format_price(item.get("base_price"))
        if not name:
            continue
        entries.append(
            {
                "name": name,
                "price": price,
                "category": str(item.get("category", "")).strip(),
                "pack": " ".join(
                    part
                    for part in (
                        str(item.get("pack_size", "")).strip(),
                        str(item.get("pack_size_unit", "")).strip(),
                    )
                    if part and part.lower() != "none"
                ),
            }
        )
    return entries


def _catalog_prompt(catalog: Iterable[dict] | None) -> str:
    entries = _catalog_entries(catalog)
    if not entries:
        return "Current catalog: No live catalog data is available. Do not invent product or price details."
    lines = []
    for item in entries:
        details = ", ".join(value for value in (item["price"], item["category"], item["pack"]) if value)
        lines.append(f"- {item['name']}: {details or 'details unavailable'}")
    return "Current active catalog:\n" + "\n".join(lines)


def fallback_reply(
    message: str,
    catalog: Iterable[dict] | None = None,
    history: Iterable[dict] | None = None,
) -> str:
    lower = message.lower()
    if is_greeting(message):
        return "Hello! I can help with Batangas Premium products, prices, delivery, ordering, or reseller inquiries."
    if not _conversation_is_related(message, history):
        return UNRELATED_REPLY

    history_text = " ".join(
        item["content"].lower()
        for item in _safe_history(history)
        if item["role"] == "user"
    )
    lookup_text = lower
    if not is_related(message) and history_text:
        lookup_text = f"{history_text} {lower}"

    matched_prices = []
    catalog_entries = _catalog_entries(catalog)
    if catalog_entries:
        for item in sorted(catalog_entries, key=lambda entry: len(entry["name"]), reverse=True):
            if item["name"].lower() in lookup_text and item["price"]:
                reply = f"{item['name']} is {item['price']}."
                if reply not in matched_prices:
                    matched_prices.append(reply)
    else:
        for term, reply in PRODUCT_PRICES.items():
            if term in lookup_text and reply not in matched_prices:
                matched_prices.append(reply)

    asks_price = any(term in lower for term in ("price", "prices", "how much", "magkano", "presyo"))
    if asks_price:
        if matched_prices:
            return " ".join(matched_prices[:3])
        if catalog_entries:
            examples = ", ".join(
                f"{item['name']} ({item['price']})" for item in catalog_entries[:4] if item["price"]
            )
            if examples:
                return f"Current prices include {examples}. Ask me for a specific product if you need another price."
        return "Product prices range from PHP 60 per pack to PHP 27,997 for distributor packages. Ask for a specific product for the exact price."

    if matched_prices:
        return " ".join(matched_prices[:3])

    if any(term in lower for term in ("product", "products", "catalog", "available", "availability", "produkto")):
        if catalog_entries:
            names = ", ".join(item["name"] for item in catalog_entries[:5])
            suffix = "" if len(catalog_entries) <= 5 else ", and more"
            return f"Our current catalog includes {names}{suffix}. Ask for a product name to check its price."
        return CONTACT_REPLY

    if "order" in lower or "ordering" in lower or "website" in lower or "hotline" in lower:
        return "Customers may order through authorized Batangas Premium channels. Orders depend on product availability."

    if "deliver" in lower or "delivery" in lower or "schedule" in lower or "fee" in lower:
        return "Delivery schedules and fees depend on location. A team leader can confirm the details for your area."

    if is_reseller_intent(lower) or "inquiry" in lower:
        return "I can help collect your reseller details first, then connect you with an available sales team leader."

    return CONTACT_REPLY


def ask_chatbot(
    message: str,
    history: Iterable[dict] | None = None,
    catalog: Iterable[dict] | None = None,
) -> str:
    if not message.strip():
        return CONTACT_REPLY

    safe_history = _safe_history(history)
    if not _conversation_is_related(message, safe_history):
        return UNRELATED_REPLY

    if not OPENROUTER_API_KEY:
        return fallback_reply(message, catalog=catalog, history=safe_history)

    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": f"{SYSTEM_PROMPT.strip()}\n\n{_catalog_prompt(catalog)}"},
                *safe_history,
                {"role": "user", "content": message.strip()},
            ],
            temperature=0.2,
            max_tokens=120,
        )
    except Exception:
        return CONTACT_REPLY

    content = response.choices[0].message.content if response.choices else ""
    reply = clean_reply(content or "")

    return reply


def _support_result(reply: str, message: str, history: Iterable[dict] | None = None) -> dict:
    next_history = _safe_history(history)
    next_history.extend(
        [
            {"role": "user", "content": message.strip()[:MAX_HISTORY_CONTENT_LENGTH]},
            {"role": "assistant", "content": reply[:MAX_HISTORY_CONTENT_LENGTH]},
        ]
    )
    return {
        "reply": reply,
        "state": {"mode": "support", "history": next_history[-MAX_HISTORY_MESSAGES:]},
        "suggestions": ["View products", "Delivery details", "Become a reseller"],
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
) -> dict:
    text = message.strip()[:MAX_MESSAGE_LENGTH]
    state = dict(state or {})
    if not text:
        return {"reply": CONTACT_REPLY, "state": state}

    normalized_quick_reply = re.sub(r"\s+", " ", text.lower()).strip()
    if normalized_quick_reply in {"view products", "delivery details"}:
        history = state.get("history") if state.get("mode") == "support" else []
        reply = ask_chatbot(text, history=history, catalog=catalog)
        return _support_result(reply, text, history)
    if normalized_quick_reply == "become a reseller":
        state = _empty_lead_state()
        return {
            "reply": "I can help with reseller inquiries. What is your full name? You can type ‘cancel’ anytime.",
            "state": state,
            "suggestions": ["Cancel"],
        }

    if state.get("mode") != "lead":
        if is_reseller_intent(text):
            state = _empty_lead_state()
            return {
                "reply": "I can help with reseller inquiries. What is your full name? You can type ‘cancel’ anytime.",
                "state": state,
                "suggestions": ["Cancel"],
            }
        history = state.get("history") if state.get("mode") == "support" else []
        reply = ask_chatbot(text, history=history, catalog=catalog)
        return _support_result(reply, text, history)

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
        max_length = LEAD_FIELD_MAX_LENGTHS[field]
        if len(text) > max_length:
            return {
                "reply": f"Please keep that detail under {max_length} characters.",
                "state": state,
                "suggestions": ["Back", "Cancel"],
            }
        if field == "email" and not EMAIL_RE.match(text.lower()):
            return {"reply": "Please enter a valid email address, or type ‘back’ or ‘cancel’.", "state": state, "suggestions": ["Back", "Cancel"]}
        phone_digits = re.sub(r"\D+", "", text)
        phone_has_valid_characters = bool(re.fullmatch(r"[0-9+().\-\s]+", text))
        if field == "contact_number" and (not phone_has_valid_characters or not 7 <= len(phone_digits) <= 15):
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
        field = _field_from_message(text)
        if field:
            data.pop(field, None)
            state["step"] = "collect"
            return {"reply": dict(LEAD_FIELDS)[field], "state": state, "suggestions": ["Cancel"]}
        return {"reply": "Please answer yes, or tell me which detail you want to change.", "state": state, "suggestions": ["Yes", "Change email", "Cancel"]}

    if step == "edit":
        field = _field_from_message(text)
        if not field:
            return {
                "reply": "Please choose name, business, email, phone, location, or interest.",
                "state": state,
                "suggestions": ["Name", "Email", "Phone", "Location", "Cancel"],
            }
        data.pop(field, None)
        state["step"] = "collect"
        return {"reply": dict(LEAD_FIELDS)[field], "state": state, "suggestions": ["Cancel"]}

    if step == "connect":
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
        reply = ask_chatbot(text, catalog=catalog)
        return _support_result(reply, text)

    reply = ask_chatbot(text, catalog=catalog)
    return _support_result(reply, text)
