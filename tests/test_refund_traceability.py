"""Sales-to-Inventory refund gates, custody reconciliation, and visibility."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions
from app.business_time import business_now
from app.database import fetch_one, fetch_all, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import supporting_photo, lifecycle, create_case, receive, refund_proof  # noqa: F401

pytestmark = pytest.mark.postgres


def approved(d, quantity=2):
    case = create_case(d, quantity=quantity)
    returns.review_product_return(case, d["sales"], True, "Approved expired stock return")
    return case


def test_return_operation_replay_rejects_changed_claim(lifecycle):  # noqa: F811
    d = lifecycle
    key = str(uuid4())
    args = (d["order"], d["reseller"], "refund", [(d["allocation"], 2)], "Expired packs returned for review", [supporting_photo()], key)
    case = returns.create_product_return(*args)
    assert returns.create_product_return(*args) == case
    with pytest.raises(ValueError, match="different details"):
        returns.create_product_return(
            d["order"], d["reseller"], "refund", [(d["allocation"], 3)], "Expired packs returned for review", [supporting_photo()], key
        )
    assert fetch_one("SELECT count(*) n FROM product_returns")["n"] == 1


def transfer(d, refund, **overrides):
    args = dict(
        refund_method="Simulated bank transfer",
        transaction_reference="SIM" + uuid4().hex,
        transferred_at=business_now() - timedelta(seconds=1),
        internal_note="SIMULATED transfer checked against ledger",
        proof=refund_proof(),
        operation_key=str(uuid4()),
    )
    args.update(overrides)
    repositories.complete_order_refund(refund, d["supervisor"], "sales-supervisor", **args)
    return args


def test_receipt_is_separate_from_final_verification_and_replays(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case, 1, verify=False)
    assert returns.get_product_return(case, d["sales"])["status"] == "awaiting_receipt"
    with pytest.raises(ValueError, match="Receive all"):
        returns.verify_product_return(case, d["inventory"], "Cannot verify incomplete receipts", str(uuid4()))
    line = returns.get_product_return(case, d["sales"])["lines"][0]["return_line_id"]
    key = str(uuid4())
    args = (case, d["inventory"], [(line, 1)], "Expired freezer", "Matched original expired goods", key)
    for _ in range(2):
        returns.receive_product_return(*args)
    with pytest.raises(ValueError, match="different details"):
        returns.receive_product_return(
            case, d["inventory"], [(line, 1)], "Different freezer", "Matched original expired goods", key
        )
    data = returns.get_product_return(case, d["sales"])
    assert data["status"] == "awaiting_inventory_verification" and data["refund"] is None
    assert len(data["receipts"]) == 2 and not data["reconciliation_flags"]
    key = str(uuid4())
    for _ in range(2):
        returns.verify_product_return(case, d["inventory"], "Final quantities and custody checked", key)
    assert fetch_one("SELECT count(*) n FROM order_refunds")["n"] == 1
    assert fetch_one("SELECT count(*) n FROM product_return_events WHERE action='inventory_verified'")["n"] == 1
    assert (
        fetch_one("SELECT quantity_available n FROM inventory_batches WHERE batch_id=%s", (d["batches"][0],))["n"] == 10
    )
    with pytest.raises(ValueError):
        returns.review_product_return(
            case, d["sales"], True, "Cannot change verified quantities", revised_quantities=[(line, 1)]
        )


def test_settlement_evidence_retries_replays_and_history(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    refund = returns.get_product_return(case, d["sales"])["refund"]["order_refund_id"]
    key = str(uuid4())
    first = repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor", key)
    assert repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor", key) == first
    key = str(uuid4())
    for _ in range(2):
        repositories.fail_order_refund(refund, d["supervisor"], "sales-supervisor", "SIMULATED transfer failed", key)
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor", str(uuid4()))
    with pytest.raises(ValueError, match="proof"):
        transfer(d, refund, proof=None)
    with pytest.raises(ValueError, match="remarks"):
        transfer(d, refund, internal_note="")
    args = transfer(d, refund)
    repositories.complete_order_refund(refund, d["supervisor"], "sales-supervisor", **args)
    with pytest.raises(ValueError, match="different details"):
        repositories.complete_order_refund(
            refund, d["supervisor"], "sales-supervisor", **{**args, "internal_note": "Changed settlement remarks"}
        )
    data = returns.get_product_return(case, d["sales"])
    assert data["status"] == "completed"
    events = [e for e in data["events"] if e["refund_id"]]
    assert [e["action"] for e in events] == [
        "refund_processing",
        "refund_failed",
        "refund_processing",
        "refund_completed",
    ]
    assert all(e["amount"] == 200 and e["refund_attempt_id"] for e in events)
    assert len(repositories.get_order_refund(refund, "sales-supervisor", d["supervisor"])["attempts"]) == 2


def test_holding_disposal_does_not_block_refund_and_proof_cannot_be_reused(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    data = returns.get_product_return(case, d["sales"])
    holding = data["receipt_lines"][0]["holding_id"]
    returns.dispose_expired_stock(
        holding, d["inventory"], 1, "Documented waste collection", "SIMULATED partial disposal", str(uuid4())
    )
    refund = data["refund"]["order_refund_id"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    args = transfer(d, refund)
    case2 = approved(d)
    receive(d, case2)
    refund2 = returns.get_product_return(case2, d["sales"])["refund"]["order_refund_id"]
    repositories.start_order_refund(refund2, d["supervisor"], "sales-supervisor")
    with pytest.raises(ValueError, match="already used"):
        transfer(d, refund2, proof=args["proof"])
    with pytest.raises(ValueError, match="reference"):
        transfer(d, refund2, transaction_reference=args["transaction_reference"])
    assert fetch_one("SELECT status FROM order_refunds WHERE order_refund_id=%s", (refund2,))["status"] == "processing"


def test_reconciliation_blocks_corrupted_credit_or_holding(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    refund = returns.get_product_return(case, d["sales"])["refund"]["order_refund_id"]
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE product_returns SET credit=credit+1 WHERE return_id=%s", (case,))
    with pytest.raises(ValueError, match="credit"):
        repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE product_returns SET credit=credit-1 WHERE return_id=%s", (case,))
        cur.execute("UPDATE expired_holdings SET quantity_remaining=quantity_remaining-1")
    assert returns.get_product_return(case, d["sales"])["reconciliation_flags"]
    with pytest.raises(ValueError, match="reconcile"):
        repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    assert fetch_one("SELECT count(*) n FROM order_refund_attempts")["n"] == 0


def test_sales_visibility_scope_proofs_and_http_mutations(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    refund = returns.get_product_return(case, d["sales"])["refund"]["order_refund_id"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    transfer(d, refund)
    data = repositories.get_order_refund(refund, "team-leader", d["sales"])
    assert data["return_case"]["verified_by_account_id"] == d["inventory"]
    assert "internal_note" not in data["attempts"][0]
    proof = data["attempts"][0]["proof_id"]
    assert repositories.get_order_refund_proof(proof, "team-leader", d["sales"])
    assert repositories.get_order_refund_proof(proof, "team-leader", d["inventory"]) is None
    assert repositories.get_order_refund(refund, "team-leader", d["inventory"]) is None
    with get_transaction_cursor() as cur:
        cur.execute(
            "INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Other sales leader','other.sales@refund.test','hash','sales') RETURNING account_id"
        )
        other_sales = cur.fetchone()["account_id"]
    assert repositories.list_order_refunds("team-leader", other_sales) == []
    assert repositories.count_order_refunds("team-leader", other_sales) == 0
    assert repositories.get_order_refund_proof(proof, "team-leader", other_sales) is None
    session = dict(
        account_id=d["sales"],
        role_key="team-leader",
        team_leader_role="sales",
        portal_expires_at=int(business_now().timestamp()) + 600,
    )
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    with TestClient(main.app) as client:
        response = client.get(f"/portal/team-leader/refunds?refund={refund}")
        assert response.status_code == 200, response.text[:1000]
        assert "Sales Refunds" in response.text and "Receipt #" in response.text
        assert "SIMULATED transfer checked against ledger" not in response.text
        assert 'name="internal_note"' not in response.text
        for action in ["start", "retry", "fail", "complete", "take-over", "messages"]:
            response = client.post(
                f"/portal/team-leader/refunds/{refund}/{action}",
                data={
                    "failure_reason": "Rejected mutation",
                    "refund_method": "Bank",
                    "transaction_reference": "FAKE123",
                    "transferred_at": "2026-01-01T00:00",
                    "reason": "Unauthorized attempt",
                    "message": "Unauthorized message",
                },
            )
            assert response.status_code in {403, 404}, (action, response.status_code, response.text)
        assert client.get(f"/portal/refund-proofs/{proof}").status_code == 200
        session["account_id"] = other_sales
        assert client.get(f"/portal/team-leader/refunds?refund={refund}").status_code == 404
        assert client.get(f"/portal/refund-proofs/{proof}").status_code == 404
        session["account_id"] = d["inventory"]
        session["team_leader_role"] = "inventory"
        assert client.get("/portal/team-leader/refunds").status_code in {403, 404}
        assert client.get(f"/portal/refund-proofs/{proof}").status_code == 404


def test_concurrent_verification_and_completion_deduct_once(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case, verify=False)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(returns.verify_product_return, case, d["inventory"], "Final custody check completed", key)
            for _ in range(2)
        ]
        for f in futures:
            f.result(timeout=10)
    returns.authorize_return_settlement(case, d["sales"], "Sales confirms verified concurrent receipts", str(uuid4()))
    refund = returns.get_product_return(case, d["sales"])["refund"]["order_refund_id"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    args = dict(
        refund_method="Simulated bank",
        transaction_reference="SIM" + uuid4().hex,
        transferred_at=business_now() - timedelta(seconds=1),
        internal_note="Simulated settlement recorded once",
        proof=refund_proof(),
        operation_key=str(uuid4()),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(repositories.complete_order_refund, refund, d["supervisor"], "sales-supervisor", **args)
            for _ in range(2)
        ]
        for f in futures:
            f.result(timeout=10)
    assert len(fetch_all("SELECT * FROM order_refund_proofs")) == 1
    assert fetch_one("SELECT count(*) n FROM order_refund_attempts")["n"] == 1
    with pytest.raises(psycopg2.Error, match="cannot be changed"):
        with get_transaction_cursor() as cur:
            cur.execute("DELETE FROM product_return_receipts")


def test_legacy_upgrade_backfills_only_proven_receipts_and_preserves_verification(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    before = fetch_one("SELECT verified_at,credit,status FROM product_returns WHERE return_id=%s", (case,))
    with get_transaction_cursor() as cur:
        cur.execute("DROP VIEW order_financial_reconciliation")
        cur.execute("DROP VIEW product_return_review_reconciliation")
        cur.execute("DROP VIEW product_return_reconciliation")
        cur.execute("DROP TABLE product_return_receipt_lines,product_return_receipts,refund_operations")
        cur.execute("ALTER TABLE product_returns DROP COLUMN verified_by_account_id,DROP COLUMN verification_remarks")
        cur.execute(Path("database/migrations/033_refund_inventory_traceability.sql").read_text())
        cur.execute(Path("database/migrations/033_refund_inventory_traceability.sql").read_text())
        cur.execute(Path("database/migrations/035_align_order_financial_settlements.sql").read_text())
    after = returns.get_product_return(case, d["sales"])
    assert all(after[k] == v for k, v in before.items())
    assert after["verified_by_account_id"] is None and len(after["receipts"]) == 1
    assert after["reconciliation_flags"] == ["Final Inventory verification requires a named verifier."]
    refund = after["refund"]["order_refund_id"]
    with pytest.raises(ValueError, match="verification"):
        repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    returns.verify_product_return(case, d["inventory"], "Legacy receipt and custody reverified", str(uuid4()))
    assert returns.get_product_return(case, d["sales"])["verified_by_account_id"] == d["inventory"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    transfer(d, refund)
