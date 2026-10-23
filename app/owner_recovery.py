"""Bounded, one-use recovery of non-secret Owner form drafts after step-up."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import portal_config
from app.business_time import business_now

SESSION_KEY = "owner_action_draft"
TTL_SECONDS = 15 * 60
MAX_BYTES = 8192
ALLOWED_FIELDS = frozenset({
    "name", "email", "account_type", "team_leader_role", "team_leader_account_id",
    "destination_account_id", "active", "reason", "rejection_reason", "status", "action",
    "product_id", "item_id", "batch_id", "batch_code", "quantity", "quantity_delta",
    "expiry_date", "received_date", "production_date", "base_price", "model_name", "forecast_horizon_days",
    "order_payment_window_minutes", "near_expiry_days", "critical_expiry_days",
    "forecast_default_horizon_days", "owner_critical_alerts", "contact_email", "contact_phone",
    "contact_address", "business_hours", "return_section",
})
QUERY_KEYS = frozenset({"q", "status", "type", "sort", "page", "leader", "actions_page", "run_id", "refund", "pane"})
ACTION_PATTERN = re.compile(
    r"^/portal/(?:owner/(?:accounts(?:/[0-9]+/(?:role|status|activation|password-reset|sessions/revoke|transfer))?"
    r"|products|settings|forecasts(?:/runs/[0-9]+/(?:retry|cancel))?"
    r"|resellers/[0-9]+/team-leader|alerts/[0-9]+/status|orders/[0-9]+/(?:approve|reject|fulfill)"
    r"|inquiries/[0-9]+/(?:approve|reject)"
    r"|inventory/(?:corrections|production)|materials/[0-9]+/receipt"
    r"|refunds/[0-9]+/take-over))$"
)


def safe_destination(request, value: str, fallback: str = "/portal/owner/verify") -> str:
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
            return fallback
    sections = {item[0] for item in portal_config.portal_nav["owner"]}
    sections.update(portal_config.portal_secondary_sections["owner"])
    if parsed.path not in {f"/portal/owner/{section}" for section in sections}:
        return fallback
    query: list[tuple[str, str]] = []
    seen = set()
    for key, val in parse_qsl(parsed.query):
        if key not in QUERY_KEYS or key in seen:
            continue
        seen.add(key)
        candidate = query + [(key, val[:500])]
        if len(urlencode(candidate).encode("utf-8")) <= 2048:
            query = candidate
    return urlunsplit(("", "", parsed.path, urlencode(query), ""))


def capture(request, return_section: str, fields: dict | None = None) -> None:
    request.session.pop(SESSION_KEY, None)
    if not ACTION_PATTERN.fullmatch(request.url.path):
        return
    form = fields if fields is not None else getattr(request, "_form", None)
    form = form or {}
    values = {key: str(value) for key, value in form.items() if key in ALLOWED_FIELDS and isinstance(value, (str, int, bool))}
    if request.url.path == "/portal/owner/settings" and "owner_critical_alerts" not in form:
        values["owner_critical_alerts"] = ""
    fallback = safe_destination(request, f"/portal/owner/{return_section}")
    target = str(form.get("_owner_return_to") or request.headers.get("referer") or fallback)
    draft = {
        "account_id": request.session.get("account_id"), "created_at": int(business_now().timestamp()),
        "return_to": safe_destination(request, target), "action": request.url.path,
        "fields": values, "ready": False,
    }
    if len(json.dumps(draft).encode("utf-8")) > MAX_BYTES:
        draft["fields"] = {}
        draft["discarded"] = True
    if len(json.dumps(draft).encode("utf-8")) <= MAX_BYTES:
        request.session[SESSION_KEY] = draft


def _valid(request) -> dict | None:
    draft = request.session.get(SESSION_KEY)
    now = int(business_now().timestamp())
    if not isinstance(draft, dict) or draft.get("account_id") != request.session.get("account_id"):
        request.session.pop(SESSION_KEY, None)
        return None
    created = draft.get("created_at")
    if not isinstance(created, int) or not 0 <= now - created <= TTL_SECONDS:
        request.session.pop(SESSION_KEY, None)
        return None
    if not ACTION_PATTERN.fullmatch(str(draft.get("action", ""))):
        request.session.pop(SESSION_KEY, None)
        return None
    return draft


def complete(request) -> str:
    draft = _valid(request)
    if not draft:
        return "/portal/owner/verify"
    destination = safe_destination(request, draft["return_to"])
    if destination == "/portal/owner/verify" and draft["return_to"] != destination:
        request.session.pop(SESSION_KEY, None)
    else:
        draft["ready"] = True
        request.session[SESSION_KEY] = draft
    return destination


def consume(request) -> dict | None:
    draft = _valid(request)
    if not draft or not draft.get("ready"):
        return None
    if urlsplit(safe_destination(request, draft["return_to"])).path != request.url.path:
        return None
    request.session.pop(SESSION_KEY, None)
    return {"action": draft["action"], "fields": draft["fields"], "discarded": draft.get("discarded", False)}
