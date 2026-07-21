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
    "list_notifications",
    "unread_notification_count",
)

EXPECTED_CALLS = {
    ("owner", "dashboard"): {"current_metrics", "list_products", "list_forecasts", "list_notifications", "unread_notification_count"},
    ("owner", "products"): {"list_products", "count_products", "list_notifications", "unread_notification_count"},
    ("owner", "reports"): {"list_sales_reports", "count_sales_reports", "list_notifications", "unread_notification_count"},
    ("owner", "forecasts"): {"list_forecasts", "count_forecasts", "list_notifications", "unread_notification_count"},
    ("owner", "accounts"): {
        "list_accounts",
        "count_accounts",
        "list_team_leader_accounts",
        "list_reseller_assignments",
        "list_notifications",
        "unread_notification_count",
    },
    ("owner", "logs"): {"list_activity_logs", "count_activity_logs", "list_notifications", "unread_notification_count"},
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


def test_reseller_nav_includes_profile():
    slugs = [slug for slug, *_ in main.data.portal_nav_for("reseller")]

    assert "profile" in slugs
    assert "reports" not in slugs


def test_reseller_reports_section_is_removed(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)

    response = client.get("/portal/reseller/reports")

    assert response.status_code == 404


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
