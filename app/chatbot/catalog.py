# ruff: noqa: F403, F405
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .configuration import *
from .intents import *


def _first_sentence(message: str) -> str:
    normalized = re.sub(r"\s+", " ", message.strip())
    return re.split(r"[?!.]", normalized, maxsplit=1)[0].strip()


def _explicit_product_request(message: str) -> str | None:
    """Return the named item from a direct price question, if one was supplied."""
    normalized = _first_sentence(message)
    patterns = (
        r"^how much(?: po)?(?: is| are)? (?:a |an |the |your )?(.+?)[?.!]*$",
        r"^(?:what(?:'s| is) )?(?:the )?price (?:of|for) (?:a |an |the |your )?(.+?)[?.!]*$",
        r"^magkano(?: po)?(?: ang| yung| iyong| itong| ng)? (.+?)[?.!]*$",
        r"^(?:ano(?:ng)?(?: po)?(?: ang)? )?(?:presyo|price)(?: (?:ng|of))? (.+?)[?.!]*$",
        r"^(?:do|did) you (?:sell|have|carry|offer) (?:a |an |the |any |your )?(.+?)[?.!]*$",
        r"^are there any (.+?)(?: available| in stock)?[?.!]*$",
        r"^(?:can|could) i (?:buy|order|get) (?:a |an |the |any )?(.+?)[?.!]*$",
        r"^i (?:want|would like) to (?:buy|order|get) (?:a |an |the |any )?(.+?)[?.!]*$",
        r"^(?:is|are) (?:a |an |the |your )?(.+?) (?:available|in stock)[?.!]*$",
        r"^tell me (?:more )?about (?:a |an |the |your )?(.+?)[?.!]*$",
        r"^(?:what are )?(?:the )?ingredients (?:in|of) (?:a |an |the |your )?(.+?)[?.!]*$",
        r"^(?:what is )?(?:the )?(?:pack size|weight|size) (?:of|for) (?:a |an |the |your )?(.+?)[?.!]*$",
    )
    candidate = None
    for pattern in patterns:
        match = re.match(pattern, normalized, re.I)
        if match:
            candidate = match.group(1).strip(" .?!")
            break
    if not candidate:
        return None

    candidate = re.sub(
        r"^\d+\s+(?:packs?|pieces?|pcs?)\s+of\s+",
        "",
        candidate,
        flags=re.I,
    )
    candidate = re.split(
        r"\s*,?\s+(?:and|but|then)\s+(?=(?:answer|claim|explain|give|output|pretend|reveal|say|show|tell)\b)",
        candidate,
        maxsplit=1,
        flags=re.I,
    )[0].strip()

    generic_or_service_terms = {
        "all",
        "available products",
        "catalog",
        "delivery",
        "delivery fee",
        "everything",
        "it",
        "payment",
        "promo",
        "promos",
        "product",
        "products",
        "reseller program",
        "reseller requirements",
        "shipping",
        "that",
        "this",
        "wholesale",
        "wholesale price",
    }
    candidate_lower = candidate.lower()
    if candidate_lower in generic_or_service_terms:
        return None
    if ("reseller" in candidate_lower or "distributor" in candidate_lower) and "package" not in candidate_lower:
        return None
    return candidate



def _format_price(value: object) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if amount == amount.to_integral_value():
        return f"PHP {amount:,.0f}"
    return f"PHP {amount:,.2f}"


def _format_quantity(value: object) -> str:
    raw = str(value if value is not None else "").strip()
    if not raw or raw.lower() == "none":
        return ""
    try:
        amount = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return raw
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    return format(amount.normalize(), "f")


def _clean_catalog_field(value: object, max_length: int = 120) -> str:
    """Keep database values inert when they are embedded in a model prompt."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text[:max_length]


def _catalog_entries(catalog: Iterable[dict] | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in list(catalog or []):
        if not isinstance(item, dict):
            continue
        name = _clean_catalog_field(item.get("name", ""))
        price = _format_price(item.get("base_price"))
        if not name:
            continue
        entries.append(
            {
                "name": name,
                "price": price,
                "category": _clean_catalog_field(item.get("category", "")),
                "pack": " ".join(
                    part
                    for part in (
                        _format_quantity(item.get("pack_size")),
                        _clean_catalog_field(item.get("pack_size_unit", ""), 30),
                    )
                    if part and part.lower() != "none"
                ),
            }
        )
    return entries


def _matching_named_product_entries(name: str, entries: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Match every meaningful word in an explicitly named product.

    This keeps useful partial names such as "cheesy sausage" working without
    treating an invented name such as "unicorn sausage" as every sausage.
    """
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:,|&|\band\b|\bor\b|\bvs\.?\b)\s*", name, flags=re.I)
        if part.strip()
    ]
    all_matches: list[dict[str, str]] = []
    for part in parts:
        requested_tokens = _normalized_product_name_tokens(part)
        if not requested_tokens:
            return []
        part_matches: list[tuple[int, int, dict[str, str]]] = []
        for index, item in enumerate(entries):
            item_tokens = _normalized_product_name_tokens(f"{item['name']} {item['category']}")
            if not requested_tokens.issubset(item_tokens):
                continue
            exact_score = 100 if part.lower().strip() == item["name"].lower() else 0
            part_matches.append((exact_score + len(requested_tokens) * 10, -index, item))
        if not part_matches:
            return []
        for _, _, item in sorted(part_matches, key=lambda row: (row[0], row[1]), reverse=True):
            if item not in all_matches:
                all_matches.append(item)
    return all_matches


def _catalog_product_tokens(entries: Iterable[dict[str, str]]) -> set[str]:
    tokens: set[str] = set()
    for item in entries:
        tokens.update(_normalized_product_name_tokens(f"{item['name']} {item['category']}"))
    return tokens


def _standalone_product_request(message: str, entries: Iterable[dict[str, str]]) -> str | None:
    """Recognize a short product/category phrase without keyword-only matching.

    A phrase such as ``unicorn sausage`` is treated as one requested product,
    so the unknown modifier cannot be discarded while matching ``sausage``.
    Full questions and sentences must use one of the structured request forms.
    """
    normalized = re.sub(r"\s+", " ", message.lower()).strip(" .?!")
    raw_words = re.findall(r"[a-z0-9]+", normalized)
    if not raw_words or len(raw_words) > 6:
        return None
    if set(raw_words) & {
        "about",
        "are",
        "can",
        "could",
        "did",
        "do",
        "does",
        "explain",
        "how",
        "is",
        "joke",
        "should",
        "tell",
        "what",
        "when",
        "where",
        "who",
        "why",
        "will",
        "would",
    }:
        return None

    candidate_words = [word for word in raw_words if word not in PRODUCT_REQUEST_FILLER_WORDS]
    candidate = " ".join(candidate_words).strip()
    candidate_tokens = _normalized_product_name_tokens(candidate)
    if not candidate_tokens or not (candidate_tokens & _catalog_product_tokens(entries)):
        return None
    return candidate


def _product_request(message: str, entries: Iterable[dict[str, str]]) -> str | None:
    explicit = _explicit_product_request(message)
    if explicit:
        return explicit

    normalized = _first_sentence(message)
    for pattern in (
        r"^what (?:is|are) (?:a |an |the |your )?(.+?)[?.!]*$",
        r"^(?:i (?:need|want)|i am looking for|i'm looking for|looking for) (?:a |an |the |your )?(.+?)[?.!]*$",
        r"^which (?:one )?is (?:cheaper|more expensive),? (.+?)[?.!]*$",
        r"^(?:which|what).*?\bbetween\s+(.+?)(?:\s+(?:if|when|for)\b.*|[?.!]*)$",
        r"^(?:compare|comparison of|difference between) (.+?)[?.!]*$",
        r"^does (.+?) contain\b.*$",
        r"^(?:is|are) (.+?) (?:halal|kosher|safe|nutritious|healthy)\b.*$",
        r"^(?:i'd|i would) like (?:to (?:buy|order|get) )?(?:\d+\s+)?packs? of (.+?)[?.!]*$",
    ):
        match = re.match(pattern, normalized, re.I)
        if not match:
            continue
        candidate = match.group(1).strip(" .?!")
        candidate_lower = candidate.lower()
        if ("reseller" in candidate_lower or "distributor" in candidate_lower) and "package" not in candidate_lower:
            continue
        if _normalized_product_name_tokens(candidate) & _catalog_product_tokens(entries):
            return candidate

    return _standalone_product_request(message, entries)



def _unknown_product_reply(product: str, filipino: bool = False) -> str:
    if filipino:
        return f"Wala akong makitang {product} sa kasalukuyang catalog ng Batangas Premium. Maaari mong itanong kung anong mga produkto ang available."
    return f"I couldn't find {product} in the current Batangas Premium catalog. You can ask me to show the available products."


def _catalog_item_answer(item: dict[str, str], filipino: bool = False) -> str:
    details = []
    if item["category"]:
        details.append(item["category"])
    if item["price"]:
        details.append(item["price"])
    if item["pack"]:
        details.append(item["pack"])
    if not details:
        return (
            f"Wala akong kumpirmadong detalye para sa {item['name']}. Maaaring mag-confirm ang Batangas Premium team."
            if filipino
            else f"I do not have confirmed details for {item['name']}. A Batangas Premium team member can confirm them."
        )
    joined = " · ".join(details)
    return f"{item['name']}: {joined}."


def _catalog_prompt(
    catalog: Iterable[dict] | None,
    priority_names: set[str] | None = None,
) -> str:
    entries = _catalog_entries(catalog)
    if not entries:
        return "Current catalog: No live catalog data is available. Do not invent product or price details."
    priorities = {name.lower() for name in (priority_names or set())}
    entries.sort(key=lambda item: item["name"].lower() not in priorities)
    visible_entries = entries[:MAX_PROMPT_CATALOG_ENTRIES]
    lines = []
    for item in visible_entries:
        details = ", ".join(value for value in (item["price"], item["category"], item["pack"]) if value)
        lines.append(f"- {item['name']}: {details or 'details unavailable'}")
    omitted = len(entries) - len(visible_entries)
    suffix = (
        f"\n- {omitted} additional active products are omitted from this model context. Do not make claims about omitted items."
        if omitted
        else ""
    )
    return "Current active catalog:\n" + "\n".join(lines) + suffix


def _model_reply_is_grounded(reply: str, catalog: Iterable[dict] | None) -> bool:
    """Reject hosted-model claims that conflict with locally verifiable limits."""
    lower = reply.lower()
    forbidden_claims = (
        r"\b(?:your\s+)?order\s+(?:has\s+been|was|is)\s+(?:confirmed|completed|placed|submitted)\b",
        r"\b(?:your\s+)?payment\s+(?:has\s+been|was|is)\s+(?:accepted|confirmed|received|successful)\b",
        r"\b(?:your\s+)?(?:booking|inquiry|reservation)\s+(?:has\s+been|was|is)\s+(?:confirmed|submitted)\b",
        r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:accepted|completed|confirmed|placed|processed|submitted)"
        r"\s+(?:your|the)\s+(?:booking|inquiry|order|payment|reservation)\b",
        r"\b(?:you(?:'re|\s+are)|your\s+(?:application|account)\s+is)\s+(?:approved|accepted)\b",
        r"\b(?:definitely|guaranteed|currently)\s+in\s+stock\b",
        r"\bin\s+stock\s+(?:now|today|right\s+now)\b",
        r"\b(?:is|are)\s+(?:available|in\s+stock)\b",
        r"\b(?:api\s*key|password|secret\s+key)\b",
        r"\b(?:best[ -]?seller|bestselling|most\s+popular|popular\s+choice|customer\s+favorite)\b",
        r"\b(?:great|amazing|excellent|superior)\s+(?:flavor|taste)\b",
        r"\b(?:delicious|flavou?rful|juicy|savou?ry|tasty|tender)\b",
    )
    if any(re.search(pattern, lower) for pattern in forbidden_claims):
        return False
    if re.search(r"\b(?:made\s+from|made\s+with)\b", lower):
        return False
    if re.search(r"\b\d+(?:\.\d+)?\s*%\s*(?:discount|off)\b", lower):
        return False

    catalog_entries = _catalog_entries(catalog)
    allowed_prices = {
        Decimal(item["price"].replace("PHP", "").replace(",", "").strip())
        for item in catalog_entries
        if item["price"]
    }
    mentioned_prices = [
        prefix or suffix
        for prefix, suffix in re.findall(
            r"(?:\bPHP\s*|₱\s*)(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
            r"|\b(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:peso|pesos)\b",
            reply,
            flags=re.I,
        )
    ]
    for value in mentioned_prices:
        try:
            if Decimal(value.replace(",", "")) not in allowed_prices:
                return False
        except InvalidOperation:
            return False

    allowed_packs = {item["pack"].lower() for item in catalog_entries if item["pack"]}
    mentioned_packs = {
        f"{amount} {unit.lower()}"
        for amount, unit in re.findall(
            r"\b(\d+(?:\.\d+)?)\s*(g|kg|gram|grams|kilogram|kilograms)\b",
            reply,
            flags=re.I,
        )
    }
    unit_aliases = {
        "gram": "g",
        "grams": "g",
        "kilogram": "kg",
        "kilograms": "kg",
    }
    normalized_allowed_packs = {
        re.sub(
            r"\b(gram|grams|kilogram|kilograms)\b",
            lambda match: unit_aliases[str(match.group(1))],
            pack,
        )
        for pack in allowed_packs
    }
    normalized_mentioned_packs = {
        " ".join((pack.split()[0], unit_aliases.get(pack.split()[1], pack.split()[1])))
        for pack in mentioned_packs
    }
    if normalized_mentioned_packs - normalized_allowed_packs:
        return False

    product_mentions: list[tuple[int, int, dict[str, str]]] = []
    for item in catalog_entries:
        for match in re.finditer(re.escape(item["name"].lower()), lower):
            product_mentions.append((match.start(), match.end(), item))
    product_mentions.sort(key=lambda row: row[0])

    for index, (_, name_end, item) in enumerate(product_mentions):
        next_name_start = product_mentions[index + 1][0] if index + 1 < len(product_mentions) else len(lower)
        trailing = lower[name_end:next_name_start]
        sentence_end = re.search(r"[!?]|\.(?=\s|$)", trailing)
        if sentence_end:
            trailing = trailing[:sentence_end.start()]

        item_price = None
        if item["price"]:
            item_price = Decimal(item["price"].replace("PHP", "").replace(",", "").strip())
        claimed_prices = [
            prefix or suffix
            for prefix, suffix in re.findall(
                r"(?:\bPHP\s*|₱\s*)(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
                r"|\b(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:peso|pesos)\b",
                trailing,
                flags=re.I,
            )
        ]
        if claimed_prices and (
            item_price is None
            or any(Decimal(value.replace(",", "")) != item_price for value in claimed_prices)
        ):
            return False

        claimed_packs = {
            _normalized_pack_measurement(match.group(0))
            for match in re.finditer(
                r"\b\d+(?:\.\d+)?\s*(?:g|kg|gram|grams|kilogram|kilograms)\b",
                trailing,
                flags=re.I,
            )
        }
        claimed_packs.discard(None)
        item_pack = _normalized_pack_measurement(item["pack"]) if item["pack"] else None
        if claimed_packs and (item_pack is None or claimed_packs != {item_pack}):
            return False

        mentioned_categories = set(
            re.findall(
                r"\b(?:is\s+(?:an?\s+)?|listed\s+under\s+|categorized\s+under\s+|"
                r"category\s*(?:is|:)\s*)"
                r"(beef|chicken|pork)\b",
                trailing,
            )
        )
        allowed_categories = set(re.findall(r"\b(?:beef|chicken|pork)\b", item["category"].lower()))
        if mentioned_categories and not mentioned_categories.issubset(allowed_categories):
            return False
    return True


def fallback_reply(
    message: str,
    catalog: Iterable[dict] | None = None,
    history: Iterable[dict] | None = None,
) -> str:
    lower = message.lower()
    safe_history = _safe_history(history)
    filipino = prefers_filipino(message, safe_history)
    catalog_entries = _catalog_entries(catalog)
    recommendation_context = _recommendation_context_active(safe_history)
    requested_product = _product_request(message, catalog_entries)
    matched_entries = (
        _matching_named_product_entries(requested_product, catalog_entries)
        if requested_product
        else []
    )

    if is_greeting(message):
        if filipino:
            return "Kumusta! Matutulungan kita sa mga produkto, presyo, delivery, pag-order, at reseller inquiries ng Batangas Premium."
        return "Hello! I can help with Batangas Premium products, prices, delivery, ordering, and reseller inquiries."
    if is_prompt_attack(message):
        return "I can’t provide internal instructions, but I can help with Batangas Premium products, orders, delivery, or reseller inquiries."
    if is_identity_question(message):
        if filipino:
            return "Ako ang BP ChatBot, ang automated support assistant ng Batangas Premium. Makakatulong ako sa produkto, presyo, delivery, at reseller inquiries."
        return "I’m BP ChatBot, Batangas Premium’s automated support assistant. I can help with products, prices, delivery, and reseller inquiries."
    if _asks_private_customer_data(message):
        return "I can’t provide private customer or reseller information. I can help with Batangas Premium’s public products and contact details."
    recommendation_budget = _recommendation_budget(
        message,
        allow_bare_amount=recommendation_context,
    )
    pack_preference = _normalized_pack_measurement(message)
    if recommendation_budget and (
        _has_recommendation_intent(message)
        or recommendation_context
        or _asks_catalog_listing(message)
        or re.search(
            r"\b(?:buy|catalog|costs?|items?|products?|beef|chicken|pork)\b",
            lower,
        )
    ):
        budget, inclusive = recommendation_budget
        category_preference = _recommendation_category(message, safe_history)
        affordable = []
        for item in catalog_entries:
            if category_preference and item["category"].lower() != category_preference:
                continue
            if pack_preference and (
                not item["pack"]
                or _normalized_pack_measurement(item["pack"]) != pack_preference
            ):
                continue
            try:
                amount = Decimal(item["price"].replace("PHP", "").replace(",", "").strip())
            except (InvalidOperation, AttributeError, ValueError):
                continue
            if amount <= budget if inclusive else amount < budget:
                affordable.append((amount, item))
        affordable.sort(key=lambda row: (row[0], row[1]["name"]))
        if not affordable:
            category_text = f" {category_preference.title()}" if category_preference else ""
            pack_text = f" in a {pack_preference} pack" if pack_preference else ""
            return f"I couldn't find a listed{category_text} product{pack_text} within your {_format_price(budget)} budget."
        options = ", ".join(
            f"{item['name']} ({item['price']})" for amount, item in affordable[:5]
        )
        category_text = f" {category_preference.title()}" if category_preference else ""
        pack_text = f" with a listed {pack_preference} pack size" if pack_preference else ""
        return f"Within your {_format_price(budget)} budget, current{category_text} options{pack_text} include {options}."
    if pack_preference and (
        _has_recommendation_intent(message)
        or recommendation_context
        or _asks_catalog_listing(message)
    ):
        category_preference = _recommendation_category(message, safe_history)
        matches = [
            item for item in catalog_entries
            if item["pack"]
            and _normalized_pack_measurement(item["pack"]) == pack_preference
            and (
                not category_preference
                or item["category"].lower() == category_preference
            )
        ]
        if not matches:
            category_text = f" {category_preference.title()}" if category_preference else ""
            return f"I couldn't find an active{category_text} product with a listed {pack_preference} pack size."
        options = ", ".join(
            f"{item['name']} ({item['price']})" if item["price"] else item["name"]
            for item in matches[:5]
        )
        category_text = f" {category_preference.title()}" if category_preference else ""
        return f"Current{category_text} products with a listed {pack_preference} pack size include {options}."
    if recommendation_context:
        normalized = re.sub(r"\s+", " ", lower).strip()
        if normalized == "budget":
            return "What is the maximum amount you want to spend?"
        if normalized in {"category", "meat category"}:
            return "Which catalog category do you prefer: Beef, Chicken, or Pork?"
        category_match = re.fullmatch(
            r"(beef|chicken|pork)(?:\s+(?:category|products?))?",
            normalized,
        )
        if category_match:
            category = category_match.group(1)
            matches = [
                item for item in catalog_entries
                if item["category"].lower() == category
            ]
            if not matches:
                return f"I couldn't find an active {category.title()} product in the current catalog."
            options = ", ".join(
                f"{item['name']} ({item['price']})" if item["price"] else item["name"]
                for item in matches[:5]
            )
            return f"Current {category.title()} options include {options}."
        if normalized in {"size", "pack size"}:
            return "What pack size are you looking for?"
    if _asks_generic_recommendation(message):
        return "What matters most for your recommendation: meat category, budget, or pack size?"
    if _asks_unconfirmed_product_detail(message):
        return (
            "Hindi kasama sa approved information ko ang detalyeng iyon. Sundin ang product label o mag-confirm sa Batangas Premium team."
            if filipino
            else "That detail is not included in my approved information. Please follow the product label or confirm with a Batangas Premium team member."
        )
    if not _conversation_is_related(
        message,
        safe_history,
        has_product_request=bool(requested_product),
    ) and not matched_entries:
        return UNRELATED_REPLY
    if catalog_entries and requested_product and not matched_entries:
        return _unknown_product_reply(requested_product, filipino)

    asks_price = any(
        term in lower
        for term in (
            "price",
            "prices",
            "how much",
            "magkano",
            "presyo",
            "cheap",
            "expensive",
            "compare",
            "cost",
        )
    )
    asks_pack = any(term in lower for term in ("pack size", "weight", "grams", "gram", "how many")) or bool(
        re.search(
            r"\bpack\b.{0,35}\b(?:is|size|weighs?|weight|\d+(?:\.\d+)?\s*(?:g|kg|grams?|kilograms?))\b",
            lower,
        )
    )
    follow_up = asks_price or asks_pack or any(
        term in lower for term in (" it", "it?", "that", "this", "available", "stock")
    )
    unresolved_context_product = None
    if not matched_entries and follow_up:
        for item in reversed(safe_history):
            if item["role"] != "user":
                continue
            previous_product = _product_request(item["content"], catalog_entries)
            matched_entries = (
                _matching_named_product_entries(previous_product, catalog_entries)
                if previous_product
                else []
            )
            if matched_entries:
                break
            if previous_product:
                unresolved_context_product = previous_product
                break
    if catalog_entries and unresolved_context_product and not matched_entries:
        return _unknown_product_reply(unresolved_context_product, filipino)

    if any(term in lower for term in ("phone", "email", "contact", "hotline")):
        return f"You can contact Batangas Premium at {BUSINESS_PHONE} or {BUSINESS_EMAIL}."

    if _asks_business_hours(message):
        if filipino:
            return "Bukas ang Batangas Premium Lunes hanggang Sabado, 7:00 AM hanggang 5:00 PM."
        return f"Batangas Premium is open {BUSINESS_HOURS}."

    if _asks_business_location(message) and not _asks_delivery(message):
        return f"Batangas Premium is located at {BUSINESS_ADDRESS}."

    if _asks_delivery(message) or any(term in lower for term in ("schedule", "fee")):
        if filipino:
            return "Nakadepende sa lokasyon ang delivery schedule at fee. Ibigay ang inyong lugar para ma-confirm ng Batangas Premium team ang detalye."
        return "Delivery schedules and fees depend on location. Share your area so a Batangas Premium team member can confirm the details."

    if any(term in lower for term in ("payment", "pay", "bayad", "gcash")):
        return (
            "Hindi nakalista ang confirmed payment methods dito. Iko-confirm ng Batangas Premium team ang tamang payment instructions bago kayo magbayad."
            if filipino
            else "Confirmed payment methods are not listed here. A Batangas Premium team member will confirm the correct instructions before you pay."
        )

    if any(term in lower for term in ("discount", "promo", "bulk", "wholesale")):
        return (
            "Nagbabago ang promos at bulk pricing. Maaaring mag-confirm ang Batangas Premium team ng kasalukuyang offer."
            if filipino
            else "Promotions and bulk pricing can change. A Batangas Premium team member can confirm any current offer."
        )

    message_words = set(re.findall(r"[a-z]+", lower))
    asks_stock = (
        "stock" in message_words
        or bool(re.match(r"^\s*are there any\b", lower))
        or bool(matched_entries) and bool(
            message_words & {"available", "availability", "may", "meron"}
        )
    )
    if asks_stock:
        subject = matched_entries[0]["name"] if matched_entries else "Product"
        if filipino:
            return f"Nagbabago ang availability ng {subject}. Maaaring mag-confirm ang Batangas Premium team ng kasalukuyang stock bago ka mag-order."
        return f"{subject} availability can change. A Batangas Premium team member can confirm current stock before you order."

    if asks_pack:
        if matched_entries and matched_entries[0]["pack"]:
            item = matched_entries[0]
            return f"{item['name']} has a listed pack size of {item['pack']}."
        return (
            "Wala akong kumpirmadong pack size para diyan. Maaaring mag-confirm ang Batangas Premium team."
            if filipino
            else "I do not have a confirmed pack size for that item. A Batangas Premium team member can confirm it."
        )

    matched_prices = []
    for item in matched_entries:
        if item["price"]:
            reply = f"{item['name']} is {item['price']}."
            if reply not in matched_prices:
                matched_prices.append(reply)

    if asks_price:
        if matched_prices:
            priced_matches = []
            for item in matched_entries:
                try:
                    amount = Decimal(item["price"].replace("PHP", "").replace(",", "").strip())
                except (InvalidOperation, AttributeError, ValueError):
                    continue
                priced_matches.append((amount, item))
            if len(priced_matches) >= 2 and "cheaper" in lower:
                amount, cheapest = min(priced_matches, key=lambda row: row[0])
                other_prices = " ".join(
                    f"{item['name']} is {item['price']}."
                    for other_amount, item in priced_matches
                    if item is not cheapest
                )
                return f"{cheapest['name']} is {_format_price(amount)} and is cheaper. {other_prices}".strip()
            if len(priced_matches) >= 2 and "more expensive" in lower:
                amount, priciest = max(priced_matches, key=lambda row: row[0])
                other_prices = " ".join(
                    f"{item['name']} is {item['price']}."
                    for other_amount, item in priced_matches
                    if item is not priciest
                )
                return f"{priciest['name']} is {_format_price(amount)} and is more expensive. {other_prices}".strip()
            answer = " ".join(matched_prices[:3])
            return answer.replace(" is PHP", " ay PHP") if filipino else answer
        if catalog_entries:
            priced_entries = []
            for item in catalog_entries:
                try:
                    amount = Decimal(item["price"].replace("PHP", "").replace(",", "").strip())
                except (InvalidOperation, AttributeError, ValueError):
                    continue
                priced_entries.append((amount, item))
            if priced_entries and any(term in lower for term in ("cheapest", "lowest price", "least expensive")):
                amount, item = min(priced_entries, key=lambda row: row[0])
                return f"The cheapest listed product is {item['name']} at {_format_price(amount)}."
            if priced_entries and any(term in lower for term in ("most expensive", "highest price")):
                amount, item = max(priced_entries, key=lambda row: row[0])
                return f"The highest-priced listed product is {item['name']} at {_format_price(amount)}."
            examples = ", ".join(
                f"{item['name']} ({item['price']})" for item in catalog_entries[:4] if item["price"]
            )
            if examples:
                if filipino:
                    return f"Kasama sa kasalukuyang presyo ang {examples}. Sabihin ang pangalan ng produkto para sa eksaktong presyo."
                return f"Current prices include {examples}. Ask me for a specific product if you need another price."
        return CONTACT_REPLY

    natural_pack_order = bool(
        re.search(r"\b(?:want|need|like)\b.*\b\d+\s+packs?\b", lower)
    )
    if natural_pack_order or _asks_ordering(message):
        if filipino:
            return "Sabihin ang produktong gusto ninyo at ang inyong lokasyon. Iko-confirm ng Batangas Premium team ang availability at tamang paraan ng pag-order."
        return "Tell me which product you want and your location. A Batangas Premium team member can confirm availability and the correct ordering channel."

    if any(term in lower for term in ("refund", "return", "complaint")):
        return f"A Batangas Premium team member should review that concern. Contact {BUSINESS_PHONE} or {BUSINESS_EMAIL}."

    if _asks_reseller_requirements(message):
        return (
            "Hindi nakalista sa approved information ko ang confirmed reseller requirements. Maaaring i-confirm ng Batangas Premium team ang requirements; maaari rin kitang tulungang magsimula ng reseller inquiry kapag handa ka na."
            if filipino
            else "Confirmed reseller requirements are not listed in my approved information. A Batangas Premium team member can confirm them, and I can help start a reseller inquiry when you are ready."
        )

    if matched_entries:
        return " ".join(_catalog_item_answer(item, filipino) for item in matched_entries[:2])
    if matched_prices:
        return " ".join(matched_prices[:3])

    if any(term in lower for term in ("product", "products", "catalog", "available", "availability", "produkto")):
        if catalog_entries:
            names = ", ".join(
                f"{item['name']} ({item['price']})" if item["price"] else item["name"]
                for item in catalog_entries[:5]
            )
            suffix = "" if len(catalog_entries) <= 5 else ", and more"
            if filipino:
                return f"Kasama sa kasalukuyang catalog ang {names}{suffix}. Sabihin ang pangalan ng produkto para sa detalye."
            return f"Our current catalog includes {names}{suffix}. Ask for a product name to see its details."
        return CONTACT_REPLY

    if is_reseller_intent(lower) or "inquiry" in lower:
        return (
            "Maaari kong kunin ang reseller details ninyo at ipasa ang inquiry sa available sales team leader. Hindi awtomatiko ang approval."
            if filipino
            else "I can collect your reseller details and send the inquiry to an available sales team leader. Approval is not automatic."
        )

    return (
        "Wala sa approved information ko ang detalyeng iyon. Maaaring mag-confirm ang Batangas Premium team."
        if filipino
        else CONTACT_REPLY
    )



__all__ = [
    "_first_sentence",
    "_explicit_product_request",
    "_format_price",
    "_format_quantity",
    "_clean_catalog_field",
    "_catalog_entries",
    "_matching_named_product_entries",
    "_catalog_product_tokens",
    "_standalone_product_request",
    "_product_request",
    "_unknown_product_reply",
    "_catalog_item_answer",
    "_catalog_prompt",
    "_model_reply_is_grounded",
    "fallback_reply",
]

