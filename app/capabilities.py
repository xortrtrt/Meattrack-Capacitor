from __future__ import annotations

from fastapi import HTTPException, Request


OWNER_CAPABILITIES = frozenset({"*"})

CAPABILITIES_BY_ROLE: dict[str, frozenset[str]] = {
    "owner": OWNER_CAPABILITIES,
    "sales-supervisor": frozenset({
        "accounts.manage_team",
        "conversations.manage",
        "exports.create",
        "inquiries.manage",
        "notifications.view",
        "operations.view_team",
        "orders.manage_assigned",
        "returns.view", "returns.review",
        "profile.manage_own",
        "refunds.manage_assigned",
        "reports.view_assigned",
        "resellers.manage",
    }),
    "team-leader:sales": frozenset({
        "refunds.view_assigned",
        "conversations.manage",
        "notifications.view",
        "operations.view_team",
        "orders.manage_assigned",
        "returns.view", "returns.review",
        "profile.manage_own",
        "reports.view_assigned",
        "resellers.view_assigned",
    }),
    "team-leader:inventory": frozenset({
        "inventory.manage",
        "returns.view", "returns.receive",
        "inventory.view",
        "notifications.view",
        "operations.view_team",
        "profile.manage_own",
        "reports.view_inventory",
    }),
    "reseller": frozenset({
        "orders.create",
        "returns.view", "returns.create",
        "orders.view_own",
        "notifications.view",
        "profile.manage_own",
        "reports.create_own",
    }),
}

PORTAL_SECTION_CAPABILITIES = {
    "sales-supervisor": {
        "returns": "returns.view",
        "dashboard": "operations.view_team", "live-chat": "conversations.manage",
        "messages": "conversations.manage", "inquiries": "inquiries.manage",
        "orders": "orders.manage_assigned", "refunds": "refunds.manage_assigned",
        "reports": "reports.view_assigned", "accounts": "accounts.manage_team",
        "profile": "profile.manage_own", "notifications": "notifications.view",
    },
    "team-leader:sales": {
        "refunds": "refunds.view_assigned",
        "returns": "returns.view",
        "dashboard": "operations.view_team", "live-chat": "conversations.manage",
        "messages": "conversations.manage", "resellers": "resellers.view_assigned",
        "orders": "orders.manage_assigned", "reports": "reports.view_assigned",
        "profile": "profile.manage_own", "notifications": "notifications.view",
    },
    "team-leader:inventory": {
        "returns": "returns.view",
        "dashboard": "operations.view_team", "inventory": "inventory.view",
        "expired-stock": "inventory.view", "product-details": "inventory.view",
        "raw-materials": "inventory.view", "finished-products": "inventory.view",
        "batches": "inventory.view", "logs": "reports.view_inventory",
        "profile": "profile.manage_own", "notifications": "notifications.view",
    },
    "reseller": {
        "returns": "returns.view",
        "dashboard": "orders.view_own", "order": "orders.create", "cart": "orders.create",
        "checkout": "orders.create", "history": "orders.view_own", "inbox": "orders.view_own",
        "profile": "profile.manage_own", "notifications": "notifications.view",
    },
}

OWNER_SECTION_CAPABILITIES = {
    "returns": "returns.view", "expired-stock": "inventory.view_all", "product-details": "inventory.view_all",
    "dashboard": "operations.view",
    "operations": "operations.view",
    "inquiries": "inquiries.view_all",
    "orders": "orders.view_all",
    "inventory": "inventory.view_all",
    "materials": "inventory.view_all",
    "batches": "inventory.view_all",
    "movements": "inventory.view_all",
    "alerts": "inventory.view_all",
    "products": "inventory.prices",
    "reports": "reports.view_all",
    "forecasts": "forecasts.manage",
    "refunds": "refunds.manage_all",
    "accounts": "accounts.manage_all",
    "resellers": "resellers.manage_all",
    "verify": "security.step_up",
    "settings": "settings.manage",
    "profile": "profile.manage_own",
    "notifications": "operations.view",
}


def capability_role_key(request: Request, role_key: str | None = None) -> str:
    role = role_key or str(request.session.get("role_key") or "")
    if role == "team-leader":
        leader_role = str(request.session.get("team_leader_role") or "sales")
        return f"team-leader:{leader_role}"
    return role


def has_capability(request: Request, capability: str, role_key: str | None = None) -> bool:
    capabilities = CAPABILITIES_BY_ROLE.get(capability_role_key(request, role_key), frozenset())
    return "*" in capabilities or capability in capabilities


def require_capability(request: Request, capability: str, role_key: str | None = None) -> None:
    if not has_capability(request, capability, role_key):
        raise HTTPException(status_code=403, detail="Your account cannot perform this action.")


def owner_section_capability(section: str) -> str:
    try:
        return OWNER_SECTION_CAPABILITIES[section]
    except KeyError as exc:
        raise HTTPException(status_code=404) from exc


def portal_section_capability(request: Request, role_key: str, section: str) -> str:
    if role_key == "owner":
        return owner_section_capability(section)
    role = capability_role_key(request, role_key)
    try:
        return PORTAL_SECTION_CAPABILITIES[role][section]
    except KeyError as exc:
        raise HTTPException(status_code=404) from exc


__all__ = [
    "CAPABILITIES_BY_ROLE",
    "OWNER_SECTION_CAPABILITIES",
    "PORTAL_SECTION_CAPABILITIES",
    "capability_role_key",
    "has_capability",
    "owner_section_capability",
    "portal_section_capability",
    "require_capability",
]
