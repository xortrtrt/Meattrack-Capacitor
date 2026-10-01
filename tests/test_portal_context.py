from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web
from app.portals import owner as owner_portal
from app.portals import shared as shared_portal


METRICS = {
    "fulfilled_sales": 0.0,
    "pending_reseller_orders": 0,
    "open_alerts": 0,
    "active_resellers": 0,
    "total_available": 0.0,
}

READ_FUNCTIONS = (
    "current_metrics",
    "list_products",
    "list_alerts",
    "list_sales_reports",
    "list_forecasts",
    "list_accounts",
    "list_activity_logs",
    "list_inventory_movements",
    "list_inquiries",
    "list_orders",
    "list_inventory_items",
    "list_inventory_batches",
    "list_raw_materials",
    "list_product_recipes",
    "inventory_product_movement_analytics",
    "count_inventory_items",
    "count_inventory_batches",
    "count_products",
    "count_inquiries",
    "count_orders",
    "count_sales_reports",
    "count_forecasts",
    "count_accounts",
    "count_activity_logs",
    "count_inventory_movements",
    "team_sales_report_entries",
    "team_rejected_order_entries",
    "team_reseller_purchase_summary",
    "reseller_most_bought_products",
    "reseller_sales_series",
    "reseller_reportable_products",
    "reseller_account_profile",
    "account_portal_profile",
    "list_team_leader_accounts",
    "list_reseller_assignments",
    "latest_forecast_run",
    "get_forecast_run",
    "list_forecast_runs",
    "list_notifications",
    "unread_notification_count",
)

EXPECTED_CALLS = {
    ("owner", "dashboard"): {
        "current_metrics",
        "list_products",
        "list_forecasts",
        "reseller_sales_series",
        "reseller_most_bought_products",
        "list_notifications",
        "unread_notification_count",
    },
    ("owner", "products"): {"list_products", "count_products", "list_notifications", "unread_notification_count"},
    ("owner", "reports"): {"list_sales_reports", "count_sales_reports", "list_notifications", "unread_notification_count"},
    ("owner", "forecasts"): {"list_forecasts", "count_forecasts", "latest_forecast_run", "list_forecast_runs", "list_notifications", "unread_notification_count"},
    ("owner", "accounts"): {
        "list_accounts",
        "count_accounts",
        "list_team_leader_accounts",
        "list_reseller_assignments",
        "list_notifications",
        "unread_notification_count",
    },
    ("team-leader", "dashboard"): {"current_metrics", "list_inquiries", "list_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "inquiries"): {"list_inquiries", "count_inquiries", "list_notifications", "unread_notification_count"},
    ("team-leader", "orders"): {"list_orders", "count_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "reports"): {
        "list_sales_reports",
        "count_sales_reports",
        "team_sales_report_entries",
        "team_rejected_order_entries",
        "team_reseller_purchase_summary",
        "list_notifications",
        "unread_notification_count",
    },
    ("team-leader", "profile"): {"list_notifications", "unread_notification_count"},
    ("reseller", "dashboard"): {
        "current_metrics",
        "reseller_most_bought_products",
        "reseller_sales_series",
        "list_notifications",
        "unread_notification_count",
    },
    ("reseller", "order"): {"list_products", "count_products", "list_notifications", "unread_notification_count"},
    ("reseller", "cart"): {"list_products", "list_notifications", "unread_notification_count"},
    ("reseller", "history"): {"list_orders", "count_orders", "list_notifications", "unread_notification_count"},
    ("reseller", "profile"): {"list_notifications", "unread_notification_count"},
}

INVENTORY_EXPECTED_CALLS = {
    ("team-leader", "dashboard"): {"current_metrics", "list_products", "inventory_product_movement_analytics", "list_notifications", "unread_notification_count"},
    ("team-leader", "inventory"): {
        "list_products",
        "count_products",
        "list_raw_materials",
        "list_product_recipes",
        "list_notifications",
        "unread_notification_count",
    },
    ("team-leader", "raw-materials"): {"list_inventory_items", "count_inventory_items", "list_notifications", "unread_notification_count"},
    ("team-leader", "finished-products"): {"list_inventory_items", "count_inventory_items", "list_notifications", "unread_notification_count"},
    ("team-leader", "batches"): {"list_inventory_batches", "count_inventory_batches", "list_notifications", "unread_notification_count"},
    ("team-leader", "logs"): {"list_inventory_movements", "count_inventory_movements", "list_notifications", "unread_notification_count"},
    ("team-leader", "profile"): {"list_notifications", "unread_notification_count"},
}


def install_read_spies(monkeypatch):
    calls = defaultdict(list)
    for name in READ_FUNCTIONS:
        result = METRICS if name == "current_metrics" else 0 if name.startswith(("count_", "unread_")) else []

        def fake(*args, _name=name, _result=result, **kwargs):
            calls[_name].append((args, kwargs))
            return _result

        monkeypatch.setattr(repositories, name, fake)
    return calls


@pytest.mark.parametrize("role_key,section", EXPECTED_CALLS)
def test_each_portal_section_loads_only_its_required_data(monkeypatch, role_key, section):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)

    response = client.get(f"/portal/{role_key}/{section}")

    assert response.status_code == 200
    actual_calls = {name for name, entries in calls.items() if entries}
    assert actual_calls == EXPECTED_CALLS[(role_key, section)]


@pytest.mark.parametrize("role_key,section", INVENTORY_EXPECTED_CALLS)
def test_inventory_team_leader_sections_load_only_inventory_data(monkeypatch, role_key, section):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")

    response = client.get(f"/portal/{role_key}/{section}")

    assert response.status_code == 200
    actual_calls = {name for name, entries in calls.items() if entries}
    assert actual_calls == INVENTORY_EXPECTED_CALLS[(role_key, section)]
    if section == "dashboard":
        assert calls["inventory_product_movement_analytics"] == [((), {"days": 30, "limit": 8})]
    if section == "logs":
        assert calls["list_inventory_movements"] == [((), {"q": "", "page": 1, "page_size": 10, "sort": ""})]
        assert calls["count_inventory_movements"] == [((), {"q": ""})]

    if section == "inventory":
        assert calls["list_products"] == [
            ((), {"q": "", "category": "", "page": 1, "page_size": 8, "sort": "", "active_only": False}),
            ((), {}),
        ]
        assert calls["count_products"] == [((), {"q": "", "category": "", "active_only": False})]
        assert calls["list_product_recipes"] == [((), {"product_ids": []})]

    if section == "raw-materials":
        assert calls["list_inventory_items"] == [((), {"q": "", "category": "raw_material", "page": 1, "page_size": 10, "sort": ""})]
        assert calls["count_inventory_items"] == [((), {"q": "", "category": "raw_material"})]

    if section == "finished-products":
        assert calls["list_inventory_items"] == [((), {"q": "", "category": "finished_product", "page": 1, "page_size": 8, "sort": ""})]
        assert calls["count_inventory_items"] == [((), {"q": "", "category": "finished_product"})]

    if section == "batches":
        assert calls["list_inventory_batches"] == [((), {"q": "", "category": "", "page": 1, "page_size": 10, "sort": ""})]
        assert calls["count_inventory_batches"] == [((), {"q": "", "category": ""})]


def test_section_filters_are_applied(monkeypatch):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)

    assert client.get("/portal/team-leader/dashboard").status_code == 200
    assert calls["current_metrics"] == [((), {"team_leader_account_id": None})]
    assert calls["list_inquiries"] == [((), {"limit": 4, "assigned_team_leader_account_id": None})]
    assert calls["list_orders"] == [((), {"order_type": "reseller", "limit": 5, "team_leader_account_id": None})]

    calls = install_read_spies(monkeypatch)
    assert client.get("/portal/reseller/dashboard").status_code == 200
    assert calls["current_metrics"] == [((), {"reseller_account_id": None})]
    assert calls["reseller_most_bought_products"] == [((), {"limit": 3, "account_id": None})]
    assert len(calls["reseller_sales_series"]) == 1
    assert calls["reseller_sales_series"][0][0][2] == "fulfilled"
    assert calls["reseller_sales_series"][0][1] == {"account_id": None}

    calls = install_read_spies(monkeypatch)
    assert client.get("/portal/reseller/order?q=tocino&type=Pork&page=2").status_code == 200
    assert calls["list_products"] == [((), {"q": "tocino", "category": "Pork", "page": 2, "page_size": 8, "sort": "", "active_only": True})]
    assert calls["count_products"] == [((), {"q": "tocino", "category": "Pork", "active_only": True})]

    calls = install_read_spies(monkeypatch)
    assert client.get("/portal/reseller/history?q=order&status=pending&page=2").status_code == 200
    assert calls["list_orders"] == [((), {"order_type": "reseller", "q": "order", "status": "pending", "team_leader_account_id": None, "reseller_account_id": None, "page": 2, "page_size": 10, "sort": ""})]
    assert calls["count_orders"] == [((), {"order_type": "reseller", "q": "order", "status": "pending", "team_leader_account_id": None, "reseller_account_id": None})]


def test_landing_page_does_not_load_metrics(monkeypatch):
    monkeypatch.setattr(repositories, "list_products", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repositories,
        "current_metrics",
        lambda: pytest.fail("landing page must not load dashboard metrics"),
    )

    response = TestClient(main.app).get("/")

    assert response.status_code == 200


def test_landing_uses_six_featured_products_and_reseller_first_layout(monkeypatch):
    calls = []
    products = [
        {
            "name": f"Featured Product {index}",
            "description": "Internal note: set final price in Owner portal.",
            "category": "Pork",
            "base_price": 60 + index,
        }
        for index in range(1, 9)
    ]

    def list_products(*args, **kwargs):
        calls.append((args, kwargs))
        return products[: kwargs.get("page_size", len(products))]

    monkeypatch.setattr(repositories, "list_products", list_products)
    response = TestClient(main.app).get("/")
    css = "\n".join(
        open(path, encoding="utf-8").read()
        for path in (
            "app/static/css/public/base.css",
            "app/static/css/public/pages.css",
            "app/static/css/public/landing.css",
            "app/static/css/public/visitor-chat.css",
        )
    )

    assert response.status_code == 200
    assert calls == [((), {"active_only": True, "page": 1, "page_size": 6})]
    assert response.text.count('class="landing-product"') == 6
    assert "Featured Product 6" in response.text
    assert "Featured Product 7" not in response.text
    assert "set final price in Owner portal" not in response.text
    assert 'href="/products"' not in response.text
    assert response.text.count("data-landing-product-open") == 12
    assert "data-landing-product-modal" in response.text
    assert "data-landing-product-modal-image" in response.text
    assert "Ask about this product" in response.text
    assert 'id="store" class="landing-products"' in response.text
    assert 'id="partnerships"' in response.text
    assert 'id="about"' in response.text
    assert 'id="inquiry"' in response.text
    assert "Premium flavor, ready for your next sale." in response.text
    assert response.text.count("data-open-chatbot") >= 3
    assert "mobile-drawer-brand" in response.text
    assert "batangas_premium.png" in response.text
    assert ".landing-page .landing-nav" in css
    assert ".landing-product-grid" in css
    assert ".landing-product-modal-panel" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".chatbot-widget.is-open .chatbot-toggle" in css
    app_js = "\n".join(
        open(path, encoding="utf-8").read()
        for path in (
            "app/static/js/public-site.js",
            "app/static/js/public-navigation.js",
            "app/static/js/landing-experience.js",
            "app/static/js/visitor-chat.js",
        )
    )
    forms_js = open("app/static/js/forms.js", encoding="utf-8").read()
    assert "function bindLandingExperience()" in app_js
    assert "function bindLandingProductModal()" in app_js
    assert 'button.dataset.chatbotPrompt || "I want to be a reseller"' in app_js
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)")' in app_js
    assert 'widget.classList.toggle("is-open", open);' in app_js
    assert 'document.addEventListener("pointerdown", (event) =>' in app_js
    assert "nav.contains(event.target) || toggle.contains(event.target)" in app_js
    assert "meattrack_chatbot_messages_v1" in app_js
    assert "24 * 60 * 60 * 1000" in app_js
    assert "async function typeBotReply(bubble, reply)" in app_js
    assert 'suggestions.querySelectorAll("button")' in app_js
    assert "activeSuggestions = nextSuggestions" in app_js
    assert "suggestions.replaceChildren(fragment)" in app_js
    assert "suggestions: activeSuggestions" in app_js
    assert "Array.isArray(stored.suggestions)" in app_js
    assert "await typeBotReply(resetMessage, newChatWelcome)" in app_js
    assert "Your current conversation is still active; please try again." in app_js
    assert app_js.index('if (!response.ok)') < app_js.index('messages.innerHTML = "";', app_js.index('resetButton.addEventListener'))
    assert 'bubble.classList.add("is-typing")' in app_js
    assert "@keyframes chatbot-caret" in css
    assert "20261002-chat-interactions-1" in response.text
    assert "BP ChatBot" in response.text
    assert "chatbot-avatar" not in response.text
    assert 'role="dialog" aria-label="Batangas Premium support"' in response.text
    assert 'data-lucide="message-circle"' in response.text
    assert 'data-chatbot-send-label' in response.text
    assert "Premium support concierge" in css
    assert "padding: 0 !important" in css
    assert "function bindOtpModal()" in forms_js
    assert "data-otp-modal-input" in forms_js


def test_products_page_renders_complete_active_catalog(monkeypatch):
    calls = []
    products = [
        {
            "name": "Tocino Ala Eh",
            "description": "Internal note: set final price in Owner portal.",
            "category": "Pork",
            "base_price": 70,
        },
        {
            "name": "Beef Longganisa",
            "description": "Internal note: set final price in Owner portal.",
            "category": "Beef",
            "base_price": 75,
        },
    ]

    def list_products(*args, **kwargs):
        calls.append((args, kwargs))
        return products

    monkeypatch.setattr(repositories, "list_products", list_products)
    response = TestClient(main.app).get("/products")

    assert response.status_code == 200
    assert calls == [((), {"active_only": True})]
    assert '<body class="catalog-page">' in response.text
    assert "Tocino Ala Eh" in response.text
    assert "Beef Longganisa" in response.text
    assert "set final price in Owner portal" not in response.text


def test_landing_empty_catalog_shows_chat_fallback(monkeypatch):
    monkeypatch.setattr(repositories, "list_products", lambda *args, **kwargs: [])

    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert "Catalog update in progress" in response.text
    assert "Ask us what is available today." in response.text
    assert "data-open-chatbot" in response.text


def test_login_page_removes_social_buttons_and_uses_glass_styles():
    response = TestClient(main.app).get("/login")
    css = open("app/static/css/login.css", encoding="utf-8").read()

    assert response.status_code == 200
    assert "Google / Gmail" not in response.text
    assert "Facebook" not in response.text
    assert "or continue with" not in response.text
    assert "backdrop-filter: blur(22px)" in css
    assert "border-radius: 24px" in css


def test_reseller_nav_includes_profile():
    slugs = [slug for slug, *_ in repositories.portal_nav_for("reseller")]

    assert "profile" in slugs
    assert "reports" not in slugs


def test_team_leader_nav_includes_profile_for_sales_and_inventory():
    assert "profile" in [slug for slug, *_ in repositories.portal_nav_for("team-leader", "sales")]
    assert "profile" in [slug for slug, *_ in repositories.portal_nav_for("team-leader", "inventory")]


def test_password_otp_confirmation_renders_as_modal(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 9)
    monkeypatch.setattr(repositories, "account_portal_profile", lambda account_id: {
        "account_id": 9,
        "name": "Sales Leader",
        "email": "sales@example.test",
        "account_type": "team_leader",
        "role_key": "team-leader",
        "team_leader_role": "sales",
    })
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = client.get("/portal/team-leader/profile?otp=1")

    assert response.status_code == 200
    assert "data-otp-modal" in response.text
    assert "Confirm password change" in response.text
    assert "action=\"/portal/team-leader/profile/password/confirm\"" in response.text
    assert "otp-panel-active" not in response.text


def test_reseller_reports_section_is_removed(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)

    response = client.get("/portal/reseller/reports")

    assert response.status_code == 404


def test_owner_logs_section_is_removed(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)

    slugs = [slug for slug, *_ in repositories.portal_nav_for("owner")]
    response = client.get("/portal/owner/logs")

    assert "logs" not in slugs
    assert response.status_code == 404


def test_owner_products_render_product_cards_and_price_form(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_products", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
        "list_products",
        lambda *args, **kwargs: [
            {
                "product_id": 1,
                "name": "Tocino Ala Eh",
                "description": "Sweet pork tocino.",
                "category": "Pork",
                "available": 100,
                "unit": "pack",
                "base_price": 70,
                "recipe_count": 2,
                "is_active": True,
            }
        ],
    )

    response = client.get("/portal/owner/products")

    assert response.status_code == 200
    assert "owner-product-card" in response.text
    assert "owner-product-image" in response.text
    assert 'name="base_price"' in response.text
    assert "Sweet pork tocino." not in response.text
    assert "pack available" not in response.text
    assert "recipe items" not in response.text
    owner_css = open("app/static/css/portals/owner.css", encoding="utf-8").read()
    assert "object-fit: cover" not in owner_css
    assert "object-fit: contain" in owner_css
    assert "repeat(2, minmax(0, 1fr))" in owner_css


def test_owner_product_price_update_route_still_posts(monkeypatch):
    calls = []
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "product_by_id", lambda product_id: {"product_id": product_id})
    monkeypatch.setattr(repositories, "update_product_price", lambda *args, **kwargs: calls.append((args, kwargs)))

    response = client.post(
        "/portal/owner/products",
        data={"product_id": 1, "base_price": 129},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [((1, Decimal("129.00")), {"actor_account_id": None})]


def test_owner_accounts_render_responsive_cards(monkeypatch):
    client = TestClient(main.app)
    list_calls = []
    count_calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    def fake_count_accounts(*args, **kwargs):
        count_calls.append(kwargs)
        return 2

    monkeypatch.setattr(repositories, "count_accounts", fake_count_accounts)

    def fake_list_accounts(*args, **kwargs):
        list_calls.append(kwargs)
        return [
            {"account_id": 1, "name": "Owner", "email": "owner@example.test", "account_type": "owner", "status": "active"},
            {
                "account_id": 4,
                "name": "Sales Leader",
                "email": "sales@example.test",
                "account_type": "team_leader",
                "team_leader_role": "sales",
                "status": "active",
            },
        ]

    monkeypatch.setattr(
        repositories,
        "list_accounts",
        fake_list_accounts,
    )
    monkeypatch.setattr(repositories, "list_team_leader_accounts", lambda *args, **kwargs: [{"account_id": 4, "name": "Sales Leader B"}])
    monkeypatch.setattr(
        repositories,
        "list_reseller_assignments",
        lambda *args, **kwargs: [
            {
                "reseller_id": 2,
                "business_name": "Xort's store",
                "reseller_status": "active",
                "contact_person": "Xortoise",
                "email": "xxortoise@gmail.com",
                "team_leader_account_id": 4,
                "team_leader_name": "Sales Leader B",
            }
        ],
    )

    response = client.get("/portal/owner/accounts")

    assert response.status_code == 200
    assert "owner-account-card" in response.text
    assert "owner-assignment-card" not in response.text
    assert "/portal/owner/resellers/2/team-leader" not in response.text
    assert '<option value="reseller"' not in response.text
    assert "/portal/owner/accounts/4/role" in response.text
    assert '<option value="sales" selected>Sales leader</option>' in response.text
    assert list_calls[0]["account_types"] == ("owner", "team_leader")
    assert count_calls[0]["account_types"] == ("owner", "team_leader")


def test_owner_can_update_only_team_leader_account_roles(monkeypatch):
    client = TestClient(main.app)
    updates = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(
        repositories,
        "update_team_leader_account_role",
        lambda *args, **kwargs: updates.append((args, kwargs)) or {"account_id": args[0]},
    )

    response = client.post(
        "/portal/owner/accounts/4/role",
        data={"team_leader_role": "sales-supervisor"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert updates == [((4, "sales-supervisor"), {"actor_account_id": None})]


def test_owner_activation_route_excludes_reseller_accounts(monkeypatch):
    client = TestClient(main.app)
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(
        repositories,
        "resend_activation",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = client.post("/portal/owner/accounts/8/activation", follow_redirects=False)

    assert response.status_code == 303
    assert calls == [((8, 0), {"account_types": ("owner", "team_leader")})]


def test_owner_reports_render_mobile_cards(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_sales_reports", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
        "list_sales_reports",
        lambda *args, **kwargs: [
            {
                "sales_report_id": 1,
                "report_source": "team_leader",
                "submitted_by": "Sales Leader B",
                "period_start": date.today(),
                "period_end": date.today(),
                "total_sales": 700,
                "total_orders": 1,
                "notes": "Monthly report",
                "items": [],
            }
        ],
    )

    response = client.get("/portal/owner/reports?q=sales&page=1")

    assert response.status_code == 200
    assert "owner-report-card" in response.text
    assert "owner-report-table" in response.text


def test_owner_forecasts_render_prophet_ui(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_forecasts", lambda *args, **kwargs: 1)
    monkeypatch.setattr(repositories, "list_forecast_runs", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repositories,
        "latest_forecast_run",
        lambda *args, **kwargs: {
            "model_name": "Prophet demand forecast",
            "input_period_start": date.today(),
            "input_period_end": date.today(),
            "forecast_horizon_days": 7,
            "status": "completed",
            "notes": "Completed with: Prophet, Baseline fallback.",
        },
    )
    monkeypatch.setattr(
        repositories,
        "list_forecasts",
        lambda *args, **kwargs: [
            {
                "product": "Tocino Ala Eh",
                "forecast_date": date.today(),
                "forecast_start": date.today(),
                "predicted_quantity": 12,
                "forecast_total": 12,
                "usable_stock": 5,
                "production_gap": 7,
                "suggested_production": 7,
                "confidence": "10 - 14 packs",
                "model_name": "Prophet demand forecast",
                "method_label": "Prophet",
                "diagnostic_status": "ok",
                "diagnostic_message": "Selected by backtest.",
                "daily": [],
                "notes": "Completed with: Prophet.",
            }
        ],
    )

    response = client.get("/portal/owner/forecasts")

    assert response.status_code == 200
    assert "Models are backtested against continuous daily sales-leader demand history" in response.text
    assert "owner-forecast-result-card" in response.text
    assert "owner-horizon-picker" in response.text
    assert 'value="30"' in response.text
    assert 'data-forecast-horizon-value' in response.text
    assert "10 - 14 packs" in response.text


def test_owner_forecast_post_queues_run_for_signed_in_owner(monkeypatch):
    client = TestClient(main.app)
    captured = {}
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "queue_forecast",
        lambda name, horizon, account_id: captured.update(
            name=name, horizon=horizon, account_id=account_id
        ) or {"forecast_run_id": 9, "status": "queued", "existing": False},
    )

    response = client.post(
        "/portal/owner/forecasts",
        data={"model_name": "Demand forecast", "forecast_horizon_days": "30"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert captured == {"name": "Demand forecast", "horizon": 30, "account_id": 42}
    assert "run_id=9" in response.headers["location"]


def test_owner_forecast_status_is_account_scoped(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "forecast_run_status",
        lambda run_id, account_id: {
            "forecast_run_id": run_id,
            "status": "running",
            "processed_products": 2,
            "total_products": 5,
            "completed_at": None,
            "message": "Forecast is running.",
        } if account_id == 42 else None,
    )

    response = client.get("/portal/owner/forecasts/runs/9/status")

    assert response.status_code == 200
    assert response.json()["processed_products"] == 2
    assert response.json()["status"] == "running"


def test_owner_dashboard_renders_executive_sections(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        repositories,
        "current_metrics",
        lambda *args, **kwargs: {
            "fulfilled_sales": 1500,
            "pending_reseller_orders": 2,
            "open_alerts": 0,
            "active_resellers": 3,
            "total_available": 180,
        },
    )
    monkeypatch.setattr(
        repositories,
        "list_products",
        lambda *args, **kwargs: [
            {"product_id": 1, "name": "Tocino Ala Eh", "available": 90, "unit": "pack"}
        ],
    )
    monkeypatch.setattr(
        repositories,
        "list_forecasts",
        lambda *args, **kwargs: [
            {
                "forecast_result_id": 1,
                "product": "Tocino Ala Eh",
                    "forecast_date": date.today(),
                    "predicted_quantity": 42,
                    "forecast_total": 42,
                    "production_gap": None,
                    "confidence": "85% - 95% range",
            }
        ],
    )
    monkeypatch.setattr(
        repositories,
        "reseller_sales_series",
        lambda *args, **kwargs: [
            {"sale_date": date.today(), "total_sales": 700, "order_count": 1}
        ],
    )
    monkeypatch.setattr(
        repositories,
        "reseller_most_bought_products",
        lambda *args, **kwargs: [
            {"name": "Tocino Ala Eh", "total_quantity": 10, "unit": "pack", "total_amount": 700}
        ],
    )

    response = client.get("/portal/owner/dashboard")

    assert response.status_code == 200
    assert "Forecast priority" in response.text
    assert "Sales trend" in response.text
    assert "Most bought products" in response.text
    assert "Stock pulse" in response.text
    assert "data-owner-sales-chart" in response.text
    assert 'name="sales_period"' in response.text
    assert "data-owner-sales-form" in response.text
    assert "this.form.submit()" not in response.text
    assert "Quarterly" in response.text
    assert "Yearly" in response.text
    assert "Open alerts" not in response.text


def test_template_numeric_filters_render_missing_values_safely():
    assert web.currency(None) == "—"
    assert web.number(None) == "—"


def test_owner_sales_chart_endpoint_returns_period_json(monkeypatch):
    client = TestClient(main.app)
    today = date.today()
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(
        repositories,
        "reseller_sales_series",
        lambda *args, **kwargs: [
            {"sale_date": today, "total_sales": 700, "order_count": 1}
        ],
    )

    response = client.get("/portal/owner/dashboard/sales-chart?sales_period=monthly")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "monthly"
    assert len(payload["rows"]) == 12
    assert payload["rows"][-1]["sales"] == 700
    assert payload["rows"][-1]["orders"] == 1


def test_owner_sales_period_points_support_dashboard_granularities():
    today = date(2026, 7, 21)

    assert len(owner_portal.owner_sales_period_points("daily", today)[1]) == 31
    assert len(owner_portal.owner_sales_period_points("weekly", today)[1]) == 12
    assert len(owner_portal.owner_sales_period_points("monthly", today)[1]) == 12
    assert len(owner_portal.owner_sales_period_points("quarterly", today)[1]) == 8
    assert len(owner_portal.owner_sales_period_points("yearly", today)[1]) == 5


def test_owner_dashboard_sales_chart_uses_continuous_line_series(monkeypatch):
    today = date.today()
    monkeypatch.setattr(
        repositories,
        "current_metrics",
        lambda *args, **kwargs: {
            "fulfilled_sales": 700,
            "pending_reseller_orders": 0,
            "open_alerts": 0,
            "active_resellers": 1,
            "total_available": 100,
        },
    )
    monkeypatch.setattr(repositories, "list_products", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "list_forecasts", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "reseller_most_bought_products", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repositories,
        "reseller_sales_series",
        lambda *args, **kwargs: [
            {"sale_date": today, "total_sales": 700, "order_count": 1}
        ],
    )

    context = owner_portal.dashboard_context(None)

    assert len(context["owner_sales_chart"]) == 31
    assert context["owner_sales_chart"][-1]["sales"] == 700
    assert context["owner_sales_chart"][0]["sales"] == 0


def test_inventory_dashboard_renders_product_movement_chart(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        repositories,
        "current_metrics",
        lambda *args, **kwargs: {
            "fulfilled_sales": 0,
            "pending_reseller_orders": 0,
            "open_alerts": 0,
            "active_resellers": 0,
            "total_available": 100,
        },
    )
    monkeypatch.setattr(repositories, "list_products", lambda *args, **kwargs: [{"product_id": 1, "name": "Tocino"}])
    monkeypatch.setattr(
        repositories,
        "inventory_product_movement_analytics",
        lambda *args, **kwargs: [
            {
                "product_id": 1,
                "name": "Tocino Ala Eh",
                "category": "Pork",
                "unit": "pack",
                "total_available": 90,
                "total_in": 100,
                "total_out": 10,
            }
        ],
    )

    response = client.get("/portal/team-leader/dashboard")

    assert response.status_code == 200
    assert "Product movement analytics" in response.text
    assert "data-inventory-movement-chart" in response.text
    assert "Tocino Ala Eh" in response.text
    assert "Recent inventory logs" not in response.text


def test_team_leader_dashboard_uses_priority_led_workspace():
    template = open("app/templates/portals/team-leader/dashboard.html", encoding="utf-8").read()
    base_template = open("app/templates/portals/base.html", encoding="utf-8").read()
    css = open("app/static/css/portals/team-leader.css", encoding="utf-8").read()

    assert "dashboard-command" in template
    assert "dashboard-brief" in template
    assert "dashboard-signal-strip" in template
    assert "dashboard-workspace" in template
    assert "dashboard-ledger" in template
    assert "metric-card" not in template
    assert 'section-{{ section }}' in base_template
    assert 'section != "dashboard"' in base_template
    assert "Dashboard: one operational surface" in css
    assert ".role-team-leader.section-dashboard .portal-topbar" in css
    assert "@media (max-width: 760px)" in css


def test_sales_team_leader_pages_use_workflow_specific_layouts():
    templates = {
        name: open(f"app/templates/portals/team-leader/{name}.html", encoding="utf-8").read()
        for name in ("inquiries", "orders", "reports", "profile", "live_chat")
    }
    base_template = open("app/templates/portals/base.html", encoding="utf-8").read()
    css = open("app/static/css/portals/team-leader.css", encoding="utf-8").read()

    assert "leader-role-{{ role.team_leader_role }}" in base_template
    assert 'role_key in ["team-leader", "sales-supervisor"]' in base_template
    assert "sales-review-surface" in templates["inquiries"]
    assert "sales-review-surface" in templates["orders"]
    assert "sales-report-builder" in templates["reports"]
    assert "sales-report-history" in templates["reports"]
    assert "sales-report-ledger" in templates["reports"]
    assert "sales-profile-identity" in templates["profile"]
    assert "sales-profile-security" in templates["profile"]
    assert "sales-live-chat-page" in templates["live_chat"]
    assert "Sales portal: shared materials" in css
    assert ".role-team-leader.leader-role-sales .sales-review-surface" in css
    assert ".role-team-leader.leader-role-sales .sales-page .sales-report-history" in css
    assert ".role-team-leader.leader-role-sales .sales-page .sales-profile-identity" in css
    assert "min-height: 42px" in css
    assert "@media (max-width: 860px)" in css


def test_sales_supervisor_gets_accounts_without_owner_creation(monkeypatch):
    client = TestClient(main.app)
    list_calls = []
    count_calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales-supervisor")
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    def fake_count_accounts(*args, **kwargs):
        count_calls.append(kwargs)
        return 2

    monkeypatch.setattr(repositories, "count_accounts", fake_count_accounts)
    monkeypatch.setattr(repositories, "list_team_leader_accounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repositories,
        "list_reseller_assignments",
        lambda *args, **kwargs: [
            {
                "reseller_id": 2,
                "business_name": "Lipa Store",
                "reseller_status": "active",
                "contact_person": "Rina",
                "email": "rina@example.test",
                "team_leader_account_id": None,
                "team_leader_name": None,
            }
        ],
    )
    def fake_list_accounts(*args, **kwargs):
        list_calls.append(kwargs)
        return [
            {
                "account_id": 7,
                "name": "Sales Supervisor",
                "email": "supervisor@example.test",
                "account_type": "team_leader",
                "team_leader_role": "sales-supervisor",
                "status": "pending",
                "activation_status": "pending",
            },
            {
                "account_id": 8,
                "name": "Rina Reseller",
                "email": "rina@example.test",
                "account_type": "reseller",
                "status": "pending",
                "activation_status": "pending",
            },
        ]

    monkeypatch.setattr(repositories, "list_accounts", fake_list_accounts)

    sales_nav = [slug for slug, *_ in repositories.portal_nav_for("team-leader", "sales")]
    supervisor_nav = [slug for slug, *_ in repositories.portal_nav_for("sales-supervisor", "sales-supervisor")]
    response = client.get("/portal/sales-supervisor/accounts")

    assert "accounts" not in sales_nav
    assert "accounts" in supervisor_nav
    assert response.status_code == 200
    assert "role-team-leader role-sales-supervisor" in response.text
    assert 'css/portals/team-leader.css' in response.text
    assert 'action="/portal/sales-supervisor/accounts"' in response.text
    assert '<option value="owner">Owner</option>' not in response.text
    assert '<option value="reseller" >Reseller</option>' in response.text
    assert '<option value="sales-supervisor">Sales supervisor</option>' in response.text
    assert "Rina Reseller" in response.text
    assert "owner-assignment-card" in response.text
    assert "/portal/sales-supervisor/resellers/2/team-leader" in response.text
    assert list_calls[0]["account_types"] == ("team_leader", "reseller")
    assert count_calls[0]["account_types"] == ("team_leader", "reseller")


def test_sales_supervisor_account_post_cannot_create_owner(monkeypatch):
    client = TestClient(main.app)
    created = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "add_account", lambda *args, **kwargs: created.append((args, kwargs)))

    blocked = client.post(
        "/portal/sales-supervisor/accounts",
        data={"account_type": "owner", "name": "Owner Two", "email": "owner2@example.test"},
        follow_redirects=False,
    )
    allowed = client.post(
        "/portal/sales-supervisor/accounts",
        data={
            "account_type": "team_leader",
            "name": "Supervisor Two",
            "email": "supervisor2@example.test",
            "team_leader_role": "sales-supervisor",
        },
        follow_redirects=False,
    )

    assert blocked.status_code == 303
    assert "Sales+supervisors+can+create+team+leader+accounts+only" in blocked.headers["location"]
    assert allowed.status_code == 303
    assert created == [(("team_leader", "Supervisor Two", "supervisor2@example.test"), {"team_leader_role": "sales-supervisor"})]


def test_sales_supervisor_activation_route_includes_reseller_accounts(monkeypatch):
    client = TestClient(main.app)
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales-supervisor")
    monkeypatch.setattr(
        repositories,
        "resend_activation",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = client.post("/portal/sales-supervisor/accounts/8/activation", follow_redirects=False)

    assert response.status_code == 303
    assert calls == [((8, 0), {"account_types": ("team_leader", "reseller")})]


def test_owner_pages_use_workflow_specific_modern_layouts():
    templates = {
        name: open(f"app/templates/portals/owner/{name}.html", encoding="utf-8").read()
        for name in ("dashboard", "products", "reports", "forecasts", "accounts", "profile")
    }
    base_template = open("app/templates/portals/base.html", encoding="utf-8").read()
    css = open("app/static/css/portals/owner.css", encoding="utf-8").read()

    assert 'role_key == "owner" or' in base_template
    assert "owner-dashboard-page" in templates["dashboard"]
    assert "owner-signal-strip" in templates["dashboard"]
    assert "metric-card" not in templates["dashboard"]
    assert "owner-products-page" in templates["products"]
    assert "owner-workspace-surface" in templates["products"]
    assert "owner-reports-page" in templates["reports"]
    assert "owner-forecasts-page" in templates["forecasts"]
    assert "owner-accounts-page" in templates["accounts"]
    assert "owner-profile-identity" in templates["profile"]
    assert "owner-profile-security" in templates["profile"]
    assert "Owner workspace: restrained operational layouts" in css
    assert ".role-owner .owner-product-image::after" in css
    assert ".role-owner .owner-forecast-run-panel" in css
    assert ".role-owner .owner-profile-page" in css
    assert ".role-owner .owner-filter-bar label:first-child" in css
    assert ".role-owner .owner-horizon-picker input" in css
    assert "width: 100%;" in css
    assert "@media (max-width: 1440px)" in css
    assert "@media (max-width: 860px)" in css


def test_dropdown_controls_use_shared_stable_styles():
    portal_css = open("app/static/css/portal_base.css", encoding="utf-8").read()
    public_css = open("app/static/css/public/base.css", encoding="utf-8").read()
    owner_css = open("app/static/css/portals/owner.css", encoding="utf-8").read()

    for css in (portal_css, public_css):
        assert "--select-caret" in css
        assert "select:not([multiple])" in css
        assert "padding-right: 2.85rem !important" in css
        assert "background-image: var(--select-caret) !important" in css
        assert "text-overflow: ellipsis" in css

    assert ".role-owner .owner-forecast-result-card summary" in owner_css
    assert "details[open] summary::after" in owner_css


def test_templates_load_only_their_focused_frontend_modules():
    public_template = open("app/templates/landing.html", encoding="utf-8").read()
    login_template = open("app/templates/login.html", encoding="utf-8").read()
    portal_template = open("app/templates/portals/base.html", encoding="utf-8").read()
    inventory_template = open(
        "app/templates/portals/team-leader/inventory.html", encoding="utf-8"
    ).read()
    reports_template = open(
        "app/templates/portals/team-leader/reports.html", encoding="utf-8"
    ).read()
    portal_js = open("app/static/js/portal.js", encoding="utf-8").read()
    charts_js = open("app/static/js/charts.js", encoding="utf-8").read()

    assert "js/core.js" in public_template
    assert "js/forms.js" in public_template
    assert "js/public-site.js" in public_template
    assert "js/portal.js" not in public_template
    assert "js/core.js" in login_template
    assert "js/forms.js" in login_template
    assert "js/public-site.js" not in login_template
    for module in ("core", "forms", "portal", "profile", "charts"):
        assert f"js/{module}.js" in portal_template
    assert "js/public-site.js" not in portal_template
    assert "js/app.js" not in public_template + login_template + portal_template
    assert "<script>" not in inventory_template
    assert "<script>" not in reports_template
    assert "function bindRecipeRows()" in portal_js
    assert "function bindTeamReportSales()" in charts_js


def test_payment_proof_route_is_registered_before_generic_portal_route():
    route_contexts = [
        context
        for route in main.app.routes
        for context in (
            route.effective_route_contexts()
            if hasattr(route, "effective_route_contexts")
            else (route,)
        )
        if getattr(context, "path", "")
    ]
    route_paths = [context.path for context in route_contexts]
    route_method_pairs = [
        (context.path, method)
        for context in route_contexts
        for method in (getattr(context, "methods", set()) or set())
    ]

    assert route_paths.index("/portal/order-payment-proofs/{proof_id}") < route_paths.index("/portal/{role_key}/{section}")
    assert route_paths.index("/portal/owner/dashboard/sales-chart") < route_paths.index("/portal/{role_key}/{section}")
    assert route_paths.index("/portal/team-leader/finished-products/products") < route_paths.index("/portal/{role_key}/{section}")
    assert len(route_method_pairs) == len(set(route_method_pairs))


def test_portal_notifications_use_modern_shared_visual_hierarchy():
    css = open("app/static/css/portal_base.css", encoding="utf-8").read()
    template = open("app/templates/portals/base.html", encoding="utf-8").read()

    assert "grid-template-columns: 38px minmax(0, 1fr) 8px" in css
    assert "notification-panel-enter" in css
    assert ".notification-item.warning" in css
    assert ".notification-item.critical" in css
    assert ".notification-unread-dot" in css
    assert ".notification-item.is-read .notification-item-copy" in css
    assert "notification-item-icon" in template
    assert "notification-item-meta" in template
    assert "Mark all read" in template


def test_raw_materials_page_renders_only_raw_inventory(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_inventory_items", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
        "list_inventory_items",
        lambda *args, **kwargs: [
            {
                "item_id": 1,
                "item_type": "raw_material",
                "item_type_label": "Raw material",
                "category": "meat",
                "name": "Beef",
                "unit": "kg",
                "base_price": 0,
                "available": 20,
                "is_active": True,
            }
        ],
    )

    response = client.get("/portal/team-leader/raw-materials")

    assert response.status_code == 200
    assert "Raw materials inventory" in response.text
    assert "Beef" in response.text
    assert "Finished products inventory" not in response.text
    assert "data-inventory-products-region" not in response.text


def test_finished_products_page_renders_product_cards(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_inventory_items", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
        "list_inventory_items",
        lambda *args, **kwargs: [
            {
                "item_id": 2,
                "item_type": "finished_product",
                "item_type_label": "Finished product",
                "category": "Pork",
                "name": "Tocino Ala Eh",
                "unit": "pack",
                "base_price": 70,
                "available": 90,
                "is_active": True,
            }
        ],
    )

    response = client.get("/portal/team-leader/finished-products")

    assert response.status_code == 200
    assert "Finished products inventory" in response.text
    assert "inventory-product-card" in response.text
    assert "Tocino Ala Eh" in response.text
    assert "data-inventory-products-region" in response.text
    assert "Raw materials inventory" not in response.text


def test_inventory_finished_products_partial_returns_only_product_region(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(repositories, "count_inventory_items", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        repositories,
        "list_inventory_items",
        lambda *args, **kwargs: [
            {
                "item_id": 2,
                "item_type": "finished_product",
                "item_type_label": "Finished product",
                "category": "Pork",
                "name": "Tocino Ala Eh",
                "unit": "pack",
                "base_price": 70,
                "available": 90,
                "is_active": True,
            }
        ],
    )

    response = client.get("/portal/team-leader/finished-products/products?q=tocino&type=Pork&page=1")

    assert response.status_code == 200
    assert "data-inventory-products-region" in response.text
    assert "inventory-product-card" in response.text
    assert "portal-sidebar" not in response.text
    assert "Raw materials" not in response.text


def test_legacy_inventory_items_redirects_to_raw_materials(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")

    response = client.get("/portal/team-leader/inventory-items?q=beef&type=meat&page=2", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/team-leader/raw-materials?q=beef&type=meat&page=2"

