from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app import main, reseller_messaging as chat
from app import security_controls, web_sessions
from app.business_time import business_now
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns, return_customer as customer
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, refund_proof, substitute  # noqa: F401
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


def complete_replacement(d, replacement_price=None):
    case = create_case(d, "replace")
    returns.review_product_return(case, d["sales"], True, "Private Sales review", [(substitute(replacement_price), 1)] if replacement_price else None)
    returns.inspect_product_return(case, d["inventory"], "complete", "Complete matching products received", str(uuid4()))
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Valid replacement packs reserved")
    if not replacement_price or replacement_price <= 200:
        returns.release_replacement(case, d["inventory"], "Replacement handed over to reseller")
    return case


def test_followups_and_acknowledgments_do_not_change_settlement_or_inventory(lifecycle):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    before = fetch_all("SELECT * FROM inventory_movements ORDER BY movement_id")
    batch_before = fetch_all("SELECT * FROM inventory_batches ORDER BY batch_id")
    settled = fetch_one("SELECT status,completed_at FROM product_returns WHERE return_id=%s", (case,))
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(chat.send_from_reseller, d["reseller"], key, "One replacement pack is damaged", return_id=case, report_issue=True) for _ in range(2)]
        for job in jobs:
            assert job.result(timeout=30)["return_id"] == case
    chat.send_from_reseller(d["reseller"], str(uuid4()), "Here are more details", return_id=case, report_issue=True)
    assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 1
    assert fetch_one("SELECT COUNT(*) n FROM return_followup_messages")["n"] == 2
    assert fetch_one("SELECT COUNT(*) n FROM reseller_messages")["n"] == 2
    assert returns.return_queue_counts(d["sales"])["needs_review"] == 1
    assert returns.list_product_returns(d["sales"], queue="completed") == []
    assert returns.get_product_return(case, d["inventory"])["status"] == "completed"
    for _ in range(2):
        customer.acknowledge_replacement(case, d["reseller"])
    assert fetch_one("SELECT COUNT(*) n FROM product_return_events WHERE action='replacement_received'")["n"] == 1
    assert returns.get_product_return(case, d["sales"])["has_open_followup"]
    followup = fetch_one("SELECT followup_id FROM return_customer_followups")["followup_id"]
    for _ in range(2):
        customer.resolve_followup(case, d["sales"], followup, "We contacted you and resolved your concern.")
    assert returns.return_queue_counts(d["sales"])["completed"] == 1
    assert fetch_one("SELECT status,completed_at FROM product_returns WHERE return_id=%s", (case,)) == settled
    assert fetch_all("SELECT * FROM inventory_movements ORDER BY movement_id") == before
    assert fetch_all("SELECT * FROM inventory_batches ORDER BY batch_id") == batch_before
    assert returns.get_product_return(case, d["reseller"])["replacement_received_at"]
    assert returns.get_product_return(case, d["reseller"])["followups"][0]["resolution_message"].startswith("We contacted")
    chat.send_from_reseller(d["reseller"], str(uuid4()), "I have a different replacement concern", return_id=case, report_issue=True)
    assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 2


@pytest.mark.parametrize("unavailable", ["unassigned", "inactive", "reassigned"])
def test_issue_is_retained_when_chat_routing_changes(lifecycle, unavailable):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    with get_transaction_cursor() as cur:
        if unavailable == "unassigned":
            cur.execute("UPDATE resellers SET team_leader_account_id=NULL")
        elif unavailable == "inactive":
            cur.execute("UPDATE accounts SET is_active=false WHERE account_id=%s", (d["sales"],))
        else:
            cur.execute("UPDATE resellers SET team_leader_account_id=%s", (d["supervisor"],))
    key = str(uuid4())
    for _ in range(2):
        result = chat.send_from_reseller(d["reseller"], key, "Please check this replacement problem", return_id=case, report_issue=True)
    assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 1
    if unavailable == "reassigned":
        assert result["return_id"] == case
        with pytest.raises(ValueError):
            returns.get_product_return(case, d["supervisor"])
    else:
        assert result["followup_saved"] and "unavailable" in result["warning"]
        assert fetch_one("SELECT COUNT(*) n FROM reseller_messages")["n"] == 0
    if unavailable == "inactive":
        assert fetch_one("SELECT COUNT(*) n FROM notifications WHERE source_type='product_returns' AND title LIKE 'Replacement issue%%'")["n"] > 0


def test_required_public_explanations_and_offer_revisions(lifecycle):  # noqa: F811
    d = lifecycle
    case = create_case(d, "replace")
    with pytest.raises(ValueError, match="Explanation"):
        returns.review_product_return(case, d["sales"], False, "PRIVATE rejection details")
    returns.review_product_return(case, d["sales"], True, "PRIVATE initial review")
    old = returns.get_product_return(case, d["reseller"])["offer_snapshot"]
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.review_product_return(case, d["sales"], True, "PRIVATE revised offer", [(substitute(250), 1)], customer_message="Please review the new replacement and price difference.")
    view = returns.get_product_return(case, d["reseller"])
    assert view["offer_snapshot"]["revision"] == 2 and view["offer_changes"]
    assert view["previous_offer"] == old
    assert view["accepted_revision"] != 2
    with pytest.raises(ValueError, match="changed"):
        returns.accept_replacement_quote(case, d["reseller"], 1)
    assert "PRIVATE" not in str(view)
    notes = fetch_all("""SELECT n.message FROM notifications n JOIN notification_recipients nr USING(notification_id)
        WHERE nr.account_id=%s AND n.source_type='product_returns'""", (d["reseller"],))
    assert "PRIVATE" not in str(notes)
    assert any("new replacement" in n["message"] for n in notes)
    with pytest.raises(psycopg2.Error):
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE return_offer_snapshots SET credit=0 WHERE return_id=%s", (case,))
    returns.inspect_product_return(case, d["inventory"], "mismatch", "Internal observed packing mismatch", str(uuid4()))
    with pytest.raises(ValueError, match="Explanation"):
        returns.review_product_return(case, d["sales"], True, "PRIVATE correction")
    returns.review_product_return(case, d["sales"], True, "PRIVATE correction", customer_message="Please bring the missing approved pack for checking.")
    assert "missing approved pack" in str(returns.get_product_return(case, d["reseller"])["events"])


def test_payment_queue_and_customer_ownership(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d, 250)
    assert returns.get_product_return(case, d["reseller"])["experience"]["reseller"]["action"] == "payment"
    returns.record_return_payment(case, d["reseller"], [refund_proof()])
    assert returns.return_queue_counts(d["sales"])["payment"] == 1
    assert returns.list_product_returns(d["sales"], queue="payment")[0]["experience"]["sales"]["action"] == "verify_payment"
    sign_in(monkeypatch, d["sales"], "team-leader", "sales")
    with TestClient(main.app) as client:
        page = client.get(f"/portal/team-leader/refunds?view=returns&queue=payment&case={case}")
        assert page.status_code == 200 and "review the reseller’s payment proof" in page.text
        assert 'name="csrf_token"' in page.text and "Confirm additional payment" in page.text
    with pytest.raises(ValueError):
        customer.acknowledge_replacement(case, d["reseller"])
    with pytest.raises(ValueError):
        customer.acknowledge_replacement(case, d["owner"])
    with pytest.raises(ValueError):
        chat.send_from_reseller(d["inventory"], str(uuid4()), "Wrong account", return_id=case)


def test_queue_filtering_counts_and_pagination_are_scoped(lifecycle):  # noqa: F811
    d = lifecycle
    for _ in range(14):
        case = create_case(d, "replace")
        returns.review_product_return(case, d["sales"], False, "Private reason", customer_message="Your request was declined after evidence review.")
    current = create_case(d, "replace")
    counts = returns.return_queue_counts(d["sales"])
    assert counts == {"completed": 14, "needs_review": 1, "all": 15}
    assert len(returns.list_product_returns(d["sales"], queue="completed", page=1)) == 12
    assert len(returns.list_product_returns(d["sales"], queue="completed", page=2)) == 2
    assert returns.list_product_returns(d["sales"], queue="needs_review")[0]["return_id"] == current
    assert returns.return_queue_counts(d["supervisor"]) == {"all": 0}


def test_migration_baseline_preserves_current_offer_and_legacy_public_rejection(lifecycle):  # noqa: F811
    d = lifecycle
    case = create_case(d, "replace")
    returns.review_product_return(case, d["sales"], True, "Private approval")
    with get_transaction_cursor() as cur:
        cur.execute("DROP TRIGGER immutable_return_offer_snapshots ON return_offer_snapshots")
        cur.execute("DELETE FROM return_offer_snapshots")
        cur.execute("INSERT INTO product_return_events(return_id,actor_account_id,action,remarks) VALUES(%s,%s,'rejected','Previously public rejection reason')", (case, d["sales"]))
        cur.execute(Path("database/migrations/038_return_customer_experience.sql").read_text(encoding="utf-8"))
    view = returns.get_product_return(case, d["reseller"])
    assert view["offer_snapshot"]["is_baseline"] and not view["offer_changes"]
    assert "Previously public rejection reason" in str(view["events"])
    assert "Private approval" not in str(view)


def test_context_messages_reject_reused_keys_and_other_reseller_references(lifecycle):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    key = str(uuid4())
    chat.send_from_reseller(d["reseller"], key, "A general question about this return", return_id=case)
    with pytest.raises(ValueError, match="different details"):
        chat.send_from_reseller(d["reseller"], key, "A general question about this return", return_id=case, report_issue=True)
    assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 0
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash) VALUES('reseller','Other','other@customer.test','hash') RETURNING account_id")
        other = cur.fetchone()["account_id"]
    with pytest.raises(ValueError, match="not assigned"):
        chat.send_from_reseller(other, str(uuid4()), "Not my case", return_id=case, report_issue=True)
    assert fetch_one("SELECT COUNT(*) n FROM reseller_messages")["n"] == 1
    assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 0


def test_customer_http_forms_require_csrf_and_public_sales_explanation(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    session = dict(account_id=d["reseller"], role_key="reseller", csrf_token="return-http-token",
                   portal_expires_at=int(business_now().timestamp())+600)
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    with TestClient(main.app, headers={"Origin": "http://testserver"}) as client:
        ack = "/portal/reseller/returns/actions/acknowledge-replacement"
        assert client.post(ack, data={"return_id": case}).status_code == 403
        assert not returns.get_product_return(case, d["reseller"])["replacement_received_at"]
        valid = client.post(ack, data={"return_id": case, "csrf_token": session["csrf_token"]})
        assert valid.status_code == 200
        link = client.get(f"/portal/reseller/inbox?return_id={case}&report_issue=1")
        assert link.status_code == 200
        assert fetch_one("SELECT COUNT(*) n FROM reseller_messages")["n"] == 0
        assert fetch_one("SELECT COUNT(*) n FROM return_customer_followups")["n"] == 0
        session.update(account_id=d["sales"], role_key="team-leader", team_leader_role="sales")
        another = create_case(d)
        payload = dict(return_id=another, remarks="PRIVATE internal reason", operation_key=str(uuid4()), csrf_token=session["csrf_token"])
        rejected = client.post("/portal/team-leader/refunds/returns/actions/reject", data=payload, follow_redirects=False)
        assert rejected.status_code == 303 and "error=" in rejected.headers["location"]
        payload["customer_message"] = "The supporting photo does not match the delivered products."
        rejected = client.post("/portal/team-leader/refunds/returns/actions/reject", data=payload, follow_redirects=False)
        assert "error=" not in rejected.headers["location"]
    view = returns.get_product_return(another, d["reseller"])
    assert "PRIVATE" not in str(view) and "supporting photo" in str(view["events"])


def test_followup_resolution_requires_public_summary_and_assignment(lifecycle):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    chat.send_from_reseller(d["reseller"], str(uuid4()), "A problem with my replacement", return_id=case, report_issue=True)
    followup = fetch_one("SELECT followup_id FROM return_customer_followups")["followup_id"]
    with pytest.raises(ValueError, match="Explanation"):
        customer.resolve_followup(case, d["sales"], followup, "")
    with pytest.raises(ValueError, match="not assigned"):
        customer.resolve_followup(case, d["supervisor"], followup, "Not the assigned Sales reviewer")
    with pytest.raises(psycopg2.IntegrityError):
        with get_transaction_cursor() as cur:
            cur.execute("""UPDATE return_customer_followups SET status='resolved',resolved_at=now(),
                resolved_by_account_id=%s,resolution_message=NULL WHERE followup_id=%s""", (d["sales"], followup))
    assert fetch_one("SELECT status FROM return_customer_followups")["status"] == "open"
