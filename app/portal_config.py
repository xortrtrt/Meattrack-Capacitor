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
        ("dashboard", "Overview", "layout-dashboard"),
        ("orders", "Orders & inquiries", "clipboard-check"),
        ("returns", "Returns & refunds", "package-check"),
        ("inventory", "Inventory", "boxes"),
        ("resellers", "Resellers", "users"),
        ("reports", "Sales reports", "bar-chart-3"),
        ("forecasts", "Demand planning", "line-chart"),
        ("accounts", "People & access", "shield-check"),
        ("settings", "Business settings", "settings"),
    ],
}


owner_nav_groups = (
    ("Business", ("dashboard", "orders", "returns", "resellers")),
    ("Stock & planning", ("inventory", "forecasts")),
    ("Management", ("reports", "accounts", "settings")),
)

# Detailed workspaces retain their URLs and authorization, without crowding the sidebar.
owner_workspace_tabs = (
    (("dashboard", "Business overview"), ("operations", "Work queues")),
    (("orders", "Reseller orders"), ("inquiries", "Reseller inquiries")),
    (("returns", "Product returns"), ("refunds", "Money refunds")),
    (("inventory", "Stock & production"), ("materials", "Raw materials"),
     ("batches", "Batches"), ("movements", "Movements"),
     ("expired-stock", "Pull-outs"), ("alerts", "Alerts"), ("products", "Products & prices")),
)


def owner_workspace_for(section: str) -> dict:
    for tabs in owner_workspace_tabs:
        if section in {slug for slug, _label in tabs} or (section == "product-details" and tabs[0][0] == "inventory"):
            return {"parent": tabs[0][0], "tabs": tabs}
    return {"parent": section, "tabs": ()}


# Workflow pages that are valid portal destinations without appearing in navigation.
portal_secondary_sections = {
    "owner": {
        "messages": ("Replacement conversations", "messages-square"),
        "operations": ("Work queues", "activity"),
        "inquiries": ("Reseller inquiries", "user-check"),
        "refunds": ("Money refunds", "badge-dollar-sign"),
        "materials": ("Raw materials", "beef"),
        "batches": ("Batches", "package-check"),
        "movements": ("Movements", "arrow-left-right"),
        "expired-stock": ("Pull-outs", "archive"),
        "alerts": ("Alerts", "triangle-alert"),
        "products": ("Products & prices", "tags"),
        "profile": ("My profile", "user-cog"),
        "product-details": ("Product details", "package-search"),
        "verify": ("Owner verification", "key-round"),
        "notifications": ("Notifications", "bell"),
    },
    "team-leader": {
        "product-details": ("Product details", "package-search"),
        "notifications": ("Notifications", "bell"),
    },
    "sales-supervisor": {
        "notifications": ("Notifications", "bell"),
    },
    "reseller": {
        "returns": ("My Returns", "package-check"),
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
        ("expired-stock", "Pull-outs", "archive"),
        ("logs", "Inventory Logs", "scroll-text"),
        ("returns", "Product Returns", "package-check"),
        ("profile", "Profile", "user-cog"),
    ],
    "sales": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("live-chat", "Live Chat", "messages-square"),
        ("messages", "Reseller Messages", "message-circle"),
        ("resellers", "My Resellers", "users"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("reports", "Reports", "file-text"),
        ("returns", "Returns & tracking", "package-check"),
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
        ("returns", "Return Review", "package-check"),
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


__all__ = ['default_section_for', 'owner_nav_groups', 'owner_workspace_for', 'portal_nav', 'portal_nav_for', 'portal_secondary_sections', 'roles', 'team_leader_nav_by_role', 'team_leader_role_labels']
