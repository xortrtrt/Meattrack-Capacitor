from __future__ import annotations

from decimal import Decimal

from app import repositories


def test_current_metrics_uses_one_database_round_trip(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)

    def fake_fetch_one(query, params=None):
        calls.append((query, params))
        return {
            "fulfilled_sales": Decimal("1250.50"),
            "pending_reseller_orders": 2,
            "open_alerts": 3,
            "active_resellers": 4,
            "total_available": Decimal("80.25"),
        }

    monkeypatch.setattr(repositories, "fetch_one", fake_fetch_one)

    assert repositories.current_metrics() == {
        "fulfilled_sales": 1250.5,
        "pending_reseller_orders": 2,
        "open_alerts": 3,
        "active_resellers": 4,
        "total_available": 80.25,
    }
    assert len(calls) == 1
    assert "AS fulfilled_sales" in calls[0][0]
    assert "AS total_available" in calls[0][0]


def test_order_type_filters_headers_and_items(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)

    def fake_fetch_all(query, params=None):
        calls.append((query, params))
        if "FROM orders o" in query:
            return [
                {
                    "order_id": 7,
                    "order_type": "reseller",
                    "reseller_id": 1,
                    "reseller": "Test reseller",
                    "status": "pending",
                    "order_date": None,
                    "total_amount": Decimal("100"),
                    "notes": "",
                }
            ]
        if "FROM order_payment_proofs" in query:
            return [
                {
                    "order_payment_proof_id": 3,
                    "order_id": 7,
                    "filename": "proof.png",
                    "content_type": "image/png",
                    "size_bytes": 10,
                    "checksum_sha256": "a" * 64,
                    "uploaded_at": None,
                }
            ]
        return [
            {
                "order_id": 7,
                "product_id": 2,
                "name": "Test product",
                "quantity": Decimal("1"),
                "unit_price": Decimal("100"),
                "line_total": Decimal("100"),
            }
        ]

    monkeypatch.setattr(repositories, "fetch_all", fake_fetch_all)
    orders = repositories.list_orders(order_type="reseller")

    assert len(calls) == 3
    assert "o.order_type = %s" in calls[0][0]
    assert calls[0][1] == ("reseller",)
    assert "oi.order_id = ANY(%s)" in calls[1][0]
    assert calls[1][1] == ([7],)
    assert "FROM order_payment_proofs" in calls[2][0]
    assert calls[2][1] == ([7],)
    assert orders[0]["items"][0]["product_id"] == 2
    assert orders[0]["payment_proof_count"] == 1
    assert orders[0]["payment_proofs"][0]["filename"] == "proof.png"


def test_report_source_and_limit_are_parameterized(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    repositories.list_sales_reports(report_source="reseller", limit=1)

    assert "sr.report_source = %s" in calls[0][0]
    assert "LIMIT %s" in calls[0][0]
    assert calls[0][1] == ("reseller", 1)


def test_team_leader_order_scope_is_parameterized(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"total": 0},
    )

    repositories.list_orders(order_type="reseller", team_leader_account_id=42)
    repositories.count_orders(order_type="reseller", team_leader_account_id=42)

    assert "r.team_leader_account_id = %s" in calls[0][0]
    assert calls[0][1] == ("reseller", 42)


def test_reseller_report_scope_is_parameterized(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    repositories.list_sales_reports(report_source="reseller", reseller_account_id=7)

    assert "sr.reseller_id = (" in calls[0][0]
    assert calls[0][1] == ("reseller", 7)


def test_team_reseller_purchase_summary_uses_real_order_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    repositories.team_reseller_purchase_summary(team_leader_account_id=42)

    assert "o.status IN ('approved', 'fulfilled')" in calls[0][0]
    assert "r.team_leader_account_id = %s" in calls[0][0]
    assert calls[0][1] == (42,)


def test_inventory_activity_logs_are_scoped_to_inventory_actions(monkeypatch):
    fetch_all_calls = []
    fetch_one_calls = []
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: fetch_all_calls.append((query, params)) or [],
    )
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: fetch_one_calls.append((query, params)) or {"total": 0},
    )

    repositories.list_activity_logs(q="batch", page=2, page_size=10, inventory_only=True)
    repositories.count_activity_logs(q="batch", inventory_only=True)

    assert "al.action = ANY(%s)" in fetch_all_calls[0][0]
    assert "al.action = ANY(%s)" in fetch_one_calls[0][0]
    assert "login" not in fetch_all_calls[0][1][0]
    assert "created_reseller_order" not in fetch_all_calls[0][1][0]
    assert "produced_product_batch" in fetch_all_calls[0][1][0]
    assert fetch_all_calls[0][1][1:4] == ("%batch%", "%batch%", "%batch%")
    assert fetch_one_calls[0][1][1:4] == ("%batch%", "%batch%", "%batch%")


def test_inventory_item_and_batch_filters_are_parameterized(monkeypatch):
    fetch_all_calls = []
    fetch_one_calls = []
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: fetch_all_calls.append((query, params)) or [],
    )
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: fetch_one_calls.append((query, params)) or {"total": 0},
    )

    repositories.list_inventory_items(q="beef", category="finished_product:Beef", page=2, page_size=10)
    repositories.count_inventory_items(q="beef", category="finished_product:Beef")
    repositories.list_inventory_items(q="", category="raw_material:raw_material", page=1, page_size=10)
    repositories.list_inventory_batches(q="batch", category="Pork", page=3, page_size=10)
    repositories.count_inventory_batches(q="batch", category="Pork")

    assert "ii.item_type = %s" in fetch_all_calls[0][0]
    assert "LOWER(ii.category) = LOWER(%s)" in fetch_all_calls[0][0]
    assert fetch_all_calls[0][1] == ("%beef%", "%beef%", "%beef%", "finished_product", "Beef", 10, 10)
    assert fetch_one_calls[0][1] == ("%beef%", "%beef%", "%beef%", "finished_product", "Beef")
    assert "LOWER(ii.category) = LOWER(%s)" in fetch_all_calls[1][0]
    assert fetch_all_calls[1][1] == ("raw_material", "raw material", 10, 0)
    assert "ii.category = %s" in fetch_all_calls[2][0]
    assert fetch_all_calls[2][1] == ("%batch%", "%batch%", "%batch%", "Pork", 10, 20)
    assert fetch_one_calls[1][1] == ("%batch%", "%batch%", "%batch%", "Pork")


def test_product_recipes_can_be_scoped_to_paginated_products(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    assert repositories.list_product_recipes(product_ids=[]) == []
    repositories.list_product_recipes(product_ids=[2, 3])

    assert len(calls) == 1
    assert "pr.product_item_id = ANY(%s)" in calls[0][0]
    assert calls[0][1] == ([2, 3],)


def test_inventory_product_movement_analytics_uses_stock_in_and_fulfilled_out(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    repositories.inventory_product_movement_analytics(days=45, limit=6)

    assert len(calls) == 1
    assert "SUM(quantity_received) AS total_in" in calls[0][0]
    assert "SUM(oi.quantity) AS total_out" in calls[0][0]
    assert "o.status = 'fulfilled'" in calls[0][0]
    assert calls[0][1] == (45, 45, 6)


def test_schema_tracks_reseller_team_leader_assignment():
    schema = open("database/schema.sql", encoding="utf-8").read()

    assert "team_leader_account_id bigint REFERENCES accounts(account_id)" in schema
    assert "CREATE INDEX ix_resellers_team_leader" in schema
    assert "team_leader_role text CHECK" in schema
    assert "CREATE INDEX ix_accounts_team_leader_role" in schema
    assert "CREATE TABLE order_payment_proofs" in schema
    assert "CREATE INDEX ix_order_payment_proofs_order" in schema


def test_inquiry_and_forecast_limits_are_parameterized(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    repositories.list_inquiries(limit=4)
    repositories.list_forecasts(limit=10)

    assert calls[0][1] == (4,)
    assert calls[1][1] == (10,)
    assert all("LIMIT %s" in query for query, _ in calls)
