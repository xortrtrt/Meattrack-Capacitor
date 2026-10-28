"""One return record links customer requests, non-sellable custody, and existing refunds."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions
from app.business_time import business_now, business_today
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, receive, complete_refund, substitute  # noqa: F401

pytestmark = pytest.mark.postgres


def approved(d, resolution="refund", replacements=None):
    case = create_case(d, resolution)
    returns.review_product_return(case, d["sales"], True, "Approved after original delivery review", replacements)
    return case


def confirm(d, case, key=None):
    returns.authorize_return_settlement(case, d["sales"], "Sales confirms the approved resolution after complete receipt", key or str(uuid4()))


def test_complete_receipts_notify_sales_once_and_unlock_existing_refund_only_after_confirmation(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    before = fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))
    receive(d, case, 1, verify=False)
    with pytest.raises(ValueError, match="Inventory must verify"):
        confirm(d, case)
    with pytest.raises(ValueError, match="Receive all"):
        returns.verify_product_return(case, d["inventory"], "Incomplete receipt must not pass", str(uuid4()))
    assert fetch_one("SELECT count(*) n FROM order_refunds WHERE return_id=%s", (case,))["n"] == 0
    receive(d, case, 1, verify=False)
    key = str(uuid4())
    for _ in range(2):
        returns.verify_product_return(case, d["inventory"], "Both approved packs received and segregated", key)
    notices = fetch_all("""SELECT n.* FROM notifications n JOIN notification_recipients nr USING(notification_id)
        WHERE n.source_type='product_returns' AND n.source_id=%s AND nr.account_id=%s
        AND n.title='Returned goods complete—Sales confirmation required'""", (case, d["sales"]))
    assert len(notices) == 1 and f"case={case}" in notices[0]["target_url"]
    sales = returns.get_product_return(case, d["sales"])
    refund = sales["refund"]["order_refund_id"]
    assert sales["sales_stage"] == "ready_for_confirmation" and not sales["sales_authorized"]
    assert returns.return_settlement_totals(d["sales"])["pending_refunds"] == 200
    with pytest.raises(ValueError, match="Sales confirmation"):
        repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    key = str(uuid4())
    for _ in range(2):
        confirm(d, case, key)
    assert fetch_one("SELECT count(*) n FROM product_return_events WHERE return_id=%s AND action='settlement_authorized'", (case,))["n"] == 1
    complete_refund(d, case)
    assert fetch_one("SELECT count(*) n FROM order_refunds WHERE return_id=%s", (case,))["n"] == 1
    assert fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],)) == before
    assert fetch_one("SELECT SUM(quantity_remaining) n FROM expired_holdings WHERE source='return'")["n"] == 2
    assert returns.get_product_return(case, d["sales"])["status"] == "completed"


def test_replacement_uses_multiple_sellable_batches_and_persists_exact_movements(lifecycle):  # noqa: F811
    from datetime import timedelta
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO inventory_items(item_type,category,name,unit,base_price,pack_size,pack_size_unit,pack_content_status)
            VALUES('finished_product','Pork','Split batch replacement','pack',100,500,'g','declared') RETURNING item_id""")
        product = cur.fetchone()["item_id"]
    repositories.add_product_batch(product, "REPLACEMENT-A", 1, business_today() + timedelta(days=2))
    repositories.add_product_batch(product, "REPLACEMENT-B", 3, business_today() + timedelta(days=3))
    case = approved(d, "replace", [(product, 2)])
    receive(d, case, authorize=False)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    with pytest.raises(ValueError, match="Sales confirmation"):
        returns.reserve_replacement(case, d["inventory"], "Reservation blocked before Sales handoff")
    confirm(d, case)
    returns.reserve_replacement(case, d["inventory"], "Reserve approved sellable replacement batches")
    reserved = returns.get_product_return(case, d["inventory"])["reserved_batches"]
    assert [(r["batch_code"], r["quantity"]) for r in reserved] == [("REPLACEMENT-A", 1), ("REPLACEMENT-B", 1)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: returns.release_replacement(case, d["inventory"], "Released exact reserved batches"), range(2)))
    sales = returns.get_product_return(case, d["sales"])
    assert not sales["reserved_batches"] and len(sales["released_batches"]) == 2
    assert all(row["movement_id"] and row["released_by_name"] for row in sales["released_batches"])
    assert [(r["batch_code"], r["quantity"]) for r in sales["released_batches"]] == [("REPLACEMENT-A", 1), ("REPLACEMENT-B", 1)]
    assert fetch_one("SELECT quantity_available n FROM inventory_batches WHERE item_id=%s AND batch_code='REPLACEMENT-A'", (product,))["n"] == 0
    assert fetch_one("SELECT quantity_available n FROM inventory_batches WHERE item_id=%s AND batch_code='REPLACEMENT-B'", (product,))["n"] == 2
    assert fetch_one("SELECT quantity_available n FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))["n"] == 10
    assert fetch_one("SELECT SUM(quantity_remaining) n FROM expired_holdings WHERE source='return'")["n"] == 2
    assert not fetch_one("SELECT 1 FROM order_refunds WHERE return_id=%s", (case,))
    customer = returns.get_product_return(case, d["reseller"])
    assert len(customer["released_batches"]) == 2 and "reserved_batches" not in customer
    assert all("released_by_name" not in row for row in customer["released_batches"])
    assert sales["status"] == customer["status"] == "completed"


def test_quote_edit_revokes_sales_confirmation_and_old_operation_cannot_reauthorize(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d, "replace")
    receive(d, case, authorize=False)
    key = str(uuid4())
    confirm(d, case, key)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved first accepted revision")
    returns.review_product_return(case, d["sales"], True, "New selection requires renewed Sales confirmation", [(substitute(200), 1)])
    changed = returns.get_product_return(case, d["sales"])
    assert not changed["sales_authorized"] and changed["settlement_authorized_at"] is None
    assert not changed["reserved_batches"] and changed["accepted_revision"] is None
    with pytest.raises(ValueError, match="different details"):
        confirm(d, case, key)
    with pytest.raises(ValueError, match="Sales confirmation"):
        returns.release_replacement(case, d["inventory"], "Must not release a newly revised quote")
    confirm(d, case)
    assert returns.get_product_return(case, d["sales"])["settlement_authorized_quote_revision"] == 2


def test_missing_sales_handoff_recipient_falls_back_to_owner(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case, verify=False)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE accounts SET is_active=false WHERE account_id=%s", (d["sales"],))
    returns.verify_product_return(case, d["inventory"], "All returned goods received and verified", str(uuid4()))
    owner_notice = fetch_one("""SELECT n.target_url FROM notifications n JOIN notification_recipients nr USING(notification_id)
        WHERE n.source_type='product_returns' AND n.source_id=%s AND nr.account_id=%s
        AND n.title='Returned goods complete—Sales assignment requires attention'""", (case, d["owner"]))
    assert f"case={case}" in owner_notice["target_url"]
    with pytest.raises(ValueError, match="cannot perform"):
        confirm(d, case)
    returns.authorize_return_settlement(case, d["owner"], "Owner resolves inactive Sales assignment with audited authorization", str(uuid4()))
    assert returns.get_product_return(case, d["owner"])["settlement_authorized_by_account_id"] == d["owner"]


def test_distinct_pages_and_server_permissions_link_to_existing_refund(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case, authorize=False)
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    with TestClient(main.app) as client:
        for role in ("reseller", "inventory", "sales"):
            session.update(account_id=d[role], role_key="reseller" if role == "reseller" else "team-leader",
                           team_leader_role=role, portal_expires_at=int(business_now().timestamp()) + 600)
            prefix = "reseller" if role == "reseller" else "team-leader"
            response = client.get(f"/portal/{prefix}/returns?case={case}")
            assert response.status_code == 200, response.text[:1000]
            if role == "reseller":
                assert "My Returns" in response.text and "Awaiting Sales confirmation" in response.text
                assert "Original sales" not in response.text and "Holding #" not in response.text
                assert "Sales confirmation remarks" not in response.text
            elif role == "inventory":
                assert "Batch and custody records" in response.text and "PHP" not in response.text
            else:
                assert "Return Review and Settlement" in response.text and "Proceed with refund" in response.text
                assert "Open existing refund workflow" not in response.text
                assert 'class="returns-case-dialog sales-return-dialog"' in response.text
                assert "Supporting photos" in response.text
            if role != "sales":
                denied = client.post(f"/portal/{prefix}/returns/actions/proceed", data={"return_id": case,
                    "remarks": "Unauthorized confirmation attempt", "operation_key": str(uuid4())})
                assert denied.status_code in {403, 404}
        response = client.post("/portal/team-leader/returns/actions/proceed", data={"return_id": case,
            "remarks": "Confirmed the verified items and refund value", "operation_key": str(uuid4())})
        assert response.status_code == 200 and returns.get_product_return(case, d["sales"])["sales_authorized"]


def test_migration_replay_preserves_completed_history_without_fictional_authorization(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    complete_refund(d, case)
    refund = fetch_one("SELECT * FROM order_refunds WHERE return_id=%s", (case,))
    with get_transaction_cursor() as cur:
        cur.execute("""UPDATE product_returns SET settlement_authorized_by_account_id=NULL,
            settlement_authorized_at=NULL,settlement_authorized_quote_revision=NULL WHERE return_id=%s""", (case,))
        migration = Path("database/migrations/036_return_sales_authorization.sql").read_text(encoding="utf-8")
        cur.execute(migration)
        cur.execute(migration)
    after = returns.get_product_return(case, d["sales"])
    assert after["status"] == "completed" and after["settlement_authorized_at"] is None
    assert fetch_one("SELECT * FROM order_refunds WHERE return_id=%s", (case,)) == refund


def test_unfinished_historical_replacement_can_confirm_without_inventing_movement_links(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d, "replace", [(substitute(150), 1)])
    receive(d, case)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserve old accepted replacement")
    returns.release_replacement(case, d["inventory"], "Historical replacement already handed over")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE replacement_releases SET inventory_movement_id=NULL WHERE return_id=%s", (case,))
        cur.execute("""UPDATE product_returns SET settlement_authorized_by_account_id=NULL,
            settlement_authorized_at=NULL,settlement_authorized_quote_revision=NULL WHERE return_id=%s""", (case,))
    confirm(d, case)
    complete_refund(d, case)
    assert returns.get_product_return(case, d["sales"])["status"] == "completed"
    assert fetch_one("SELECT inventory_movement_id FROM replacement_releases WHERE return_id=%s", (case,))["inventory_movement_id"] is None
    assert fetch_one("SELECT count(*) n FROM replacement_releases WHERE return_id=%s", (case,))["n"] == 1


def test_non_sellable_returns_cannot_supply_order_or_replacement_allocations(lifecycle):  # noqa: F811
    from decimal import Decimal
    from app.repositories.inventory.allocation import _plan_fefo_allocations
    d = lifecycle
    case = approved(d, "replace")
    receive(d, case)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE item_id=%s", (business_today(), d["product"]))
    assert fetch_one("SELECT SUM(quantity_remaining) n FROM expired_holdings WHERE source='return'")["n"] == 2
    assert repositories.product_by_id(d["product"])["available"] == 0
    with pytest.raises(ValueError, match="Not enough"):
        returns.reserve_replacement(case, d["inventory"], "Non-sellable returned packs cannot be used")
    with pytest.raises(ValueError, match="Not enough"):
        with get_transaction_cursor() as cur:
            _plan_fefo_allocations(cur, {d["product"]: Decimal(2)})
    assert fetch_one("SELECT count(*) n FROM replacement_releases WHERE return_id=%s", (case,))["n"] == 0


def test_customer_view_keeps_internal_audit_notes_private_and_rejects_other_accounts(lifecycle):  # noqa: F811
    d = lifecycle
    case = create_case(d)
    secret = "INTERNAL_TREASURY_SECRET reviewed by Sales only"
    returns.review_product_return(case, d["sales"], True, secret)
    receive(d, case, authorize=False)
    returns.authorize_return_settlement(case, d["sales"], secret, str(uuid4()))
    customer = returns.get_product_return(case, d["reseller"])
    assert secret not in str(customer)
    assert not {"receipt_lines", "receipts", "custody_movements", "reconciliation_flags",
                "settlement_authorized_by_account_id", "request_hash", "reserved_batches"}.intersection(customer)
    assert secret in str(returns.get_product_return(case, d["sales"])["events"])
    assert secret not in str(repositories.list_notification_history(d["reseller"]))
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO accounts(account_type,reseller_id,name,email,password_hash)
            SELECT 'reseller',reseller_id,'Other customer','other.return@handoff.test','hash'
            FROM accounts WHERE account_id=%s RETURNING account_id""", (d["reseller"],))
        other = cur.fetchone()["account_id"]
    assert returns.list_product_returns(other) == []
    with pytest.raises(ValueError, match="not found"):
        returns.get_product_return(case, other)
