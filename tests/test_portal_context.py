from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from app import main


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
    ("owner", "forecasts"): {"list_forecasts", "count_forecasts", "latest_forecast_run", "list_notifications", "unread_notification_count"},
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
    ("team-leader", "logs"): {"list_activity_logs", "count_activity_logs", "list_notifications", "unread_notification_count"},
    ("team-leader", "profile"): {"list_notifications", "unread_notification_count"},
}


def install_read_spies(monkeypatch):
    calls = defaultdict(list)
    for name in READ_FUNCTIONS:
        result = METRICS if name == "current_metrics" else 0 if name.startswith(("count_", "unread_")) else []

        def fake(*args, _name=name, _result=result, **kwargs):
            calls[_name].append((args, kwargs))
            return _result

        monkeypatch.setattr(main.data, name, fake)
    return calls


@pytest.mark.parametrize("role_key,section", EXPECTED_CALLS)
def test_each_portal_section_loads_only_its_required_data(monkeypatch, role_key, section):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)

    response = client.get(f"/portal/{role_key}/{section}")

    assert response.status_code == 200
    actual_calls = {name for name, entries in calls.items() if entries}
    assert actual_calls == EXPECTED_CALLS[(role_key, section)]


@pytest.mark.parametrize("role_key,section", INVENTORY_EXPECTED_CALLS)
def test_inventory_team_leader_sections_load_only_inventory_data(monkeypatch, role_key, section):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "inventory")

    response = client.get(f"/portal/{role_key}/{section}")

    assert response.status_code == 200
    actual_calls = {name for name, entries in calls.items() if entries}
    assert actual_calls == INVENTORY_EXPECTED_CALLS[(role_key, section)]
    if section == "dashboard":
        assert calls["inventory_product_movement_analytics"] == [((), {"days": 30, "limit": 8})]
    if section == "logs":
        assert calls["list_activity_logs"] == [((), {"q": "", "page": 1, "page_size": 10, "inventory_only": True})]
        assert calls["count_activity_logs"] == [((), {"q": "", "inventory_only": True})]

    if section == "inventory":
        assert calls["list_products"] == [
            ((), {"q": "", "category": "", "page": 1, "page_size": 8}),
            ((), {}),
        ]
        assert calls["count_products"] == [((), {"q": "", "category": ""})]
        assert calls["list_product_recipes"] == [((), {"product_ids": []})]

    if section == "raw-materials":
        assert calls["list_inventory_items"] == [((), {"q": "", "category": "raw_material", "page": 1, "page_size": 10})]
        assert calls["count_inventory_items"] == [((), {"q": "", "category": "raw_material"})]

    if section == "finished-products":
        assert calls["list_inventory_items"] == [((), {"q": "", "category": "finished_product", "page": 1, "page_size": 8})]
        assert calls["count_inventory_items"] == [((), {"q": "", "category": "finished_product"})]

    if section == "batches":
        assert calls["list_inventory_batches"] == [((), {"q": "", "category": "", "page": 1, "page_size": 10})]
        assert calls["count_inventory_batches"] == [((), {"q": "", "category": ""})]


def test_section_filters_are_applied(monkeypatch):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)

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
    assert calls["list_products"] == [((), {"q": "tocino", "category": "Pork", "page": 2, "page_size": 8})]
    assert calls["count_products"] == [((), {"q": "tocino", "category": "Pork"})]

    calls = install_read_spies(monkeypatch)
    assert client.get("/portal/reseller/history?q=order&status=pending&page=2").status_code == 200
    assert calls["list_orders"] == [((), {"order_type": "reseller", "q": "order", "status": "pending", "team_leader_account_id": None, "reseller_account_id": None, "page": 2, "page_size": 10})]
    assert calls["count_orders"] == [((), {"order_type": "reseller", "q": "order", "status": "pending", "team_leader_account_id": None, "reseller_account_id": None})]


def test_landing_page_does_not_load_metrics(monkeypatch):
    monkeypatch.setattr(main.data, "list_products", lambda: [])
    monkeypatch.setattr(
        main.data,
        "current_metrics",
        lambda: pytest.fail("landing page must not load dashboard metrics"),
    )

    response = TestClient(main.app).get("/")

    assert response.status_code == 200


def test_landing_mobile_catalog_is_compact_and_drawer_based(monkeypatch):
    monkeypatch.setattr(
        main.data,
        "list_products",
        lambda: [
            {
                "name": "Tocino Ala Eh",
                "description": "Tocino - 500 g per pack.",
                "category": "Pork",
                "base_price": 70,
            }
        ],
    )
    response = TestClient(main.app).get("/")
    css = open("app/static/css/public.css", encoding="utf-8").read()

    assert response.status_code == 200
    assert 'id="store" class="products-section section-band"' in response.text
    assert "mobile-drawer-brand" in response.text
    assert "batangas_premium.png" in response.text
    assert "store-intro" not in response.text
    assert "Product-first cards" not in response.text
    assert "Reseller Packages" not in response.text
    assert ".logo {\n  display: none;" in css
    assert "justify-content: center;" in css
    assert ".nav-links li:last-child {\n  position: absolute;" in css
    assert "right: 0;" in css
    assert "inset: calc(14px + env(safe-area-inset-top)) auto auto 14px;" in css
    assert ".nav-title {\n    display: none;" in css
    assert ".nav-links li:last-child {\n    grid-column: auto;\n    position: static;" in css
    assert ".mobile-drawer-brand {\n    display: grid;" in css
    assert "justify-items: center;" in css
    assert ".mobile-drawer-brand img" in css
    assert "width: 112px;" in css
    assert "width: min(210px, 70vw);" in css
    assert "justify-self: center;" in css
    assert "margin-inline: auto;" in css
    assert "pointer-events: none;" in css
    assert "background: transparent" in css
    assert "translateX(-105%)" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert ".product-card p {\n    display: none;" in css
    assert "width: 44px;" in css
    assert "bottom: calc(10px + env(safe-area-inset-bottom));" in css
    assert "width: min(356px, calc(100vw - 20px));" in css
    assert ".chatbot-widget.is-open .chatbot-toggle" in css
    app_js = open("app/static/js/app.js", encoding="utf-8").read()
    assert 'widget.classList.toggle("is-open", open);' in app_js
    assert 'document.addEventListener("pointerdown", (event) =>' in app_js
    assert "nav.contains(event.target) || toggle.contains(event.target)" in app_js
    assert "meattrack_chatbot_messages_v1" in app_js
    assert "24 * 60 * 60 * 1000" in app_js
    assert "function bindOtpModal()" in app_js
    assert "data-otp-modal-input" in app_js


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
    slugs = [slug for slug, *_ in main.data.portal_nav_for("reseller")]

    assert "profile" in slugs
    assert "reports" not in slugs


def test_team_leader_nav_includes_profile_for_sales_and_inventory():
    assert "profile" in [slug for slug, *_ in main.data.portal_nav_for("team-leader", "sales")]
    assert "profile" in [slug for slug, *_ in main.data.portal_nav_for("team-leader", "inventory")]


def test_password_otp_confirmation_renders_as_modal(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 9)
    monkeypatch.setattr(main.data, "account_portal_profile", lambda account_id: {
        "account_id": 9,
        "name": "Sales Leader",
        "email": "sales@example.test",
        "account_type": "team_leader",
        "role_key": "team-leader",
        "team_leader_role": "sales",
    })
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)

    response = client.get("/portal/team-leader/profile?otp=1")

    assert response.status_code == 200
    assert "data-otp-modal" in response.text
    assert "Confirm password change" in response.text
    assert "action=\"/portal/team-leader/profile/password/confirm\"" in response.text
    assert "otp-panel-active" not in response.text


def test_reseller_reports_section_is_removed(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)

    response = client.get("/portal/reseller/reports")

    assert response.status_code == 404


def test_owner_logs_section_is_removed(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)

    slugs = [slug for slug, *_ in main.data.portal_nav_for("owner")]
    response = client.get("/portal/owner/logs")

    assert "logs" not in slugs
    assert response.status_code == 404


def test_owner_products_render_product_cards_and_price_form(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_products", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
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
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main.data, "product_by_id", lambda product_id: {"product_id": product_id})
    monkeypatch.setattr(main.data, "update_product_price", lambda *args: calls.append(args))

    response = client.post(
        "/portal/owner/products",
        data={"product_id": 1, "base_price": 129},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(1, 129.0)]


def test_owner_accounts_render_responsive_cards(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_accounts", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
        "list_accounts",
        lambda *args, **kwargs: [
            {"name": "Owner", "email": "owner@example.test", "account_type": "owner", "auth_provider": None, "status": "active"}
        ],
    )
    monkeypatch.setattr(main.data, "list_team_leader_accounts", lambda *args, **kwargs: [{"account_id": 4, "name": "Sales Leader B"}])
    monkeypatch.setattr(
        main.data,
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
    assert "owner-assignment-card" in response.text
    assert "/portal/owner/resellers/2/team-leader" in response.text


def test_owner_reports_render_mobile_cards(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_sales_reports", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
        "list_sales_reports",
        lambda *args, **kwargs: [
            {
                "sales_report_id": 1,
                "report_source": "team_leader",
                "submitted_by": "Sales Leader B",
                "period_start": main.date.today(),
                "period_end": main.date.today(),
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
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_forecasts", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
        "latest_forecast_run",
        lambda *args, **kwargs: {
            "model_name": "Prophet demand forecast",
            "input_period_start": main.date.today(),
            "input_period_end": main.date.today(),
            "forecast_horizon_days": 7,
            "status": "completed",
            "notes": "Completed with: Prophet, Baseline fallback.",
        },
    )
    monkeypatch.setattr(
        main.data,
        "list_forecasts",
        lambda *args, **kwargs: [
            {
                "product": "Tocino Ala Eh",
                "forecast_date": main.date.today(),
                "predicted_quantity": 12,
                "confidence": "10 - 14 packs",
                "model_name": "Prophet demand forecast",
                "notes": "Completed with: Prophet.",
            }
        ],
    )

    response = client.get("/portal/owner/forecasts")

    assert response.status_code == 200
    assert "Prophet uses fulfilled reseller order history" in response.text
    assert "owner-forecast-result-card" in response.text
    assert "owner-horizon-picker" in response.text
    assert 'value="30"' in response.text
    assert 'data-forecast-horizon-value' in response.text
    assert "10 - 14 packs" in response.text


def test_owner_dashboard_renders_executive_sections(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        main.data,
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
        main.data,
        "list_products",
        lambda *args, **kwargs: [
            {"product_id": 1, "name": "Tocino Ala Eh", "available": 90, "unit": "pack"}
        ],
    )
    monkeypatch.setattr(
        main.data,
        "list_forecasts",
        lambda *args, **kwargs: [
            {
                "forecast_result_id": 1,
                "product": "Tocino Ala Eh",
                "forecast_date": main.date.today(),
                "predicted_quantity": 42,
                "confidence": "85% - 95% range",
            }
        ],
    )
    monkeypatch.setattr(
        main.data,
        "reseller_sales_series",
        lambda *args, **kwargs: [
            {"sale_date": main.date.today(), "total_sales": 700, "order_count": 1}
        ],
    )
    monkeypatch.setattr(
        main.data,
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


def test_owner_sales_chart_endpoint_returns_period_json(monkeypatch):
    client = TestClient(main.app)
    today = main.date.today()
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(
        main.data,
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
    today = main.date(2026, 7, 21)

    assert len(main.owner_sales_period_points("daily", today)[1]) == 31
    assert len(main.owner_sales_period_points("weekly", today)[1]) == 12
    assert len(main.owner_sales_period_points("monthly", today)[1]) == 12
    assert len(main.owner_sales_period_points("quarterly", today)[1]) == 8
    assert len(main.owner_sales_period_points("yearly", today)[1]) == 5


def test_owner_dashboard_sales_chart_uses_continuous_line_series(monkeypatch):
    today = main.date.today()
    monkeypatch.setattr(
        main.data,
        "current_metrics",
        lambda *args, **kwargs: {
            "fulfilled_sales": 700,
            "pending_reseller_orders": 0,
            "open_alerts": 0,
            "active_resellers": 1,
            "total_available": 100,
        },
    )
    monkeypatch.setattr(main.data, "list_products", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "list_forecasts", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "reseller_most_bought_products", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main.data,
        "reseller_sales_series",
        lambda *args, **kwargs: [
            {"sale_date": today, "total_sales": 700, "order_count": 1}
        ],
    )

    context = main._owner_dashboard_context(None)

    assert len(context["owner_sales_chart"]) == 31
    assert context["owner_sales_chart"][-1]["sales"] == 700
    assert context["owner_sales_chart"][0]["sales"] == 0


def test_inventory_dashboard_renders_product_movement_chart(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        main.data,
        "current_metrics",
        lambda *args, **kwargs: {
            "fulfilled_sales": 0,
            "pending_reseller_orders": 0,
            "open_alerts": 0,
            "active_resellers": 0,
            "total_available": 100,
        },
    )
    monkeypatch.setattr(main.data, "list_products", lambda *args, **kwargs: [{"product_id": 1, "name": "Tocino"}])
    monkeypatch.setattr(
        main.data,
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


def test_payment_proof_route_is_registered_before_generic_portal_route():
    route_paths = [getattr(route, "path", "") for route in main.app.routes]

    assert route_paths.index("/portal/order-payment-proofs/{proof_id}") < route_paths.index("/portal/{role_key}/{section}")


def test_portal_notifications_use_readable_color_scheme():
    css = open("app/static/css/portal_base.css", encoding="utf-8").read()

    assert "background: #fffdf6;" in css
    assert "border-left: 5px solid var(--info);" in css
    assert ".notification-item.warning" in css
    assert "background: #fff8e8;" in css
    assert ".notification-item.critical" in css
    assert "background: #fff1ef;" in css
    assert ".notification-item strong" in css
    assert "color: var(--primary);" in css
    assert ".notification-item.is-read span" in css


def test_raw_materials_page_renders_only_raw_inventory(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_inventory_items", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
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
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(main.data, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(main.data, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main.data, "count_inventory_items", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
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
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(main.data, "count_inventory_items", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        main.data,
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
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "session_team_leader_role", lambda request: "inventory")

    response = client.get("/portal/team-leader/inventory-items?q=beef&type=meat&page=2", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/team-leader/raw-materials?q=beef&type=meat&page=2"
