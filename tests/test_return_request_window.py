"""Five-day request eligibility is enforced for reads and transactional submission."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, receive, supporting_photo  # noqa: F401
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


def set_fulfilled_at(d, fulfilled_at):
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE orders SET fulfilled_at=%s WHERE order_id=%s", (fulfilled_at, d["order"]))


@pytest.mark.parametrize("resolution", ["refund", "replace"])
@pytest.mark.parametrize("offset,allowed", [
    (timedelta(days=5)-timedelta(microseconds=1), True),
    (timedelta(days=5), False),
    (timedelta(days=5, microseconds=1), False),
])
def test_same_exact_five_day_boundary_for_picker_allocations_and_submission(lifecycle, monkeypatch, resolution, offset, allowed):  # noqa: F811
    d = lifecycle
    now = business_now().replace(microsecond=0)
    monkeypatch.setattr(returns, "business_now", lambda: now)
    set_fulfilled_at(d, now-offset)
    order = returns.reseller_return_order(d["order"], d["reseller"])
    assert order["request_window_open"] is allowed
    assert order["request_deadline"] == now-offset+timedelta(days=5)
    assert bool(returns.reseller_return_orders(d["reseller"])) is allowed
    assert bool(returns.returnable_allocations(d["order"], d["reseller"])) is allowed
    stock = fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))
    args = (d["order"], d["reseller"], resolution, [(d["allocation"], 2)], "Request products within the allowed fulfillment window", [supporting_photo()])
    if allowed:
        case = returns.create_product_return(*args)
        assert returns.get_product_return(case, d["reseller"])["status"] == "submitted"
    else:
        with pytest.raises(ValueError, match="5-day.*closed"):
            returns.create_product_return(*args)
        assert fetch_one("SELECT count(*) n FROM product_returns WHERE order_id=%s", (d["order"],))["n"] == 0
        assert fetch_one("SELECT count(*) n FROM product_return_events")["n"] == 0
    assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],)) == stock
    assert fetch_one("SELECT count(*) n FROM order_refunds WHERE order_id=%s", (d["order"],))["n"] == 0


@pytest.mark.parametrize("timestamp", [None, "future"])
def test_missing_or_future_fulfillment_time_does_not_grant_a_window(lifecycle, monkeypatch, timestamp):  # noqa: F811
    d = lifecycle
    now = business_now()
    monkeypatch.setattr(returns, "business_now", lambda: now)
    set_fulfilled_at(d, now+timedelta(hours=1) if timestamp else None)
    assert not returns.reseller_return_order(d["order"], d["reseller"])["request_window_open"]
    assert returns.reseller_return_orders(d["reseller"]) == returns.returnable_allocations(d["order"], d["reseller"]) == []
    with pytest.raises(ValueError, match="fulfillment time needs verification"):
        returns.create_product_return(d["order"], d["reseller"], "refund", [(d["allocation"], 2)], "Request with unverified fulfillment time")


def test_stale_modal_cannot_bypass_deadline_but_existing_request_and_replay_continue(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    now = business_now()
    monkeypatch.setattr(returns, "business_now", lambda: now)
    set_fulfilled_at(d, now-timedelta(days=4))
    key = str(uuid4())
    args = (d["order"], d["reseller"], "refund", [(d["allocation"], 2)], "Request submitted before the window closes", [supporting_photo()])
    case = returns.create_product_return(*args, operation_key=key)
    monkeypatch.setattr(returns, "business_now", lambda: now+timedelta(days=2))
    assert returns.create_product_return(*args, operation_key=key) == case
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        page = client.get(f"/portal/reseller/history?view=returns&order={d['order']}")
        assert page.status_code == 200 and "5-day return/refund/replacement request window has closed" in page.text
        assert 'action="/portal/reseller/returns/actions/submit"' not in page.text
        rejected = client.post("/portal/reseller/returns/actions/submit", data={
            "order_id": d["order"], "allocation_id": d["allocation"], "quantity": 2,
            "resolution": "replace", "remarks": "Another request from an already open stale modal", "operation_key": str(uuid4()),
        })
        assert rejected.status_code == 200 and "5-day return/refund/replacement request window has closed" in rejected.text
        assert fetch_one("SELECT count(*) n FROM product_returns WHERE order_id=%s", (d["order"],))["n"] == 1
        tracked = client.get(f"/portal/reseller/history?view=returns&case={case}")
        assert tracked.status_code == 200 and f'data-return-dialog="{case}"' in tracked.text
        history = client.get("/portal/reseller/history?view=fulfilled")
        assert "5-day return/refund/replacement request window has closed" in history.text
        assert "Return / Refund / Replace</a>" not in history.text
    returns.review_product_return(case, d["sales"], True, "Review continues after the request deadline")
    receive(d, case, authorize=False)
    assert returns.get_product_return(case, d["sales"])["verified_at"]
