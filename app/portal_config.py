from __future__ import annotations


roles = {
    "owner": {
        "label": "Owner",
        "account_type": "owner",
        "name": "Patric Mapa",
        "email": "patric.mapa@gmail.com",
        "default_section": "dashboard",
    },
    "team-leader": {
        "label": "Team Leader",
        "account_type": "team_leader",
        "name": "Team Leader",
        "email": "team.leader@batangaspremium.test",
        "default_section": "dashboard",
    },
    "sales-supervisor": {
        "label": "Sales Supervisor",
        "account_type": "team_leader",
        "name": "Sales Supervisor",
        "email": "sales.supervisor@batangaspremium.test",
        "default_section": "dashboard",
    },
    "reseller": {
        "label": "Reseller",
        "account_type": "reseller",
        "name": "Lipa Fresh Mart",
        "email": "reseller@lipafresh.test",
        "default_section": "dashboard",
    },
}


team_leader_role_labels = {
    "inventory": "Inventory Team Leader",
    "sales": "Sales Team Leader",
    "sales-supervisor": "Sales Supervisor",
}


portal_nav = {
    "reseller": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("order", "Products", "shopping-basket"),
        ("cart", "Cart", "shopping-cart"),
        ("history", "Order History", "clipboard-list"),
        ("inbox", "Inbox", "inbox"),
        ("profile", "Profile", "user-cog"),
    ],
    "team-leader": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("sales", "Walk-in Sales", "shopping-cart"),
        ("inventory", "Inventory", "boxes"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("reports", "Reports", "file-text"),
    ],
    "owner": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("operations", "Operations", "activity"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Orders", "clipboard-check"),
        ("inventory", "Inventory", "boxes"),
        ("materials", "Materials", "beef"),
        ("batches", "Batches", "package-check"),
        ("movements", "Movements", "arrow-left-right"),
        ("alerts", "Alerts", "triangle-alert"),
        ("products", "Products & Prices", "tags"),
        ("reports", "Reports", "bar-chart-3"),
        ("forecasts", "Forecasts", "line-chart"),
        ("refunds", "Refunds", "badge-dollar-sign"),
        ("accounts", "Users & Access", "shield-check"),
        ("resellers", "Resellers", "users"),
        ("settings", "Settings", "settings"),
        ("profile", "Profile", "user-cog"),
    ],
}


owner_nav_groups = (
    ("Overview", ("dashboard", "operations")),
    ("Sales operations", ("inquiries", "orders", "refunds")),
    ("Inventory", ("inventory", "materials", "batches", "movements", "alerts", "products")),
    ("People", ("accounts", "resellers")),
    ("Intelligence", ("reports", "forecasts")),
    ("Preferences", ("settings",)),
    ("Account", ("profile",)),
)


# Workflow pages that are valid portal destinations without appearing in navigation.
portal_secondary_sections = {
    "owner": {
        "verify": ("Owner verification", "key-round"),
        "notifications": ("Notifications", "bell"),
    },
    "team-leader": {
        "notifications": ("Notifications", "bell"),
    },
    "sales-supervisor": {
        "notifications": ("Notifications", "bell"),
    },
    "reseller": {
        "checkout": ("Checkout", "credit-card"),
        "notifications": ("Notifications", "bell"),
    },
}


team_leader_nav_by_role = {
    "inventory": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("inventory", "Inventory", "boxes"),
        ("raw-materials", "Raw Materials", "beef"),
        ("finished-products", "Finished Products", "package-search"),
        ("batches", "Product Batches", "package-check"),
        ("logs", "Inventory Logs", "scroll-text"),
        ("profile", "Profile", "user-cog"),
    ],
    "sales": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("live-chat", "Live Chat", "messages-square"),
        ("messages", "Reseller Messages", "message-circle"),
        ("resellers", "My Resellers", "users"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("reports", "Reports", "file-text"),
        ("profile", "Profile", "user-cog"),
    ],
    "sales-supervisor": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("live-chat", "Live Chat", "messages-square"),
        ("messages", "Reseller Messages", "message-circle"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("refunds", "Refunds", "badge-dollar-sign"),
        ("reports", "Reports", "file-text"),
        ("accounts", "Accounts", "shield-check"),
        ("profile", "Profile", "user-cog"),
    ],
}


def portal_nav_for(role_key: str, team_leader_role: str | None = None) -> list[tuple[str, str, str]]:
    if role_key == "sales-supervisor":
        return team_leader_nav_by_role["sales-supervisor"]
    if role_key == "team-leader":
        return team_leader_nav_by_role.get(team_leader_role or "sales", team_leader_nav_by_role["sales"])
    return portal_nav[role_key]


def default_section_for(role_key: str, team_leader_role: str | None = None) -> str:
    nav = portal_nav_for(role_key, team_leader_role)
    return nav[0][0] if nav else roles[role_key]["default_section"]


__all__ = ['default_section_for', 'owner_nav_groups', 'portal_nav', 'portal_nav_for', 'portal_secondary_sections', 'roles', 'team_leader_nav_by_role', 'team_leader_role_labels']
