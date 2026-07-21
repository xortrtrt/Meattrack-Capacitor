from __future__ import annotations

from decimal import Decimal

import pytest

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


def test_sort_parameters_are_whitelisted(monkeypatch):
    queries = []
    monkeypatch.setattr(repositories, "ensure_system_tables", lambda: None)
    monkeypatch.setattr(repositories, "fetch_all", lambda query, params=None: queries.append(query) or [])

    repositories.list_products(sort="price_desc; DROP TABLE accounts")
    repositories.list_orders(order_type="reseller", sort="total_desc")
    repositories.list_inventory_items(sort="stock_desc")

    assert "DROP TABLE" not in "\n".join(queries)
    assert "ORDER BY p.item_id" in queries[0]
    assert "ORDER BY o.total_amount DESC, o.order_id DESC" in queries[1]
    assert "ORDER BY available DESC, ii.name ASC" in queries[2]


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
    assert "follow_up_sent_at timestamptz" in schema
    assert "CREATE TABLE account_password_otps" in schema
    assert "CREATE TABLE account_login_otps" in schema
    assert "CREATE INDEX ix_account_password_otps_pending" in schema
    assert "CREATE INDEX ix_account_login_otps_pending" in schema


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
    assert "confidence_lower" in calls[1][0]
    assert "JOIN forecast_runs" in calls[1][0]


def test_add_forecast_uses_prophet_when_history_is_sufficient(monkeypatch):
    writes = []
    monkeypatch.setattr(repositories, "add_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(repositories, "create_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"account_id": 1} if "account_type = 'owner'" in query else {"val": Decimal("100")},
    )

    def fake_fetch_all(query, params=None):
        if "FROM inventory_items" in query and "WHERE item_type = 'finished_product'" in query:
            return [{"product_id": 10, "name": "Tocino Ala Eh"}]
        if "FROM orders o" in query:
            return [
                {"product_id": 10, "sale_date": repositories.date.today() - repositories.timedelta(days=3), "quantity": Decimal("5")},
                {"product_id": 10, "sale_date": repositories.date.today() - repositories.timedelta(days=2), "quantity": Decimal("8")},
                {"product_id": 10, "sale_date": repositories.date.today() - repositories.timedelta(days=1), "quantity": Decimal("10")},
            ]
        return []

    def fake_execute_write(query, params=None, returning=False):
        writes.append((query, params, returning))
        if returning:
            return {"forecast_run_id": 99}
        return None

    monkeypatch.setattr(repositories, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(repositories, "execute_write", fake_execute_write)
    monkeypatch.setattr(
        repositories,
        "prophet_product_forecast",
        lambda history_rows, horizon: {
            "forecast_date": repositories.date.today() + repositories.timedelta(days=horizon),
            "predicted_quantity": 12,
            "confidence_lower": 9,
            "confidence_upper": 15,
            "method": "Prophet",
        },
    )

    repositories.add_forecast("Prophet demand forecast", 7)

    forecast_writes = [call for call in writes if "INSERT INTO forecast_results" in call[0]]
    assert len(forecast_writes) == 1
    assert forecast_writes[0][1][3:] == (12, 9, 15)
    assert any("Completed with: Prophet" in call[1][0] for call in writes if "UPDATE forecast_runs" in call[0])
    assert any("Philippine holidays" in call[1][0] for call in writes if "UPDATE forecast_runs" in call[0])


def test_forecast_business_events_include_paydays_and_batangas_season():
    events = repositories.forecast_business_events(
        repositories.date(2026, 7, 1),
        repositories.date(2026, 12, 31),
    )
    event_names = set(events["holiday"].tolist())

    assert "payday_window" in event_names
    assert "month_end_payday_window" in event_names
    assert "christmas_rush" in event_names
    assert "new_year_rush" in event_names
    assert "batangas_sublian_foundation_season" in event_names
    assert repositories.date(2026, 7, 23) in set(events["ds"].tolist())


def test_prophet_forecast_adds_ph_country_holidays(monkeypatch):
    captured = {}

    class FakeSeries:
        def __init__(self, value):
            self.value = value

        def date(self):
            return self.value

    class FakeForecast:
        def tail(self, *_args, **_kwargs):
            return self

        @property
        def iloc(self):
            return [self]

        def __getitem__(self, key):
            if key == "ds":
                return FakeSeries(repositories.date.today() + repositories.timedelta(days=7))
            return 10

        def get(self, _key, default=None):
            return default

    class FakeProphet:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def add_country_holidays(self, country_name):
            captured["country_name"] = country_name

        def fit(self, frame):
            captured["frame_columns"] = list(frame.columns)

        def make_future_dataframe(self, **kwargs):
            captured["future_kwargs"] = kwargs
            return object()

        def predict(self, future):
            captured["future"] = future
            return FakeForecast()

    monkeypatch.setitem(__import__("sys").modules, "prophet", type("ProphetModule", (), {"Prophet": FakeProphet}))

    result = repositories.prophet_product_forecast(
        [
            {"sale_date": repositories.date.today() - repositories.timedelta(days=3), "quantity": repositories.Decimal("5")},
            {"sale_date": repositories.date.today() - repositories.timedelta(days=2), "quantity": repositories.Decimal("8")},
            {"sale_date": repositories.date.today() - repositories.timedelta(days=1), "quantity": repositories.Decimal("10")},
        ],
        7,
    )

    assert captured["country_name"] == "PH"
    assert "holidays" in captured["kwargs"]
    assert "batangas_sublian_foundation_season" in set(captured["kwargs"]["holidays"]["holiday"])
    assert captured["future_kwargs"]["periods"] == 7
    assert result["method"] == "Prophet"


def test_add_forecast_falls_back_when_history_is_insufficient(monkeypatch):
    writes = []
    monkeypatch.setattr(repositories, "add_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(repositories, "create_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        repositories,
        "fetch_one",
        lambda query, params=None: {"account_id": 1} if "account_type = 'owner'" in query else {"val": Decimal("100")},
    )

    def fake_fetch_all(query, params=None):
        if "FROM inventory_items" in query and "WHERE item_type = 'finished_product'" in query:
            return [{"product_id": 10, "name": "Tocino Ala Eh"}]
        return []

    def fake_execute_write(query, params=None, returning=False):
        writes.append((query, params, returning))
        if returning:
            return {"forecast_run_id": 100}
        return None

    monkeypatch.setattr(repositories, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(repositories, "execute_write", fake_execute_write)
    monkeypatch.setattr(
        repositories,
        "prophet_product_forecast",
        lambda *args, **kwargs: pytest.fail("Prophet should not run without enough history"),
    )

    repositories.add_forecast("Prophet demand forecast", 7)

    forecast_writes = [call for call in writes if "INSERT INTO forecast_results" in call[0]]
    assert len(forecast_writes) == 1
    assert forecast_writes[0][1][3:] == (42.0, 35.7, 48.3)
    assert any("Baseline fallback" in call[1][0] for call in writes if "UPDATE forecast_runs" in call[0])
