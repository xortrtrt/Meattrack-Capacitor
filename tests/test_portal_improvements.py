"""Connected payment, reservation, handoff, media, and refund invariants."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import re
from threading import Barrier
from uuid import uuid4

import pytest
import psycopg2
from fastapi.testclient import TestClient

from app import main, repositories, reseller_messaging, receipt_ocr
from app.business_time import business_now
from app.conversation_notifications import unread_summary, mark_refund_read
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from app.repositories.portal_dashboard import leader_work_summary
from test_inventory_postgres import _seed_fulfillment, _payment_submission
from test_inventory_postgres import strict_inventory_database as strict_inventory_database
from test_product_lifecycle import lifecycle, create_case, supporting_photo, refund_proof  # noqa: F401
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


def unpaid(dsn, quantity=2):
    leader, product, batches, _ = _seed_fulfillment(dsn, batch_quantities=[8], order_quantities=[])
    account = fetch_one("SELECT account_id FROM accounts WHERE email='account@test.local'")["account_id"]
    order = repositories.create_order_from_items("reseller", [(product, quantity)], account_id=account)
    return leader, product, batches[0], account, order


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "199.99", "199.999"])
def test_insufficient_payments_leave_no_proofs_or_reservations(strict_inventory_database, amount):
    _, _, _, account, order = unpaid(strict_inventory_database)
    proofs, payment = _payment_submission(amount)
    payment["payment_amount"] = Decimal(amount)
    with pytest.raises(ValueError, match="valid payment|Shortfall"):
        repositories.submit_order_payment(account, order["order_id"], proofs, payment)
    assert fetch_one("SELECT status FROM orders WHERE order_id=%s", (order["order_id"],))["status"] == "to_pay"
    assert fetch_one("SELECT COUNT(*) n FROM order_payment_proofs")["n"] == 0
    assert fetch_one("SELECT COUNT(*) n FROM order_stock_reservations")["n"] == 0


def test_full_payment_reserves_once_and_fulfillment_preserves_availability(strict_inventory_database):
    leader, product, batch, account, order = unpaid(strict_inventory_database)
    assert repositories.product_by_id(product)["available"] == 8
    proofs, payment = _payment_submission("sufficient")
    payment["payment_amount"] = Decimal("200.00")
    for _ in range(2):
        assert repositories.submit_order_payment(account, order["order_id"], proofs, payment)["status"] == "pending"
    assert repositories.product_by_id(product)["available"] == 6
    assert (
        fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (batch,))["quantity_available"]
        == 8
    )
    repositories.decide_order(order["order_id"], "approve", team_leader_account_id=leader)
    repositories.decide_order(order["order_id"], "fulfill", team_leader_account_id=leader)
    assert repositories.product_by_id(product)["available"] == 6
    assert (
        fetch_one("SELECT quantity_available FROM inventory_batches WHERE batch_id=%s", (batch,))["quantity_available"]
        == 6
    )
    assert [
        (r["action"], r["quantity_delta"])
        for r in fetch_all("SELECT * FROM order_reservation_events ORDER BY reservation_event_id")
    ] == [("reserved", 2), ("fulfilled", -2)]
    logs = repositories.list_inventory_movements(category="reservations")
    assert len(logs) == 2 and all(r["quantity_delta"] == 0 for r in logs)
    assert repositories.count_orders(order_type="reseller", team_leader_account_id=leader, exclude_to_pay=True) == 1
    with pytest.raises(
        psycopg2.errors.RaiseException,
        match="immutable|trace|append",
    ):
        with get_transaction_cursor() as cur:
            cur.execute("DELETE FROM order_reservation_events")


def test_unpaid_orders_hidden_and_database_blocks_underpaid_transition(strict_inventory_database):
    leader, _, _, account, order = unpaid(strict_inventory_database)
    assert repositories.count_orders(order_type="reseller", team_leader_account_id=leader, exclude_to_pay=True) == 0
    assert repositories.list_orders(order_type="reseller", team_leader_account_id=leader, exclude_to_pay=True) == []
    proofs, payment = _payment_submission("db-guard")
    with pytest.raises(psycopg2.errors.RaiseException, match="sufficient|Insufficient"):
        with get_transaction_cursor() as cur:
            cur.execute(
                """INSERT INTO order_payment_proofs(order_id,uploaded_by_account_id,filename,content_type,content,size_bytes,checksum_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    order["order_id"],
                    account,
                    *[proofs[0][k] for k in ("filename", "content_type", "content", "size_bytes", "checksum_sha256")],
                ),
            )
            cur.execute(
                """UPDATE orders SET status='pending',payment_amount=1,payment_transaction_number=%s,
                payment_transaction_key=%s,payment_paid_at=now(),payment_confirmed_at=now() WHERE order_id=%s""",
                (payment["transaction_number"], payment["transaction_key"], order["order_id"]),
            )


def test_expiry_racing_payment_cannot_reanimate_or_reserve_an_expired_order(strict_inventory_database):
    _, _, _, account, order = unpaid(strict_inventory_database)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE orders SET order_date=now()-interval '30 minutes', payment_due_at=now()-interval '1 second' WHERE order_id=%s", (order["order_id"],))
    barrier = Barrier(2)

    def pay():
        proofs, payment = _payment_submission("expiry-race")
        payment["payment_amount"] = Decimal("200")
        barrier.wait(timeout=10)
        with pytest.raises(ValueError, match="expired|no longer awaiting payment"):
            repositories.submit_order_payment(account, order["order_id"], proofs, payment)

    def expire():
        barrier.wait(timeout=10)
        repositories.expire_unpaid_orders(account_id=account)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(pay), executor.submit(expire)]
        for future in futures:
            future.result(timeout=30)
    assert fetch_one("SELECT status FROM orders WHERE order_id=%s", (order["order_id"],))["status"] == "expired"
    assert fetch_one("SELECT COUNT(*) n FROM order_payment_proofs")["n"] == 0
    assert fetch_one("SELECT COUNT(*) n FROM order_stock_reservations")["n"] == 0
    assert fetch_one("SELECT COUNT(*) n FROM notifications WHERE dedupe_key=%s", (f"order-expired-{order['order_id']}",))["n"] == 1


def test_competing_payments_do_not_oversell_and_shortage_requires_receipt_review(strict_inventory_database):
    leader, product, _, account, order = unpaid(strict_inventory_database, quantity=8)
    with get_transaction_cursor() as cur:
        cur.execute(
            """INSERT INTO resellers(business_name,contact_person,email,contact_number,reseller_status,team_leader_account_id)
            VALUES('Other','Other','other@stock.test','09170000001','active',%s) RETURNING reseller_id""",
            (leader,),
        )
        reseller = cur.fetchone()["reseller_id"]
        cur.execute(
            "INSERT INTO accounts(account_type,reseller_id,name,email,password_hash) VALUES('reseller',%s,'Other','other@stock.test','hash') RETURNING account_id",
            (reseller,),
        )
        other = cur.fetchone()["account_id"]
        cur.execute(
            "INSERT INTO accounts(account_type,name,email,password_hash) VALUES('owner','Owner','owner@stock.test','hash') RETURNING account_id"
        )
        owner = cur.fetchone()["account_id"]
        supervisors = []
        for label in ("Processor", "Unrelated"):
            cur.execute(
                """INSERT INTO accounts(account_type,team_leader_role,name,email,password_hash)
                VALUES('team_leader','sales-supervisor',%s,%s,'hash') RETURNING account_id""",
                (label, f"{label.lower()}@stock.test"),
            )
            supervisors.append(cur.fetchone()["account_id"])
    second = repositories.create_order_from_items("reseller", [(product, 8)], account_id=other)

    def pay(actor, oid):
        proofs, payment = _payment_submission(str(actor))
        payment["payment_amount"] = Decimal("800")
        return repositories.submit_order_payment(actor, oid, proofs, payment)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda args: pay(*args), [(account, order["order_id"]), (other, second["order_id"])])
        )
    assert sorted(r["status"] for r in results) == ["pending", "rejected"]
    assert repositories.product_by_id(product)["available"] == 0
    refund = fetch_one("SELECT * FROM order_refunds")
    assert refund["payment_review_required"]
    processor = refund["assigned_supervisor_account_id"]
    assert processor in supervisors
    proof_id = fetch_one(
        "SELECT order_payment_proof_id FROM order_payment_proofs WHERE order_id=%s", (refund["order_id"],)
    )["order_payment_proof_id"]
    assert repositories.get_order_payment_proof(proof_id, processor, "sales-supervisor")
    unrelated = next(actor for actor in supervisors if actor != processor)
    assert repositories.get_order_payment_proof(proof_id, unrelated, "sales-supervisor") is None
    assert repositories.get_order_payment_proof(proof_id, owner, "owner")
    with pytest.raises(ValueError, match="actually received"):
        repositories.start_order_refund(refund["order_refund_id"], owner, "owner")
    repositories.confirm_shortage_payment(refund["order_refund_id"], owner, "owner")
    repositories.start_order_refund(refund["order_refund_id"], owner, "owner")


def test_accepted_case_transfers_to_supervisor_and_sales_tracks_only(lifecycle):  # noqa: F811
    d = lifecycle
    case = create_case(d)
    key = str(uuid4())
    for _ in range(2):
        returns.review_product_return(case, d["sales"], True, "Initial assigned leader approval", operation_key=key)
    state = returns.get_product_return(case, d["sales"])
    assert state["processing_supervisor_account_id"] == d["supervisor"] and state["handed_off_at"]
    assert state["approved_by_account_id"] == d["sales"]
    assert state["experience"]["sales"]["action"] == ""
    assert any(c["return_id"] == case for c in returns.list_product_returns(d["supervisor"]))
    with pytest.raises(ValueError, match="tracking only"):
        returns.review_product_return(case, d["sales"], True, "Stale browser tries to revise")
    with pytest.raises(ValueError, match="tracking only"):
        returns.authorize_return_settlement(case, d["sales"], "Cannot authorize after handoff", str(uuid4()))
    returns.review_product_return(case, d["supervisor"], True, "Processor can manage the accepted case")
    assert fetch_one("SELECT COUNT(*) n FROM product_return_events WHERE action='processing_handoff'")["n"] == 1


def test_existing_return_details_keep_multiple_cases_selectable_and_default_closed(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    cases = [create_case(d), create_case(d)]
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        for view in ("all", "fulfilled"):
            html = client.get(f"/portal/reseller/history?view={view}").text
            assert ">Details</a>" in html
            assert ">Request return</a>" not in html
            assert f"source_order={d['order']}&amp;case={cases[-1]}" in html
            assert not re.search(r'<details class="order-history-card"[^>]*\bopen\b', html)
        html = client.get("/portal/reseller/history?view=returns").text
        assert not re.search(r'<details class="order-history-card"[^>]*\bopen\b', html)
        html = client.get(
            f"/portal/reseller/history?view=returns&source_order={d['order']}&case={cases[-1]}"
        ).text
        assert 'data-return-details' in html and f'data-return-id="{cases[-1]}"' in html
        assert "Return history" in html
        history = client.get(f"/portal/reseller/history?view=returns&source_order={d['order']}").text
        assert all(f'id="return-{case}"' in history for case in cases)
        for case in cases:
            selected = client.get(f"/portal/reseller/history?view=returns&source_order={d['order']}&case={case}").text
            assert f'data-return-id="{case}"' in selected


def test_media_retry_private_access_and_read_cursor(lifecycle):  # noqa: F811
    d = lifecycle
    photo = supporting_photo()
    key = str(uuid4())
    first = reseller_messaging.send_from_reseller(d["reseller"], key, "", attachments=[photo])
    again = reseller_messaging.send_from_reseller(d["reseller"], key, "", attachments=[photo])
    assert first["reseller_message_id"] == again["reseller_message_id"]
    attachment = first["attachments"][0]["attachment_id"]
    assert reseller_messaging.leader_thread(d["sales"], d["reseller"])["messages"][0]["attachments"]
    assert (
        bytes(reseller_messaging.get_attachment(d["sales"], "team-leader", attachment)["content"]) == photo["content"]
    )
    with pytest.raises(ValueError, match="not found"):
        reseller_messaging.get_attachment(d["supervisor"], "sales-supervisor", attachment)
    with pytest.raises(ValueError, match="different attachments"):
        reseller_messaging.send_from_reseller(
            d["reseller"], key, "", attachments=[{**photo, "filename": "different.png"}]
        )
    assert unread_summary(d["sales"], "team-leader")["unread_conversations"] == 1
    second = reseller_messaging.send_from_reseller(d["reseller"], str(uuid4()), "Second incoming message")
    reseller_messaging.mark_read_for_leader(d["sales"], d["reseller"], first["reseller_message_id"])
    assert unread_summary(d["sales"], "team-leader")["threads"][0]["unread_count"] == 1
    reseller_messaging.mark_read_for_leader(d["sales"], d["reseller"], second["reseller_message_id"])
    assert unread_summary(d["sales"], "team-leader")["unread_conversations"] == 0


def test_refund_scan_scope_amount_and_once_only_closure(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = create_case(d)
    returns.review_product_return(case, d["sales"], True, "Approve complete refund request")
    returns.inspect_product_return(
        case, d["inventory"], "complete", "All approved packs and evidence match", str(uuid4())
    )
    refund = returns.get_product_return(case, d["supervisor"])["refund"]
    fid = refund["order_refund_id"]
    repositories.start_order_refund(fid, d["supervisor"], "sales-supervisor")
    sign_in(monkeypatch, d["supervisor"], "sales-supervisor", "sales-supervisor")
    monkeypatch.setattr(
        receipt_ocr,
        "scan_payment_proofs",
        lambda attachments: {
            "transaction_number": "REFUND123456",
            "payment_date": business_now().date().isoformat(),
            "payment_time": "00:00",
            "payment_amount": "200.00",
            "confidence": 0.9,
        },
    )
    photo = supporting_photo()
    with TestClient(main.app) as client:
        scanned = client.post(
            f"/portal/sales-supervisor/refunds/{fid}/receipt-scan",
            files={"proof": ("receipt.png", photo["content"], "image/png")},
        )
        assert scanned.status_code == 200
        token = scanned.json()["scan_token"]
        with pytest.raises(ValueError, match="does not match"):
            receipt_ocr.verify_scan_token(d["supervisor"], [photo], token, context=f"refund:{fid + 1}")
        fields = dict(
            refund_method="Bank transfer",
            transaction_reference="REFUND123456",
            transferred_at=business_now().date().isoformat() + "T00:00",
            internal_note="Checked against transfer receipt",
            receipt_scan_token=token,
            payment_details_confirmed="yes",
            transfer_amount="199.99",
            operation_key=str(uuid4()),
        )
        bad = client.post(
            f"/portal/sales-supervisor/refunds/{fid}/complete",
            data=fields,
            files={"proof": ("receipt.png", photo["content"], "image/png")},
            follow_redirects=False,
        )
        assert "Transfer+amount+must+equal" in bad.headers["location"]
        fields["transfer_amount"] = "200.00"
        for _ in range(2):
            result = client.post(
                f"/portal/sales-supervisor/refunds/{fid}/complete",
                data=fields,
                files={"proof": ("receipt.png", photo["content"], "image/png")},
                follow_redirects=False,
            )
            assert "Refund+completed" in result.headers["location"]
    closing = fetch_all("SELECT * FROM order_refund_messages WHERE message_kind='system'")
    assert len(closing) == 1 and "Thank you for your patience" in closing[0]["message"]
    summary = unread_summary(d["reseller"], "reseller")
    assert summary["unread_conversations"] == 1  # System event and refund reply share the order thread.
    mark_refund_read(fid, d["reseller"], "reseller", closing[0]["order_refund_message_id"])
    reply = repositories.send_order_refund_message(
        fid, d["reseller"], "reseller", "I have a question about this completed refund"
    )
    assert reply["message"] == "I have a question about this completed refund"


@pytest.mark.parametrize("role", ["sales", "inventory", "supervisor"])
def test_all_leader_dashboards_render_scoped_priorities(lifecycle, monkeypatch, role):  # noqa: F811
    d = lifecycle
    specialized = "sales-supervisor" if role == "supervisor" else role
    portal = "sales-supervisor" if role == "supervisor" else "team-leader"
    sign_in(monkeypatch, d[role], portal, specialized)
    with TestClient(main.app) as client:
        response = client.get(f"/portal/{portal}/dashboard")
    assert response.status_code == 200
    assert {"sales": "Your work at a glance", "inventory": "Warehouse Overview", "supervisor": "Operations Overview"}[role] in response.text
    assert "data-dashboard-queue" in response.text
    summary = leader_work_summary(d[role], specialized)
    assert summary["queue"]


def test_existing_accepted_case_handoff_migration_is_idempotent(lifecycle):  # noqa: F811
    from pathlib import Path

    d = lifecycle
    case = create_case(d)
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE product_returns SET status='awaiting_receipt',approved_by_account_id=%s WHERE return_id=%s",
            (d["sales"], case),
        )
    before = fetch_one(
        "SELECT approved_by_account_id,credit,quote_revision FROM product_returns WHERE return_id=%s", (case,)
    )
    for _ in range(2):
        with get_transaction_cursor() as cur:
            cur.execute(Path("database/migrations/041_return_processing_handoff.sql").read_text(encoding="utf-8"))
    after = returns.get_product_return(case, d["supervisor"])
    assert after["processing_supervisor_account_id"] == d["supervisor"] and after["handed_off_at"]
    assert all(after[key] == value for key, value in before.items())
    assert (
        fetch_one(
            "SELECT COUNT(*) n FROM product_return_events WHERE return_id=%s AND action='processing_handoff'", (case,)
        )["n"]
        == 1
    )
    assert fetch_one("SELECT COUNT(*) n FROM notifications WHERE dedupe_key LIKE 'return-migration-041-%%'")["n"] == 2


def test_migration_releases_legacy_unpaid_hold_with_a_balanced_audit(strict_inventory_database):
    from pathlib import Path

    _, product, _, _, order = unpaid(strict_inventory_database)
    with get_transaction_cursor() as cur:
        cur.execute("ALTER TABLE order_stock_reservations DISABLE TRIGGER guard_paid_order_reservation")
        cur.execute("DROP TRIGGER validate_order_reservation_change ON order_stock_reservations")
        cur.execute(Path("database/migrations/017_scope_timed_order_reservations.sql").read_text(encoding="utf-8"))
        cur.execute(
            "INSERT INTO order_stock_reservations(order_id,product_id,quantity) VALUES(%s,%s,2)",
            (order["order_id"], product),
        )
    with get_transaction_cursor() as cur:
        cur.execute(Path("database/migrations/040_paid_stock_reservations.sql").read_text(encoding="utf-8"))
    assert fetch_one("SELECT COUNT(*) n FROM order_stock_reservations")["n"] == 0
    assert fetch_one("SELECT SUM(quantity_delta) n FROM order_reservation_events")["n"] == 0
    assert fetch_one("SELECT COUNT(*) n FROM order_reservation_events WHERE action='unpaid_hold_released'")["n"] == 1


def test_legacy_pending_payment_uses_confirmed_details_and_reserves_once(strict_inventory_database):
    _, product, _, account, order = unpaid(strict_inventory_database)
    with get_transaction_cursor() as cur:
        # Emulate a pre-payment-policy row in this isolated test schema.
        cur.execute("ALTER TABLE orders DISABLE TRIGGER USER")
        cur.execute("UPDATE orders SET status='pending', payment_due_at=NULL WHERE order_id=%s", (order["order_id"],))
        cur.execute("ALTER TABLE orders ENABLE TRIGGER USER")
    proofs, payment = _payment_submission("legacy-confirmation")
    result = repositories.submit_order_payment(account, order["order_id"], proofs, payment)
    assert result["status"] == "pending"
    stored = fetch_one("SELECT payment_transaction_number,payment_amount FROM orders WHERE order_id=%s", (order["order_id"],))
    assert stored["payment_transaction_number"] == payment["transaction_number"]
    assert stored["payment_amount"] == payment["payment_amount"]
    assert fetch_one("SELECT SUM(quantity) quantity FROM order_stock_reservations WHERE order_id=%s", (order["order_id"],))["quantity"] == 2
