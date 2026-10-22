from io import BytesIO
from pathlib import Path

import psycopg2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, receipt_ocr, repositories
from app.portals import shared
from test_inventory_postgres import (
    _payment_submission,
    _seed_fulfillment,
    strict_inventory_database as strict_inventory_database,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("upgrade", [False, True])
def test_cancelling_unpaid_order_commits_and_releases_reserved_stock(strict_inventory_database, monkeypatch, upgrade):
    dsn = strict_inventory_database
    _, product, _, _ = _seed_fulfillment(dsn, batch_quantities=[5], order_quantities=[])
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local'")
            account = cur.fetchone()[0]
            if upgrade:
                cur.execute(Path("database/migrations/020_restrict_order_rejection_to_pending.sql").read_text())
    order = repositories.create_order_from_items("reseller", [(product, "2")], account_id=account)
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    monkeypatch.setattr(shared, "session_account_id", lambda *args: account)
    client = TestClient(main.app)
    if upgrade:
        with pytest.raises(psycopg2.errors.RaiseException, match="Invalid order status transition"):
            repositories.cancel_reseller_order(account, order["order_id"], "ordered_by_mistake")
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(Path("database/migrations/028_allow_unpaid_reseller_cancellation.sql").read_text())
    response = client.post(
        f"/portal/reseller/orders/{order['order_id']}/cancel",
        data={"cancellation_reason": "ordered_by_mistake"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert "view=cancelled" in response.headers["location"]
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, cancellation_reason, cancelled_at FROM orders WHERE order_id=%s", (order["order_id"],))
            status, reason, cancelled_at = cur.fetchone()
            assert status == "cancelled" and reason and cancelled_at
            cur.execute("SELECT COUNT(*) FROM order_stock_reservations WHERE order_id=%s", (order["order_id"],))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT event_type FROM order_system_messages WHERE order_id=%s", (order["order_id"],))
            assert cur.fetchone()[0] == "cancelled"
    replacement = repositories.create_order_from_items("reseller", [(product, "5")], account_id=account)
    proofs, payment = _payment_submission("paid-cancellation")
    repositories.submit_order_payment(account, replacement["order_id"], proofs, payment)
    with pytest.raises(ValueError, match="submitted payment cannot be cancelled"):
        repositories.cancel_reseller_order(account, replacement["order_id"], "ordered_by_mistake")
    with pytest.raises(psycopg2.errors.RaiseException, match="submitted payment cannot be cancelled"):
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE orders SET status='cancelled', cancellation_reason='Ordered by mistake',
                               cancelled_at=now() WHERE order_id=%s""", (replacement["order_id"],))


def test_history_payment_upload_counts_only_after_commit_and_reports_duplicate_errors(strict_inventory_database, monkeypatch):
    dsn = strict_inventory_database
    _, product, _, _ = _seed_fulfillment(dsn, batch_quantities=[10], order_quantities=[])
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id FROM accounts WHERE email = 'account@test.local'")
            account = cur.fetchone()[0]
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    monkeypatch.setattr(shared, "session_account_id", lambda *args: account)
    monkeypatch.setattr(receipt_ocr, "scan_payment_proofs", lambda *args: {
        "transaction_number": "UNIQUE123456", "payment_amount": "200.00",
        "payment_date": "2026-10-15", "payment_time": "10:30", "confidence": 0.9,
    })
    client = TestClient(main.app)

    def submit(order_id, color, reference):
        content = BytesIO()
        Image.new("RGB", (8, 8), color).save(content, format="PNG")
        files = {"payment_proofs": ("receipt.png", content.getvalue(), "image/png")}
        scan = client.post("/portal/reseller/orders/receipt-scan", files=files)
        assert scan.status_code == 200
        return client.post(
            f"/portal/reseller/orders/{order_id}/payment", files=files,
            headers={"accept": "application/json"},
            data={
                "receipt_scan_token": scan.json()["scan_token"], "transaction_number": reference,
                "payment_amount": "200.00", "payment_date": "2026-10-15", "payment_time": "10:30",
                "payment_details_confirmed": "yes",
            },
        )

    first = repositories.create_order_from_items("reseller", [(product, "2")], account_id=account)
    response = submit(first["order_id"], "red", "UNIQUE123456")
    assert response.status_code == 200 and response.json()["ok"]
    assert "view=pending" in response.json()["redirect_url"]
    second = repositories.create_order_from_items("reseller", [(product, "2")], account_id=account)
    for color, reference, error in [
        ("blue", "UNIQUE123456", "transaction number has already been used"),
        ("red", "DIFFERENT123456", "screenshot has already been used"),
    ]:
        response = submit(second["order_id"], color, reference)
        assert response.status_code == 400
        assert not response.json()["ok"] and error in response.json()["error"]
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT status, (SELECT COUNT(*) FROM order_payment_proofs WHERE order_id=o.order_id)
                               FROM orders o WHERE order_id=%s""", (second["order_id"],))
                assert cur.fetchone() == ("to_pay", 0)
    response = submit(second["order_id"], "blue", "DIFFERENT123456")
    assert response.status_code == 200 and response.json()["ok"]
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT status, payment_amount,
                                  (SELECT COUNT(*) FROM order_payment_proofs WHERE order_id=o.order_id)
                           FROM orders o ORDER BY order_id""")
            assert cur.fetchall() == [("pending", 200, 1), ("pending", 200, 1)]
