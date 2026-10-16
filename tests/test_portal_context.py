from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web
from app.portals import owner as owner_portal
from app.portals import reseller as reseller_portal
from app.portals import shared as shared_portal
from app.portals import team_leader as team_leader_portal


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
    "reseller_cart_count",
    "list_reseller_cart_items",
    "reseller_account_profile",
    "account_portal_profile",
    "list_team_leader_accounts",
    "list_reseller_assignments",
    "list_team_resellers",
    "count_team_resellers",
    "latest_forecast_run",
    "get_forecast_run",
    "list_forecast_runs",
    "list_notifications",
    "list_order_system_messages",
    "get_order_system_message",
    "unread_order_system_message_count",
    "list_notification_history",
    "count_notification_history",
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
    ("team-leader", "dashboard"): {"current_metrics", "list_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "resellers"): {"list_team_resellers", "count_team_resellers", "list_notifications", "unread_notification_count"},
    ("sales-supervisor", "inquiries"): {"list_inquiries", "count_inquiries", "list_notifications", "unread_notification_count"},
    ("team-leader", "orders"): {"list_orders", "count_orders", "list_notifications", "unread_notification_count"},
    ("team-leader", "reports"): {
        "list_sales_reports",
        "count_sales_reports",
        "team_sales_report_entries",
        "team_rejected_order_entries",
        "list_notifications",
        "unread_notification_count",
    },
    ("team-leader", "profile"): {"list_notifications", "unread_notification_count"},
    ("reseller", "dashboard"): {
        "current_metrics",
        "reseller_most_bought_products",
        "reseller_sales_series",
        "reseller_cart_count",
        "list_notifications",
        "unread_notification_count",
    },
    ("reseller", "order"): {"list_products", "count_products", "reseller_cart_count", "list_notifications", "unread_notification_count"},
    ("reseller", "cart"): {"reseller_cart_count", "list_notifications", "unread_notification_count"},
    ("reseller", "checkout"): {"reseller_cart_count", "list_notifications", "unread_notification_count"},
    ("reseller", "history"): {"list_orders", "count_orders", "reseller_cart_count", "list_notifications", "unread_notification_count"},
    ("reseller", "inbox"): {"list_order_system_messages", "unread_order_system_message_count", "reseller_cart_count", "list_notifications", "unread_notification_count"},
    ("reseller", "profile"): {"reseller_cart_count", "list_notifications", "unread_notification_count"},
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
        result = METRICS if name == "current_metrics" else 0 if name.startswith(("count_", "unread_", "reseller_cart_count")) else []

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
        assert calls["list_inventory_movements"] == [(
            (),
            {"q": "", "category": "", "page": 1, "page_size": 10, "sort": ""},
        )]
        assert calls["count_inventory_movements"] == [((), {"q": "", "category": ""})]

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
    assert calls["list_inquiries"] == []
    assert calls["list_orders"] == [((), {"order_type": "reseller", "limit": 5, "team_leader_account_id": None})]

    calls = install_read_spies(monkeypatch)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 17)
    response = client.get("/portal/team-leader/resellers?q=lipa&status=active&sort=business_asc&page=2")
    assert response.status_code == 200
    assert calls["list_team_resellers"] == [(
        (),
        {
            "team_leader_account_id": 17,
            "q": "lipa",
            "status": "active",
            "page": 2,
            "page_size": 10,
            "sort": "business_asc",
        },
    )]
    assert calls["count_team_resellers"] == [(
        (),
        {"team_leader_account_id": 17, "q": "lipa", "status": "active"},
    )]

    calls = install_read_spies(monkeypatch)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales-supervisor")
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: None)
    assert client.get("/portal/sales-supervisor/dashboard").status_code == 200
    assert calls["list_inquiries"] == [(
        (),
        {"limit": 4, "assigned_team_leader_account_id": None, "include_unassigned": True},
    )]

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


def test_team_leader_order_review_lists_every_order_item(monkeypatch):
    order = {
        "order_id": 26,
        "order_type": "reseller",
        "reseller": "Xort's store",
        "status": "fulfilled",
        "total_amount": Decimal("910.00"),
        "items": [
            {"quantity": 5, "name": "Tocino Ala Eh"},
            {"quantity": 8, "name": "Chicken Tocino"},
            {"quantity": 4, "name": "Deli Beef"},
            {"quantity": 2, "name": "Cheesy Overload Sausage"},
            {"quantity": 1, "name": "Bacon"},
        ],
        "payment_proofs": [],
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_orders", lambda **kwargs: [order])
    monkeypatch.setattr(repositories, "count_orders", lambda **kwargs: 1)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/team-leader/orders")

    assert response.status_code == 200
    assert "5 packs</strong> Tocino Ala Eh" in response.text
    assert "8 packs</strong> Chicken Tocino" in response.text
    assert "Show 2 more products" in response.text
    assert "4 packs</strong> Deli Beef" in response.text
    assert "1 packs</strong> Bacon" in response.text


def test_approved_order_review_allows_fulfillment_only(monkeypatch):
    order = {
        "order_id": 27,
        "order_type": "reseller",
        "reseller": "Xort's store",
        "status": "approved",
        "total_amount": Decimal("910.00"),
        "items": [{"quantity": 5, "name": "Tocino Ala Eh"}],
        "payment_proofs": [{"order_payment_proof_id": 8, "filename": "receipt.png"}],
        "payment_transaction_number": "9045990596496",
        "payment_amount": Decimal("910.00"),
        "payment_paid_at": datetime(2026, 10, 11, 7, 4),
        "payment_extraction_method": "ocr_confirmed",
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(repositories, "list_orders", lambda **kwargs: [order])
    monkeypatch.setattr(repositories, "count_orders", lambda **kwargs: 1)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/team-leader/orders")

    assert response.status_code == 200
    reject_control = response.text.split('data-order-action="reject"', 1)[1].split("</form>", 1)[0]
    fulfill_control = response.text.split('data-order-action="fulfill"', 1)[1].split("</form>", 1)[0]
    assert "disabled" in reject_control
    assert "disabled" not in fulfill_control
    assert "data-order-action-modal" in response.text
    assert "data-order-action-confirm" in response.text
    assert "data-order-rejection-field" in response.text
    assert "data-order-rejection-reason" in response.text
    assert 'name="rejection_reason"' in response.text


def test_reseller_inbox_renders_order_system_messages(monkeypatch):
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(repositories, "reseller_cart_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 1)
    monkeypatch.setattr(repositories, "unread_order_system_message_count", lambda *args, **kwargs: 7)
    monkeypatch.setattr(
        repositories,
        "list_order_system_messages",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [
            {
                "order_system_message_id": 91,
                "event_type": "rejected",
                "severity": "critical",
                "title": "Order rejected",
                "message": "Your reseller order #28 was rejected.",
                "rejection_reason": "Receipt amount does not match.",
                "order_id": 28,
                "read_at": None,
                "created_at": datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc),
            }
        ],
    )

    response = TestClient(main.app).get("/portal/reseller/inbox")

    assert response.status_code == 200
    assert "System messages" in response.text
    assert "Order rejected" in response.text
    assert "Receipt amount does not match." in response.text
    assert "reseller-system-message critical is-unread" in response.text
    assert calls == [((42,), {"limit": 20})]
    assert 'id="system-message-91"' in response.text
    assert 'data-system-message-id="91"' in response.text
    assert 'data-system-message-event="rejected"' in response.text
    assert 'action="/portal/reseller/notifications/9/open"' not in response.text
    assert 'href="/portal/reseller/notifications?type=order"' not in response.text
    assert 'data-system-message-modal' in response.text
    assert 'data-system-message-dialog-reason' in response.text
    assert 'data-reseller-inbox-tab="messages"' in response.text
    assert 'data-reseller-inbox-tab="system"' in response.text
    assert ">7</b>" in response.text


def test_opening_reseller_order_notification_marks_it_read_and_deep_links(monkeypatch):
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "mark_notification_read",
        lambda account_id, notification_id: calls.append((account_id, notification_id)) or {
            "notification_id": notification_id,
            "target_url": "/portal/reseller/history",
            "source_type": "order_system_messages",
            "source_id": 91,
        },
    )

    response = TestClient(main.app).post(
        "/portal/reseller/notifications/9/open",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/reseller/inbox?system_message=91#system-message-91"
    assert calls == [(42, 9)]


def test_reseller_inbox_deep_link_loads_system_message_outside_recent_window(monkeypatch):
    linked_calls = []
    linked_message = {
        "order_system_message_id": 91,
        "event_type": "approved",
        "severity": "info",
        "title": "Order approved",
        "message": "Your reseller order #28 was approved and is ready for fulfillment.",
        "rejection_reason": None,
        "order_id": 28,
        "read_at": None,
        "created_at": datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(repositories, "reseller_cart_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "unread_order_system_message_count", lambda *args, **kwargs: 1)
    monkeypatch.setattr(repositories, "list_order_system_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repositories,
        "get_order_system_message",
        lambda account_id, system_message_id: linked_calls.append((account_id, system_message_id)) or linked_message,
    )

    response = TestClient(main.app).get("/portal/reseller/inbox?system_message=91")

    assert response.status_code == 200
    assert linked_calls == [(42, 91)]
    assert 'data-open-system-message="91"' in response.text
    assert 'data-system-message-id="91"' in response.text
    assert "Order approved" in response.text


def test_reseller_system_message_read_is_separate_from_notification_read(monkeypatch):
    calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "mark_order_system_message_read",
        lambda account_id, system_message_id: calls.append((account_id, system_message_id)) or True,
    )

    response = TestClient(main.app).post("/portal/reseller/system-messages/91/read")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == [(42, 91)]


def test_inventory_logs_apply_movement_category_and_render_compact_ledger(monkeypatch):
    calls = install_read_spies(monkeypatch)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")

    response = TestClient(main.app).get("/portal/team-leader/logs?type=production&q=tocino&page=2")

    assert response.status_code == 200
    assert calls["list_inventory_movements"] == [(
        (),
        {"q": "tocino", "category": "production", "page": 2, "page_size": 10, "sort": ""},
    )]
    assert calls["count_inventory_movements"] == [((), {"q": "tocino", "category": "production"})]
    assert "Stock movements" in response.text
    assert 'aria-label="Movement categories"' in response.text
    assert "Actor</th>" not in response.text
    assert "Reference</th>" not in response.text


def test_inventory_batches_render_compact_shelf_life_ledger(monkeypatch):
    calls = []
    batch = {
        "batch_id": 17,
        "item_id": 8,
        "item_name": "Tocino Ala Eh",
        "category": "Package",
        "batch_code": "TOC-2026-017",
        "source_type": "production",
        "quantity_received": Decimal("40"),
        "quantity_available": Decimal("26"),
        "unit": "pack",
        "received_date": date(2026, 10, 10),
        "expiry_date": date(2026, 10, 20),
        "quality_status": "approved",
        "days_until_expiry": 4,
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(
        repositories,
        "list_inventory_batches",
        lambda **kwargs: calls.append(kwargs) or [batch],
    )
    monkeypatch.setattr(repositories, "count_inventory_batches", lambda **kwargs: 24)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/team-leader/batches?type=Package&page=2")

    assert response.status_code == 200
    assert calls == [{"q": "", "category": "Package", "page": 2, "page_size": 10, "sort": ""}]
    assert "Product batches" in response.text
    assert 'aria-label="Product batch categories"' in response.text
    assert "4 days left" in response.text
    assert 'aria-label="Page 2"' in response.text
    assert "<table" not in response.text


def test_reseller_order_history_views_apply_server_side_filters(monkeypatch):
    calls = install_read_spies(monkeypatch)
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)

    response = client.get("/portal/reseller/history?view=pending&q=tocino")

    assert response.status_code == 200
    assert 'class="active"' in response.text
    assert calls["list_orders"] == [((), {
        "order_type": "reseller",
        "q": "tocino",
        "status": "pending",
        "team_leader_account_id": None,
        "reseller_account_id": None,
        "page": 1,
        "page_size": 10,
        "sort": "",
    })]
    assert calls["count_orders"] == [((), {
        "order_type": "reseller",
        "q": "tocino",
        "status": "pending",
        "team_leader_account_id": None,
        "reseller_account_id": None,
    })]

    calls = install_read_spies(monkeypatch)
    response = client.get("/portal/reseller/history?view=approved")

    assert response.status_code == 200
    assert calls["list_orders"][0][1]["status"] == "approved"


def test_reseller_history_hides_proof_upload_after_payment_is_submitted(monkeypatch):
    order = {
        "order_id": 28,
        "order_type": "reseller",
        "order_date": datetime(2026, 10, 16, 22, 50),
        "status": "pending",
        "total_amount": Decimal("1133.00"),
        "items": [{"quantity": 1, "name": "Tocino Ala Eh"}],
        "payment_proofs": [{"order_payment_proof_id": 9, "filename": "receipt.jpg"}],
        "payment_transaction_number": "900629",
        "payment_amount": Decimal("454.00"),
        "payment_paid_at": datetime(2026, 10, 16, 22, 59),
        "payment_extraction_method": "manual_corrected",
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: None)
    monkeypatch.setattr(repositories, "list_orders", lambda **kwargs: [order])
    monkeypatch.setattr(repositories, "count_orders", lambda **kwargs: 1)
    monkeypatch.setattr(repositories, "reseller_cart_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/reseller/history?view=pending")

    assert response.status_code == 200
    assert "receipt.jpg" in response.text
    assert 'class="proof-upload-form"' not in response.text
    assert "Send proof" not in response.text

    order["payment_proofs"] = []
    response = TestClient(main.app).get("/portal/reseller/history?view=pending")

    assert response.status_code == 200
    assert 'class="proof-upload-form"' in response.text
    assert "Send proof" in response.text


def test_reseller_checkout_page_renders_cart_manifest(monkeypatch):
    item = {
        "product_id": 17,
        "name": "Premium Tocino",
        "category": "Pork",
        "unit": "pack",
        "base_price": Decimal("180.00"),
        "available": Decimal("24"),
        "cart_quantity": Decimal("3"),
        "line_total": Decimal("540.00"),
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(repositories, "reseller_cart_count", lambda account_id: 3)
    monkeypatch.setattr(repositories, "list_reseller_cart_items", lambda account_id: [item])
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/reseller/checkout?product_id=17")

    assert response.status_code == 200
    assert "Confirm your order" in response.text
    assert "Premium Tocino" in response.text
    assert 'action="/portal/reseller/checkout"' in response.text
    assert "15-minute payment window" in response.text
    assert "data-receipt-modal" not in response.text
    assert 'name="payment_amount"' not in response.text
    assert "Place order and reserve stock" in response.text


def test_reseller_checkout_post_uses_dedicated_route(monkeypatch):
    submitted = []

    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "checkout_reseller_cart",
        lambda account_id, notes, product_ids: submitted.append(
            (account_id, notes, product_ids)
        ) or {"order_id": 73},
    )

    response = TestClient(main.app).post(
        "/portal/reseller/checkout",
        data={
            "notes": "  Deliver Friday  ",
            "product_id": "17",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/reseller/checkout?placed=73"
    assert submitted == [(42, "Deliver Friday", [17])]


def test_reseller_checkout_requires_an_explicit_selection(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "checkout_reseller_cart",
        lambda *args, **kwargs: pytest.fail("Checkout must not run without selected products."),
    )

    response = TestClient(main.app).post("/portal/reseller/checkout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/portal/reseller/cart?error=")


def test_reseller_checkout_starts_payment_window_without_proof(monkeypatch):
    submitted = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "checkout_reseller_cart",
        lambda *args, **kwargs: submitted.append((args, kwargs)) or {"order_id": 81},
    )

    response = TestClient(main.app).post(
        "/portal/reseller/checkout",
        data={"product_id": "17"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/reseller/checkout?placed=81"
    assert submitted == [((42, "", [17]), {})]


def test_receipt_scan_endpoint_returns_signed_suggestions(monkeypatch):
    attachments = [{"checksum_sha256": "a" * 64, "filename": "receipt.png"}]

    async def parse_uploads(_uploads):
        return attachments

    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(shared_portal, "parse_payment_proof_attachments", parse_uploads)
    monkeypatch.setattr(
        reseller_portal.receipt_ocr,
        "scan_payment_proofs",
        lambda _attachments: {
            "transaction_number": "ABC123456",
            "payment_date": "2026-10-15",
            "payment_time": "10:30",
            "payment_amount": "500.00",
            "confidence": 0.9,
            "engine": "tesseract-test",
            "warnings": [],
        },
    )

    response = TestClient(main.app).post(
        "/portal/reseller/checkout/receipt-scan",
        files={"payment_proofs": ("receipt.png", b"image", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["transaction_number"] == "ABC123456"
    assert response.json()["payment_amount"] == "500.00"
    assert response.json()["scan_token"]


def test_reseller_payment_submission_moves_timed_order_to_pending(monkeypatch):
    attachments = [{"checksum_sha256": "a" * 64, "filename": "receipt.png"}]
    payment = {"transaction_number": "TXN-123456", "payment_amount": Decimal("500.00")}
    submitted = []

    async def parse_uploads(_uploads):
        return attachments

    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(shared_portal, "parse_payment_proof_attachments", parse_uploads)
    monkeypatch.setattr(reseller_portal.receipt_ocr, "confirmed_payment_details", lambda *args: payment)
    monkeypatch.setattr(
        repositories,
        "submit_order_payment",
        lambda account_id, order_id, proofs, details: submitted.append(
            (account_id, order_id, proofs, details)
        ) or {"order_id": order_id, "status": "pending"},
    )

    response = TestClient(main.app).post(
        "/portal/reseller/orders/91/payment",
        data={
            "receipt_scan_token": "signed",
            "transaction_number": "TXN-123456",
            "payment_date": "2026-10-15",
            "payment_time": "10:30",
            "payment_amount": "500.00",
            "payment_details_confirmed": "yes",
        },
        files={"payment_proofs": ("receipt.png", b"image", "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/portal/reseller/history?view=pending")
    assert submitted == [(42, 91, attachments, payment)]


def test_reseller_checkout_only_renders_selected_cart_items(monkeypatch):
    items = [
        {
            "product_id": 17,
            "name": "Premium Tocino",
            "category": "Pork",
            "unit": "pack",
            "base_price": Decimal("180.00"),
            "available": Decimal("24"),
            "cart_quantity": Decimal("3"),
            "line_total": Decimal("540.00"),
        },
        {
            "product_id": 18,
            "name": "Garlic Longganisa",
            "category": "Pork",
            "unit": "pack",
            "base_price": Decimal("160.00"),
            "available": Decimal("12"),
            "cart_quantity": Decimal("2"),
            "line_total": Decimal("320.00"),
        },
    ]
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(repositories, "reseller_cart_count", lambda account_id: 5)
    monkeypatch.setattr(repositories, "list_reseller_cart_items", lambda account_id: items)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/reseller/checkout?product_id=18")

    assert response.status_code == 200
    assert "Garlic Longganisa" in response.text
    assert "Premium Tocino" not in response.text
    assert "PHP 320.00" in response.text


def test_reseller_selected_checkout_redirects_to_order_confirmation(monkeypatch):
    submitted = []

    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(
        repositories,
        "checkout_reseller_cart",
        lambda account_id, notes, product_ids: submitted.append(
            (account_id, notes, product_ids)
        ) or {"order_id": 91},
    )

    response = TestClient(main.app).post(
        "/portal/reseller/checkout",
        data={
            "notes": "Pickup Friday",
            "product_id": ["17", "18"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal/reseller/checkout?placed=91"
    assert submitted == [(42, "Pickup Friday", [17, 18])]


def test_reseller_checkout_success_prompt_links_to_owned_order(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)
    monkeypatch.setattr(repositories, "reseller_cart_count", lambda account_id: 0)
    monkeypatch.setattr(repositories, "list_reseller_cart_items", lambda account_id: [])
    monkeypatch.setattr(
        repositories,
        "reseller_order_confirmation",
        lambda account_id, order_id: {
            "order_id": order_id,
            "status": "to_pay",
            "total_amount": 215.0,
            "payment_due_at": datetime.now(timezone.utc) + timedelta(minutes=15),
        },
    )
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)

    response = TestClient(main.app).get("/portal/reseller/checkout?placed=91")

    assert response.status_code == 200
    assert "Your items are reserved." in response.text
    assert "15:00" in response.text
    assert 'href="/portal/reseller/history?view=to_pay#order-91"' in response.text


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
    assert "20261016-product-preview-1" in response.text
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


def test_team_leader_reports_render_business_date_products_pagination_and_mobile_cards(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 5)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(team_leader_portal, "business_today", lambda: date(2026, 10, 17))
    monkeypatch.setattr(repositories, "count_sales_reports", lambda *args, **kwargs: 11)
    monkeypatch.setattr(
        repositories,
        "list_sales_reports",
        lambda *args, **kwargs: [
            {
                "sales_report_id": 1,
                "report_source": "team_leader",
                "submitted_by": "Sales Leader B",
                "period_start": date(2026, 10, 17),
                "period_end": date(2026, 10, 17),
                "total_sales": 910,
                "total_orders": 1,
                "notes": "Daily report",
                "items": [],
            }
        ],
    )
    monkeypatch.setattr(repositories, "team_sales_report_entries", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repositories,
        "team_rejected_order_entries",
        lambda *args, **kwargs: [
            {
                "order_id": 28,
                "reseller": "Xort's store",
                "items": "1 pack Bacon, 3 pack Chicken Tocino",
                "rejected_at": datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc),
                "total_amount": 1133,
                "notes": "",
            }
        ],
    )

    response = client.get("/portal/team-leader/reports")

    assert response.status_code == 200
    assert 'data-business-date="2026-10-17"' in response.text
    assert response.text.count('value="2026-10-17"') == 2
    assert "Assigned reseller purchase summary" not in response.text
    assert 'class="rejected-order-card"' in response.text
    assert "1 pack Bacon" in response.text
    assert "3 pack Chicken Tocino" in response.text
    assert "No notes were provided." in response.text
    assert "built-in method items" not in response.text
    assert "PHP 910.00 - 1 order" in response.text
    assert "Page 1 of 2" in response.text
    assert "page=2" in response.text
    assert response.text.count("responsive-card-table") == 1
    assert "compact-table responsive-card-table report-items-table" in response.text


def test_team_leader_reports_render_search_empty_state(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 5)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_sales_reports", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "list_sales_reports", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "team_sales_report_entries", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "team_rejected_order_entries", lambda *args, **kwargs: [])

    response = client.get("/portal/team-leader/reports?q=missing")

    assert response.status_code == 200
    assert "No team reports found" in response.text
    assert "Try a different name, report number, or note." in response.text
    assert "No rejected reseller orders" in response.text
    assert 'role="status"' in response.text


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


def test_portal_shell_does_not_repeat_the_active_sidebar_page_title():
    template = open("app/templates/portals/base.html", encoding="utf-8").read()
    css = open("app/static/css/portal_base.css", encoding="utf-8").read()

    assert "portal-title-copy" not in template
    assert "{{ role.label }} workspace" not in template
    assert '<h1 class="sr-only">{{ section_title }}</h1>' in template
    assert "min-height: 44px" in css
    assert "justify-content: flex-end" in css


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
    assert "sales-report-rejections" in templates["reports"]
    assert "rejected-order-card" in templates["reports"]
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
    assert "inquiries" not in sales_nav
    assert "inquiries" in supervisor_nav
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


def test_regular_sales_leader_sees_read_only_team_reseller_roster(monkeypatch):
    reseller = {
        "reseller_id": 3,
        "business_name": "Lipa Fresh Mart",
        "contact_person": "Carlo Mendoza",
        "email": "carlo@example.test",
        "contact_number": "09171234567",
        "address": "Lipa City",
        "reseller_status": "active",
        "account_status": "active",
        "total_orders": 4,
        "last_order_at": None,
    }
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales")
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 17)
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "list_team_resellers", lambda **kwargs: [reseller])
    monkeypatch.setattr(repositories, "count_team_resellers", lambda **kwargs: 1)

    response = TestClient(main.app).get("/portal/team-leader/resellers")

    assert response.status_code == 200
    assert "My Resellers" in response.text
    assert "Lipa Fresh Mart" in response.text
    assert "Carlo Mendoza" in response.text
    assert "4 orders" in response.text
    reseller_card = response.text.split("team-reseller-card", 1)[1].split("</article>", 1)[0]
    assert "<form" not in reseller_card
    assert 'data-lucide="user-round"' in reseller_card


def test_inventory_leader_cannot_open_sales_reseller_roster(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")

    response = TestClient(main.app).get("/portal/team-leader/resellers")

    assert response.status_code == 404


def test_regular_sales_leader_cannot_open_supervisor_inquiries(monkeypatch):
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "sales")

    response = TestClient(main.app).get("/portal/team-leader/inquiries")

    assert response.status_code == 404


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
    css = open("app/static/css/portals/owner.css", encoding="utf-8").read()

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
    portal_js = open("app/static/js/portal.js", encoding="utf-8").read()

    assert "grid-template-columns: 38px minmax(0, 1fr) 8px" in css
    assert "notification-panel-enter" in css
    assert ".notification-item.warning" in css
    assert ".notification-item.critical" in css
    assert ".notification-unread-dot" in css
    assert ".notification-item.is-read .notification-item-copy" in css
    assert "notification-item-icon" in template
    assert "notification-item-meta" in template
    assert "Mark all read" in template
    assert "data-notification-menu" in template
    assert 'aria-expanded="false"' in template
    assert "notification-trigger-icon" in template
    assert ".notification-menu summary::marker" in css
    assert "flex: 0 0 42px" in css
    assert "@media (min-width: 861px)" in css
    assert "body.portal-body .portal-main > .portal-topbar" in css
    assert "position: fixed" in css
    assert "inset: 94px auto auto 22px" in css
    assert "left: calc(100% + 14px)" in css
    assert "notification-trigger-label" in template
    assert "View all notifications" in template
    assert ".portal-body .notification-center-hero" in css
    assert ".portal-body .notification-center-toolbar" in css
    assert "function bindNotificationMenu()" in portal_js
    assert "function positionPanel()" in portal_js
    assert "if (list) list.scrollTop = 0" in portal_js
    assert 'window.matchMedia("(min-width: 861px)").matches' in portal_js
    assert ".notification-menu.is-positioning .notification-panel" in css
    assert 'event.key === "Escape" && menu.open' in portal_js
    assert "!menu.contains(event.target)" in portal_js


@pytest.mark.parametrize("role_key", ["owner", "team-leader", "sales-supervisor", "reseller"])
def test_notification_center_is_available_to_every_portal_role(monkeypatch, role_key):
    calls = install_read_spies(monkeypatch)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_account_id", lambda request: 42)

    response = TestClient(main.app).get(
        f"/portal/{role_key}/notifications?status=unread&type=order&page=2"
    )

    assert response.status_code == 200
    assert "Activity center" in response.text
    assert "No notifications found" in response.text
    assert f'action="/portal/{role_key}/notifications"' in response.text
    assert calls["list_notification_history"] == [(
        (42,),
        {"status": "unread", "category": "order", "page": 2, "page_size": 15},
    )]
    assert calls["count_notification_history"] == [(
        (42,),
        {"status": "unread", "category": "order"},
    )]


def test_notification_targets_are_normalized_for_the_signed_in_portal():
    assert shared_portal.notification_target_for(
        "sales-supervisor",
        "sales-supervisor",
        "/portal/team-leader/orders",
    ) == "/portal/sales-supervisor/orders"
    assert shared_portal.notification_target_for(
        "team-leader",
        "sales",
        "/portal/sales-supervisor/inquiries",
    ) == "/portal/team-leader/notifications"
    assert shared_portal.notification_target_for(
        "team-leader",
        "inventory",
        "/portal/team-leader/inventory?type=raw#stock",
    ) == "/portal/team-leader/inventory?type=raw#stock"
    assert shared_portal.notification_target_for(
        "owner",
        None,
        "/portal/owner/forecasts?run_id=4",
    ) == "/portal/owner/forecasts?run_id=4"
    assert shared_portal.notification_target_for(
        "reseller",
        None,
        "https://example.test/phishing",
    ) == "/portal/reseller/notifications"


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
    assert "Raw materials" in response.text
    assert "material-row" in response.text
    assert "Beef" in response.text
    assert "Finished products inventory" not in response.text
    assert "data-inventory-products-region" not in response.text
    assert "<table" not in response.text


def test_raw_materials_page_renders_compact_paginated_stock_ledger(monkeypatch):
    client = TestClient(main.app)
    inventory_calls = []
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_inventory_items", lambda *args, **kwargs: 23)

    def list_inventory_items(**kwargs):
        inventory_calls.append(kwargs)
        return [
            {
                "item_id": 1,
                "item_type": "raw_material",
                "item_type_label": "Raw material",
                "category": "meat",
                "name": "Pork shoulder",
                "unit": "kg",
                "base_price": Decimal("0"),
                "available": Decimal("24"),
                "is_active": True,
            },
            {
                "item_id": 2,
                "item_type": "raw_material",
                "item_type_label": "Raw material",
                "category": "raw_material",
                "name": "Curing salt",
                "unit": "kg",
                "base_price": Decimal("0"),
                "available": Decimal("0"),
                "is_active": True,
            },
        ]

    monkeypatch.setattr(repositories, "list_inventory_items", list_inventory_items)

    response = client.get("/portal/team-leader/raw-materials?type=meat&page=2")

    assert response.status_code == 200
    assert inventory_calls == [
        {"q": "", "category": "raw_material:meat", "page": 2, "page_size": 10, "sort": ""}
    ]
    assert 'aria-label="Raw material categories"' in response.text
    assert 'aria-label="Page 2"' in response.text
    assert 'aria-current="page"' in response.text
    assert "Page 2 of 3" in response.text
    assert "Out of stock" in response.text
    assert "--stock-fill: 100%" in response.text
    assert "type=meat" in response.text
    assert "<table" not in response.text


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


def test_finished_products_page_separates_packages_from_individual_products(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(shared_portal, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(shared_portal, "session_team_leader_role", lambda request: "inventory")
    monkeypatch.setattr(repositories, "list_notifications", lambda *args, **kwargs: [])
    monkeypatch.setattr(repositories, "unread_notification_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repositories, "count_inventory_items", lambda *args, **kwargs: 2)
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
            },
            {
                "item_id": 3,
                "item_type": "finished_product",
                "item_type_label": "Finished product",
                "category": "Package",
                "name": "Resellers Package",
                "unit": "set",
                "base_price": 2500,
                "available": 12,
                "is_active": True,
            },
        ],
    )

    response = client.get("/portal/team-leader/finished-products")

    assert response.status_code == 200
    assert response.text.index('id="standard-products-title"') < response.text.index('id="package-products-title"')
    assert "inventory-product-card--package" in response.text
    assert "Resellers Package" in response.text
    assert "type=Package" in response.text


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

