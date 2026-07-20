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
    "count_products",
    "count_inquiries",
    "count_orders",
    "count_sales_reports",
    "count_forecasts",
    "count_accounts",
    "count_activity_logs",
    "team_sales_report_entries",
    "team_rejected_order_entries",
    "reseller_most_bought_products",
    "reseller_sales_series",
    "reseller_reportable_products",
    "list_notifications",
    "unread_notification_count",
)

EXPECTED_CALLS = {
    ("owner", "dashboard"): {"current_metrics", "list_products", "list_alerts", "list_forecasts", "list_notifications", "unread_notification_count"},
    ("owner", "products"): {"list_products", "count_products", "list_notifications", "unread_notification_count"},
    ("owner", "reports"): {"list_sales_reports", "count_sales_reports", "list_notifications", "unread_notification_count"},
    ("owner", "forecasts"): {"list_forecasts", "count_forecasts", "list_notifications", "unread_notification_count"},
    ("owner", "accounts"): {"list_accounts", "count_accounts", "list_notifications", "unread_notification_count"},
    ("owner", "logs"): {"list_activity_logs", "count_activity_logs", "list_notifications", "unread_notification_count"},
    ("team-leader", "dashboard"): {"current_metrics", "list_inquiries", "list_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "sales"): {"list_products", "list_orders", "count_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "inventory"): {
        "list_products",
        "list_inventory_items",
        "list_inventory_batches",
        "list_raw_materials",
        "list_product_recipes",
        "list_alerts",
        "list_notifications",
        "unread_notification_count",
    },
    ("team-leader", "inquiries"): {"list_inquiries", "count_inquiries", "list_notifications", "unread_notification_count"},
    ("team-leader", "orders"): {"list_orders", "count_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "reports"): {
        "list_sales_reports",
        "count_sales_reports",
        "team_sales_report_entries",
        "team_rejected_order_entries",
        "list_notifications",
        "unread_notification_count",
    },
    ("reseller", "dashboard"): {
        "current_metrics",
        "reseller_most_bought_products",
        "reseller_sales_series",
        "list_sales_reports",
        "list_notifications",
        "unread_notification_count",
    },
    ("reseller", "order"): {"list_products", "list_notifications", "unread_notification_count"},
    ("reseller", "cart"): {"list_products", "list_notifications", "unread_notification_count"},
    ("reseller", "history"): {"list_orders", "list_notifications", "unread_notification_count"},
    ("reseller", "reports"): {"list_sales_reports", "list_notifications", "unread_notification_count"},
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


def test_section_filters_are_applied(monkeypatch):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)

    assert client.get("/portal/team-leader/dashboard").status_code == 200
    assert calls["list_inquiries"] == [((), {"limit": 4})]
    assert calls["list_orders"] == [((), {"order_type": "walk_in", "limit": 5})]

    calls = install_read_spies(monkeypatch)
    assert client.get("/portal/team-leader/sales").status_code == 200
    assert calls["list_orders"] == [((), {"order_type": "walk_in", "q": "", "status": "", "page": 1, "page_size": 10})]
    assert calls["count_orders"] == [((), {"order_type": "walk_in", "q": "", "status": ""})]

    calls = install_read_spies(monkeypatch)
    assert client.get("/portal/reseller/dashboard").status_code == 200
    assert calls["reseller_most_bought_products"] == [((), {"limit": 3})]
    assert len(calls["reseller_sales_series"]) == 1
    assert calls["reseller_sales_series"][0][0][2] == "fulfilled"
    assert calls["list_sales_reports"] == [((), {"report_source": "reseller", "limit": 1})]


def test_landing_page_does_not_load_metrics(monkeypatch):
    monkeypatch.setattr(main.data, "list_products", lambda: [])
    monkeypatch.setattr(
        main.data,
        "current_metrics",
        lambda: pytest.fail("landing page must not load dashboard metrics"),
    )

    response = TestClient(main.app).get("/")

    assert response.status_code == 200
