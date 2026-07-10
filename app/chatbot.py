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

Use only this approved information:

Products and prices:
- Pork Tocino: PHP 180 per pack
- Pork Longganisa: PHP 160 per pack
- Beef Tapa: PHP 220 per pack
- Skinless Sausage: PHP 150 per pack
- Bacon: PHP 250 per pack
- Hungarian Sausage: PHP 210 per pack

Ordering:
- Customers may order through the website, hotline, or authorized resellers.
- Orders are subject to product availability.

Delivery:
- Delivery schedules vary depending on location.
- Delivery fees may apply.

Reseller process:
1. Submit a reseller inquiry.
2. The Business Development team reviews the application.
3. Submit the required documents.
4. Approved applicants receive onboarding instructions.

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
    "pork tocino": "Pork Tocino is PHP 180 per pack.",
    "tocino": "Pork Tocino is PHP 180 per pack.",
    "pork longganisa": "Pork Longganisa is PHP 160 per pack.",
    "longganisa": "Pork Longganisa is PHP 160 per pack.",
    "beef tapa": "Beef Tapa is PHP 220 per pack.",
    "tapa": "Beef Tapa is PHP 220 per pack.",
    "skinless sausage": "Skinless Sausage is PHP 150 per pack.",
    "bacon": "Bacon is PHP 250 per pack.",
    "hungarian sausage": "Hungarian Sausage is PHP 210 per pack.",
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
    "tocino",
    "longganisa",
    "tapa",
    "sausage",
    "bacon",
    "hungarian",
    "hotline",
    "website",
    "availability",
    "available",
    "fee",
    "fees",
    "schedule",
    "schedules",
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
        return (
            "Available prices are Pork Tocino PHP 180, Pork Longganisa PHP 160, "
            "Beef Tapa PHP 220, Skinless Sausage PHP 150, Bacon PHP 250, and Hungarian Sausage PHP 210 per pack."
        )

    if matched_prices:
        return " ".join(matched_prices[:3])

    if "order" in lower or "ordering" in lower or "website" in lower or "hotline" in lower:
        return "Customers may order through the website, hotline, or authorized resellers. Orders are subject to product availability."

    if "deliver" in lower or "delivery" in lower or "schedule" in lower or "fee" in lower:
        return "Delivery schedules vary depending on location. Delivery fees may apply."

    if "reseller" in lower or "inquiry" in lower:
        return (
            "Submit a reseller inquiry first. The Business Development team reviews the application, requests required documents, "
            "and approved applicants receive onboarding instructions."
        )

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
