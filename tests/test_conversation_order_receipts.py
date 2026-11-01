"""Receipt acknowledgements preserve fulfillment, stock, and return eligibility."""
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app import main, repositories, reseller_messaging
from app.database import fetch_one, get_transaction_cursor
from app.repositories.product_returns import return_request_window
from app.report_documents import document_context, excel_document
from test_inventory_postgres import strict_inventory_database as strict_inventory_database
from test_product_lifecycle import lifecycle as lifecycle
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


def test_order_received_is_once_only_and_keeps_the_original_return_window(lifecycle, monkeypatch):
    d = lifecycle
    before = fetch_one("SELECT * FROM orders WHERE order_id=%s", (d["order"],))
    stock = repositories.product_by_id(d["product"])["available"]
    deadline = return_request_window(before)["request_deadline"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(repositories.acknowledge_order_received, d["reseller"], d["order"]) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    assert fetch_one("SELECT COUNT(*) n FROM reseller_order_receipts")["n"] == 1
    assert fetch_one("SELECT COUNT(*) n FROM activity_logs WHERE action='acknowledged_order_received'")["n"] == 1
    after = fetch_one("SELECT * FROM orders WHERE order_id=%s", (d["order"],))
    assert before == after and return_request_window(after)["request_deadline"] == deadline
    assert repositories.product_by_id(d["product"])["available"] == stock
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        page = client.get("/portal/reseller/history?view=completed")
        assert page.status_code == 200
        assert "Completed</a>" in page.text and ">completed</span>" in page.text
        assert "Order received" in page.text and ">Return / Refund / Replace</a>" in page.text
        assert f'action="/portal/reseller/orders/{d["order"]}/received"' not in page.text
    with pytest.raises(psycopg2.errors.RaiseException, match="immutable|trace|append"):
        with get_transaction_cursor() as cur:
            cur.execute("DELETE FROM reseller_order_receipts")


def test_receipt_endpoint_is_reseller_scoped_and_rejects_unfulfilled_orders(lifecycle, monkeypatch):
    d = lifecycle
    with pytest.raises(ValueError, match="Only your completed"):
        repositories.acknowledge_order_received(d["sales"], d["order"])
    unpaid = repositories.create_order("reseller",d["product"],1,account_id=d["reseller"])
    with pytest.raises(ValueError, match="Only your completed"):
        repositories.acknowledge_order_received(d["reseller"], unpaid["order_id"])
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        response = client.post(f'/portal/reseller/orders/{d["order"]}/received',follow_redirects=False)
        assert response.status_code == 303 and "marked+received" in response.headers["location"]
    assert fetch_one("SELECT account_id FROM reseller_order_receipts")["account_id"] == d["reseller"]


def test_participant_photos_and_report_sections_follow_the_requested_order(lifecycle):
    from app.business_time import business_today
    from app.repositories.activity_reports import build_report
    from io import BytesIO
    from openpyxl import load_workbook

    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE accounts SET profile_image_path='uploads/profiles/test.png',profile_image_position_x=35 WHERE account_id IN (%s,%s)",(d["reseller"],d["sales"]))
    participant = reseller_messaging.reseller_workspace(d["reseller"])["participant"]
    assert participant["team_leader_profile_image_path"] == "uploads/profiles/test.png"
    assert participant["reseller_profile_image_position_x"] == 35
    assert reseller_messaging.leader_conversations(d["sales"])[0]["reseller_profile_image_path"] == "uploads/profiles/test.png"
    payload = build_report(d["sales"],business_today(),business_today())
    assert [table["key"] for table in document_context(payload)["tables"]] == ["summary","orders","sales","returns","refunds"]
    assert load_workbook(BytesIO(excel_document(payload))).sheetnames == ["Summary","Orders","Sales","Returns","Refunds"]
