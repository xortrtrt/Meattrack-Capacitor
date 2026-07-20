from __future__ import annotations

from decimal import Decimal

from app import repositories


def test_current_metrics_uses_one_database_round_trip(monkeypatch):
    calls = []

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

    assert len(calls) == 2
    assert "o.order_type = %s" in calls[0][0]
    assert calls[0][1] == ("reseller",)
    assert "oi.order_id = ANY(%s)" in calls[1][0]
    assert calls[1][1] == ([7],)
    assert orders[0]["items"][0]["product_id"] == 2


def test_report_source_and_limit_are_parameterized(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repositories,
        "fetch_all",
        lambda query, params=None: calls.append((query, params)) or [],
    )

    repositories.list_sales_reports(report_source="reseller", limit=1)

    assert "sr.report_source = %s" in calls[0][0]
    assert "LIMIT %s" in calls[0][0]
    assert calls[0][1] == ("reseller", 1)


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
