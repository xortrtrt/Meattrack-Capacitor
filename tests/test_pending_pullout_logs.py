"""Expiry queue events appear once in Inventory Logs without moving stock twice."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, worker
from app.database import fetch_all, fetch_one
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


def test_finished_products_excludes_pending_stock_without_double_deduction(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    sign_in(monkeypatch, d["inventory"], "team-leader", "inventory")
    with TestClient(main.app) as client:
        for stage in ("expired", "pending", "pulled_out"):
            if stage == "pending":
                worker.scan_batch_expiry()
                assert len(returns.expired_stock_workspace()["pullouts"]) == 1
            elif stage == "pulled_out":
                returns.confirm_expired_pullout(d["batches"][0], d["inventory"], 10,
                                               "Expired holding", "All expired packs physically removed", str(uuid4()))
            response = client.get("/portal/team-leader/finished-products")
            assert response.status_code == 200
            product = next(row for row in response.context["finished_products"] if row["item_id"] == d["product"])
            assert product["available"] == 20, stage


def test_pending_pullout_event_retains_original_quantity_time_and_product_balance(lifecycle):  # noqa: F811
    d = lifecycle
    stock_before = fetch_all("SELECT batch_id,quantity_available FROM inventory_batches ORDER BY batch_id")
    movements_before = fetch_one("SELECT COUNT(*) n FROM inventory_movements")["n"]
    logs_before = repositories.count_inventory_movements()
    for _ in range(2):
        worker.scan_batch_expiry()

    def pending():
        rows = repositories.list_inventory_movements(q="Pending pull-out", page=1, page_size=1)
        assert len(rows) == repositories.count_inventory_movements(q="Pending pull-out") == 1
        row = rows[0]
        assert row["action"] == "expired_pending_pullout" and row["actor_name"] == "MEATTRACK"
        assert row["entity"] == "Test Product" and row["batch_code"] == "TEST-1"
        assert row["status_quantity"] == 10 and row["quantity_delta"] == 0
        assert row["balance_before"] == row["balance_after"] == 30
        assert row["created_at"] == fetch_one("SELECT created_at FROM expired_pullouts")["created_at"]
        return row

    original = pending()
    assert repositories.count_inventory_movements() == logs_before + 1
    assert fetch_one("SELECT COUNT(*) n FROM inventory_movements")["n"] == movements_before
    assert fetch_all("SELECT batch_id,quantity_available FROM inventory_batches ORDER BY batch_id") == stock_before
    assert repositories.product_by_id(d["product"])["available"] == 20
    assert repositories.list_inventory_movements(q="TEST-1")[0]["action"] == "expired_pending_pullout"
    assert repositories.list_inventory_movements(category="sales")[0]["balance_after"] == 30

    for quantity in (4, 6):
        returns.confirm_expired_pullout(d["batches"][0], d["inventory"], quantity,
                                       "Expired holding", "Expired packs physically segregated", str(uuid4()))
        worker.scan_batch_expiry()
        assert pending() == original
    assert not returns.expired_stock_workspace()["pullouts"]
    assert repositories.list_inventory_movements()[0]["balance_after"] == 20
    assert len([row for row in repositories.list_inventory_movements() if row["action"] == "expired_pullout"]) == 2
    holding = fetch_one("SELECT holding_id FROM expired_holdings ORDER BY holding_id LIMIT 1")["holding_id"]
    returns.dispose_expired_stock(holding, d["inventory"], 2, None, "Physically disposed with witness", str(uuid4()))
    worker.scan_batch_expiry()
    assert pending() == original


def test_inventory_log_labels_expiry_as_status_change(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    worker.scan_batch_expiry()
    sign_in(monkeypatch, d["inventory"], "team-leader", "inventory")
    with TestClient(main.app) as client:
        response = client.get("/portal/team-leader/logs", params={"q": "Pending pull-out"})
    assert response.status_code == 200
    assert response.text.count('class="movement-row movement-row--neutral"') == 1
    assert "Moved to pending pull-out" in response.text
    assert "Unavailable for sale · Batch TEST-1" in response.text
    assert '<span class="movement-direction">Flagged</span>' in response.text
    assert "Total stock after" in response.text
    assert "Stock out</span>" not in response.text and "Stock in</span>" not in response.text
