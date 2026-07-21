from __future__ import annotations

import re

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
- Be professional, clear, and customer-service focused.
- Do not answer unrelated questions.
- Do not invent products, prices, requirements, schedules, or policies.
- If details are unavailable, say: "Please contact Batangas Premium directly for complete details."
- If the question is not about Batangas Premium, products, prices, ordering, delivery, or reseller inquiries, reply exactly:
"Sorry, I can only assist with Batangas Premium-related concerns."
"""

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


def clean_reply(reply: str) -> str:
    text = re.sub(r"\s+", " ", reply).strip()
    if not text:
        return CONTACT_REPLY
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:3]).strip()


def is_related(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & ALLOWED_TERMS)


def is_reseller_intent(message: str) -> bool:
    lower = message.lower()
    return any(term in lower for term in ("reseller", "partner", "partnership", "distributor", "business opportunity", "be a seller"))


def is_yes(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & YES_WORDS)


def is_no(message: str) -> bool:
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & NO_WORDS)


def fallback_reply(message: str) -> str:
    lower = message.lower()
    if not is_related(lower):
        return UNRELATED_REPLY

    matched_prices = []
    for term, reply in PRODUCT_PRICES.items():
        if term in lower and reply not in matched_prices:
            matched_prices.append(reply)

    if "price" in lower or "prices" in lower or "how much" in lower:
        if matched_prices:
            return " ".join(matched_prices[:3])
        return "Product prices range from PHP 60 per pack to PHP 27,997 for distributor packages. Ask for a specific product for the exact price."

    if matched_prices:
        return " ".join(matched_prices[:3])

    if "order" in lower or "ordering" in lower or "website" in lower or "hotline" in lower:
        return "Customers may order through authorized Batangas Premium channels. Orders depend on product availability."

    if "deliver" in lower or "delivery" in lower or "schedule" in lower or "fee" in lower:
        return "Delivery schedules and fees depend on location. A team leader can confirm the details for your area."

    if is_reseller_intent(lower) or "inquiry" in lower:
        return "I can help collect your reseller details first, then connect you with an available sales team leader."

    return CONTACT_REPLY


def ask_chatbot(message: str) -> str:
    if not message.strip():
        return CONTACT_REPLY

    if not is_related(message):
        return UNRELATED_REPLY

    if not OPENROUTER_API_KEY:
        return fallback_reply(message)

    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.strip()},
            ],
            temperature=0.2,
            max_tokens=120,
        )
    except Exception:
        return CONTACT_REPLY

    content = response.choices[0].message.content if response.choices else ""
    reply = clean_reply(content or "")

    if reply != UNRELATED_REPLY and not is_related(reply):
        return CONTACT_REPLY
    return reply


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


def process_chatbot_message(message: str, state: dict | None = None) -> dict:
    text = message.strip()
    state = dict(state or {})
    if not text:
        return {"reply": CONTACT_REPLY, "state": state}

    if state.get("mode") != "lead":
        if is_reseller_intent(text):
            state = _empty_lead_state()
            return {"reply": "I can help with reseller inquiries. What is your full name?", "state": state}
        return {"reply": ask_chatbot(text), "state": state}

    step = state.get("step", "collect")
    data = state.setdefault("data", {})

    if step == "collect":
        missing = _next_missing_field(state)
        if missing is None:
            state["step"] = "confirm"
            return {"reply": _lead_summary(data), "state": state}

        index, field, prompt = missing
        if field == "email" and not EMAIL_RE.match(text.lower()):
            return {"reply": "Please enter a valid email address.", "state": state}
        if field == "contact_number" and len(re.sub(r"\D+", "", text)) < 7:
            return {"reply": "Please enter a valid contact number.", "state": state}
        if len(text) < 2:
            return {"reply": prompt, "state": state}

        data[field] = text
        next_field = _next_missing_field(state)
        if next_field is None:
            state["step"] = "confirm"
            return {"reply": _lead_summary(data), "state": state}
        state["field_index"] = next_field[0]
        return {"reply": next_field[2], "state": state}

    if step == "confirm":
        if is_yes(text):
            state["step"] = "connect"
            return {"reply": "Do you want me to connect you with an available sales team leader now?", "state": state}
        if is_no(text):
            new_state = _empty_lead_state()
            return {"reply": "No problem. Let's update your details. What is your full name?", "state": new_state}
        return {"reply": "Please answer yes if the details are correct, or no if you want to change them.", "state": state}

    if step == "connect":
        if is_yes(text):
            state["step"] = "submitted"
            return {
                "reply": "Thank you. I will send your details to an available sales team leader.",
                "state": state,
                "action": "create_lead",
                "lead": data,
            }
        if is_no(text):
            state["step"] = "done"
            return {"reply": "Understood. Your details were not submitted. You can message me again when you are ready.", "state": state}
        return {"reply": "Please answer yes if you want to talk to an available team leader, or no to cancel.", "state": state}

    if step in {"submitted", "done"}:
        return {"reply": ask_chatbot(text), "state": {}}

    return {"reply": ask_chatbot(text), "state": {}}
