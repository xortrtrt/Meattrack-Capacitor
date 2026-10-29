from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions, worker
from app.business_time import business_now, business_today
from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import _payment_submission, _seed_fulfillment, strict_inventory_database  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture
def lifecycle(strict_inventory_database):  # noqa: F811
    sales, product, batches, orders = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[20, 20], order_quantities=[10]
    )
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE orders SET payment_amount=total_amount WHERE order_id=%s;", (orders[0],))
        cur.execute(
            "INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Inventory','inventory@lifecycle.test','hash','inventory') RETURNING account_id;"
        )
        inventory = cur.fetchone()["account_id"]
        cur.execute(
            "INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Supervisor','supervisor@lifecycle.test','hash','sales-supervisor') RETURNING account_id;"
        )
        supervisor = cur.fetchone()["account_id"]
        cur.execute(
            "INSERT INTO accounts(account_type,name,email,password_hash) VALUES('owner','Lifecycle Owner','owner@lifecycle.test','hash') RETURNING account_id;"
        )
        owner = cur.fetchone()["account_id"]
        cur.execute("SELECT created_by_account_id FROM orders WHERE order_id=%s;", (orders[0],))
        reseller = cur.fetchone()["created_by_account_id"]
    repositories.decide_order(orders[0], "fulfill", team_leader_account_id=sales)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s;", (business_today(), batches[0]))
        cur.execute(
            "SELECT allocation_id FROM fulfillment_allocations a JOIN order_items oi USING(order_item_id) WHERE oi.order_id=%s;",
            (orders[0],),
        )
        allocation = cur.fetchone()["allocation_id"]
    return dict(
        sales=sales,
        inventory=inventory,
        supervisor=supervisor,
        owner=owner,
        reseller=reseller,
        product=product,
        batches=batches,
        order=orders[0],
        allocation=allocation,
        dsn=strict_inventory_database,
    )


def supporting_photo():
    import hashlib
    from io import BytesIO
    from PIL import Image
    stream = BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
    content = stream.getvalue()
    return {"filename": "return-photo.png", "content_type": "image/png", "content": content,
            "size_bytes": len(content), "checksum_sha256": hashlib.sha256(content).hexdigest()}


def create_case(d, resolution="refund", quantity=2):
    return returns.create_product_return(
        d["order"], d["reseller"], resolution, [(d["allocation"], quantity)], "Expired goods returned for review",
        attachments=[supporting_photo()],
    )


def receive(d, case, quantity=2, *, verify=True, authorize=True):
    line = returns.get_product_return(case, d["inventory"])["lines"][0]
    returns.receive_product_return(
        case,
        d["inventory"],
        [(line["return_line_id"], quantity)],
        "Expired freezer A",
        "Matched original batch and packs",
        str(uuid4()),
    )
    if verify and returns.get_product_return(case,d["inventory"])["status"] == "awaiting_inventory_verification":
        returns.verify_product_return(case,d["inventory"],"Final quantities and custody verified",str(uuid4()))
        if authorize:
            returns.authorize_return_settlement(case,d["sales"],"Sales confirms the verified return resolution",str(uuid4()))


def substitute(price):
    with get_transaction_cursor() as cur:
        cur.execute(
            """INSERT INTO inventory_items(item_type,category,name,unit,base_price,pack_size,pack_size_unit,pack_content_status)
            VALUES('finished_product','Pork',%s,'pack',%s,500,'g','declared') RETURNING item_id;""",
            (f"Substitute {price}", price),
        )
        product = cur.fetchone()["item_id"]
    repositories.add_product_batch(product, f"SUB-{uuid4().hex[:8]}", 20, business_today() + timedelta(days=20))
    return product


def complete_refund(d, case):
    refund = returns.get_product_return(case, d["supervisor"])["refund"]
    repositories.start_order_refund(refund["order_refund_id"], d["supervisor"], "sales-supervisor")
    repositories.complete_order_refund(
        refund["order_refund_id"],
        d["supervisor"],
        "sales-supervisor",
        refund_method="Bank transfer",
        transaction_reference=f"REF{uuid4().hex[:14]}",
        transferred_at=business_now(),
        internal_note="Simulated transfer confirmed against ledger",
        proof=refund_proof(),
    )


def refund_proof():
    import hashlib
    content = f"Simulated refund evidence {uuid4()}".encode()
    return {"filename":"refund.png","content_type":"image/png","content":content,"size_bytes":len(content),"checksum_sha256":hashlib.sha256(content).hexdigest()}


def test_expiry_blocks_today_without_worker_and_two_batches_remain_separate(lifecycle):
    d = lifecycle
    assert repositories.product_by_id(d["product"])["available"] == 20
    details = returns.product_lifecycle_details(d["product"])
    assert details["expired"] == 10 and details["available"] == 20
    assert len(details["batches"]) == 2
    for expiry in (business_today(), business_today() - timedelta(days=1)):
        with pytest.raises(ValueError, match="after today"):
            repositories.add_product_batch(d["product"], f"BAD-{uuid4().hex}", 1, expiry)


def test_product_details_pages_preserve_totals_and_include_entire_movement_history(lifecycle):
    d = lifecycle
    for number in range(5):
        repositories.add_product_batch(
            d["product"], f"PAGED-{number}", 4, business_today() + timedelta(days=20 + number)
        )
    with get_transaction_cursor() as cur:
        cur.execute(
            """INSERT INTO inventory_movements
            (item_id,affected_batch_id,movement_type,quantity_delta,unit,balance_before,balance_after,actor_name,note)
            SELECT %s,%s,'opening_balance',0,'pack',20,20,'Test inventory','Historical stock snapshot'
            FROM generate_series(1,55);""",
            (d["product"], d["batches"][1]),
        )
    first = returns.product_lifecycle_details(d["product"])
    second = returns.product_lifecycle_details(d["product"], batch_page=2, movement_page=2)
    last = returns.product_lifecycle_details(d["product"], batch_page=999999, movement_page=999999)
    for details in (first, second, last):
        assert details["warehouse"] == 50
        assert details["eligible"] == details["available"] == 40
        assert details["expired"] == 10
        assert details["near_expiry"] == 20
        assert details["batch_pagination"]["total"] == 7
        assert details["movement_pagination"]["total"] == 63
    assert len(first["batches"]) == len(second["batches"]) == 3
    assert len(last["batches"]) == 1
    assert {row["batch_id"] for row in first["batches"]}.isdisjoint(row["batch_id"] for row in second["batches"])
    assert len(first["movements"]) == len(second["movements"]) == 5
    assert len(last["movements"]) == 3
    assert last["batch_pagination"]["page"] == 3
    assert last["movement_pagination"]["page"] == 13
    oldest = fetch_one("SELECT MIN(movement_id) id FROM inventory_movements WHERE item_id=%s", (d["product"],))["id"]
    assert last["movements"][-1]["movement_id"] == oldest
    negative = returns.product_lifecycle_details(d["product"], batch_page=-1, movement_page=-1)
    assert negative["batch_pagination"]["page"] == negative["movement_pagination"]["page"] == 1


@pytest.mark.parametrize("method", [None, "Documented waste collection"])
def test_pullout_and_disposal_are_partial_and_idempotent(lifecycle, method):
    d = lifecycle
    worker.scan_batch_expiry()
    worker.scan_batch_expiry()
    assert fetch_one("SELECT COUNT(*) n FROM expired_pullouts;")["n"] == 1
    key = str(uuid4())
    for _ in range(2):
        returns.confirm_expired_pullout(
            d["batches"][0], d["inventory"], 4, "Expired freezer A", "Physically segregated from sale", key
        )
    assert (
        fetch_one("SELECT quantity_available n FROM inventory_batches WHERE batch_id=%s;", (d["batches"][0],))["n"] == 6
    )
    holding = fetch_one("SELECT * FROM expired_holdings;")
    key = str(uuid4())
    for _ in range(2):
        returns.dispose_expired_stock(
            holding["holding_id"],
            d["inventory"],
            2,
            method,
            "Disposed with inventory witness",
            key,
        )
    assert fetch_one("SELECT quantity_remaining n FROM expired_holdings;")["n"] == 2
    assert fetch_one("SELECT disposal_method FROM expired_stock_movements WHERE action='disposal';")["disposal_method"] == method
    with pytest.raises(ValueError, match="exceeds"):
        returns.dispose_expired_stock(
            holding["holding_id"], d["inventory"], 3, "Waste collection", "Cannot exceed actual holding", str(uuid4())
        )


def test_partial_return_refunds_preserve_original_order_and_counts(lifecycle):
    d = lifecycle
    for _ in range(2):
        case = create_case(d)
        returns.review_product_return(case, d["sales"], True, "Approved after checking expiry")
        receive(d, case)
        assert returns.get_product_return(case, d["reseller"])["refund"]["amount"] == 200
        complete_refund(d, case)
        assert returns.get_product_return(case, d["reseller"])["status"] == "completed"
    assert (
        fetch_one("SELECT quantity_available n FROM inventory_batches WHERE batch_id=%s;", (d["batches"][0],))["n"]
        == 10
    )
    assert fetch_one("SELECT SUM(quantity_remaining) n FROM expired_holdings;")["n"] == 4
    orders = repositories.list_orders(reseller_account_id=d["reseller"])
    assert len(orders) == 1 and orders[0]["status"] == "fulfilled" and orders[0]["refunded_amount"] == 400
    assert repositories.count_orders(reseller_account_id=d["reseller"]) == 1


def test_receipt_requires_approval_and_full_verification(lifecycle):
    d = lifecycle
    case = create_case(d)
    with pytest.raises(ValueError, match="Sales approval"):
        receive(d, case)
    returns.review_product_return(case, d["sales"], True, "Approved after sales review")
    receive(d, case, 1)
    partial = returns.get_product_return(case, d["reseller"])
    assert partial["status"] == "awaiting_receipt" and partial["refund"] is None
    receive(d, case, 1)
    assert returns.get_product_return(case, d["reseller"])["refund"]["amount"] == 200


def test_discrepancy_reapproval_can_reduce_to_received_quantity(lifecycle):
    d = lifecycle
    case = create_case(d)
    returns.review_product_return(case, d["sales"], True, "Approved return request")
    receive(d, case, 1)
    returns.flag_return_discrepancy(case, d["inventory"], "Only one matching pack delivered")
    line = returns.get_product_return(case, d["sales"])["lines"][0]
    returns.review_product_return(
        case, d["sales"], True, "Approved corrected one pack claim", revised_quantities=[(line["return_line_id"], 1)], customer_message="Your corrected one-pack return is approved."
    )
    returns.verify_product_return(case,d["inventory"],"Verified corrected approved quantity",str(uuid4()))
    case_data = returns.get_product_return(case, d["reseller"])
    assert case_data["credit"] == 100 and case_data["refund"]["amount"] == 100


def test_concurrent_duplicate_claims_cannot_exceed_fulfilled_quantity(lifecycle):
    d = lifecycle

    def claim(_):
        try:
            return create_case(d, quantity=6)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, range(2)))
    assert sum(r is not None for r in results) == 1


@pytest.mark.parametrize("price,extra,refund", [(250, 50, 0), (150, 0, 50), (200, 0, 0)])
def test_substitute_replacement_settles_difference_and_releases_once(lifecycle, price, extra, refund):
    d = lifecycle
    product = substitute(price)
    case = create_case(d, "replace")
    returns.review_product_return(case, d["sales"], True, "Approved substitute product quote", [(product, 1)])
    receive(d, case)
    with pytest.raises(ValueError, match="acceptance"):
        returns.reserve_replacement(case, d["inventory"], "Reserve a replacement pack")
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved physical replacement stock")
    assert repositories.product_by_id(product)["available"] == 19
    assert next(p for p in repositories.list_inventory_items() if p["item_id"] == product)["available"] == 19
    if extra:
        with pytest.raises(ValueError, match="additional payment"):
            returns.release_replacement(case, d["inventory"], "Attempt before payment")
        proof, _ = _payment_submission(f"replacement-{case}")
        returns.record_return_payment(case, d["reseller"], proof)
        returns.verify_return_payment(
            case, d["sales"], f"TOPUP{uuid4().hex[:12]}", extra, "Payment screenshot and transfer matched"
        )
    returns.release_replacement(case, d["inventory"], "Replacement physically handed over")
    returns.release_replacement(case, d["inventory"], "Repeated release confirmation")
    if refund:
        assert returns.get_product_return(case, d["reseller"])["refund"]["amount"] == refund
        complete_refund(d, case)
    assert returns.get_product_return(case, d["reseller"])["status"] == "completed"
    assert repositories.product_by_id(product)["available"] == 19
    summary = returns.return_settlement_totals(d["sales"])
    assert summary["original_sales"] == 1000
    assert summary["net_collections"] == 1000 + extra - refund


def test_quote_revision_invalidates_acceptance_and_reservation(lifecycle):
    d = lifecycle
    case = create_case(d, "replace")
    returns.review_product_return(case, d["sales"], True, "Same product replacement approved")
    receive(d, case)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved two valid replacement packs")
    returns.review_product_return(case, d["sales"], True, "Revised substitute quote", [(substitute(250), 1)])
    case_data = returns.get_product_return(case, d["reseller"])
    assert (
        case_data["quote_revision"] == 2
        and case_data["accepted_revision"] is None
        and not case_data["has_reservations"]
    )


def test_expired_reservation_blocks_release_without_worker(lifecycle):
    d = lifecycle
    case = create_case(d, "replace")
    returns.review_product_return(case, d["sales"], True, "Replacement approved after review")
    receive(d, case)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved replacement packs")
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s;", (business_today(), d["batches"][1])
        )
    with pytest.raises(ValueError, match="expired"):
        returns.release_replacement(case, d["inventory"], "Must not release expired goods")
    worker.scan_batch_expiry()
    assert not returns.get_product_return(case, d["reseller"])["has_reservations"]


def test_unauthorized_reviews_and_private_attachments(lifecycle):
    d = lifecycle
    case = create_case(d)
    with pytest.raises(ValueError, match="cannot perform"):
        returns.review_product_return(case, d["inventory"], True, "Inventory cannot approve refunds")
    with pytest.raises(ValueError, match="cannot perform"):
        returns.flag_return_discrepancy(case, d["reseller"], "Reseller cannot verify inventory")


def test_request_and_approval_operation_keys_prevent_duplicate_cases_and_quotes(lifecycle):
    d = lifecycle
    key = str(uuid4())
    ids = [
        returns.create_product_return(
            d["order"],
            d["reseller"],
            "replace",
            [(d["allocation"], 2)],
            "Expired packs requiring replacement",
            attachments=[supporting_photo()], operation_key=key,
        )
        for _ in range(2)
    ]
    assert ids[0] == ids[1]
    review_key = str(uuid4())
    for _ in range(2):
        returns.review_product_return(
            ids[0], d["sales"], True, "Approved after checking batch", operation_key=review_key
        )
    assert returns.get_product_return(ids[0], d["reseller"])["quote_revision"] == 1


def test_expired_arrival_never_enters_sellable_stock_and_dates_are_preserved(lifecycle):
    d = lifecycle
    key = str(uuid4())
    for _ in range(2):
        returns.record_expired_arrival(
            d["product"],
            d["inventory"],
            "EXPIRED-ARRIVAL",
            business_today() - timedelta(days=2),
            3,
            "Expired freezer B",
            "Arrived already expired; segregated immediately",
            key,
        )
    assert repositories.product_by_id(d["product"])["available"] == 20
    assert fetch_one("SELECT SUM(quantity_remaining) n FROM expired_holdings;")["n"] == 3
    batch = repositories.add_product_batch(
        d["product"],
        "RECEIPT-DATED",
        4,
        business_today() + timedelta(days=10),
        received_date=business_today() - timedelta(days=1),
    )
    assert batch["received_date"] == business_today() - timedelta(days=1)
    with pytest.raises(ValueError, match="future"):
        repositories.add_product_batch(
            d["product"],
            "FUTURE-DATED",
            1,
            business_today() + timedelta(days=10),
            received_date=business_today() + timedelta(days=1),
        )


def test_near_expiry_setting_requires_at_least_fourteen_days(lifecycle):
    from app.repositories.superuser import update_business_settings

    with pytest.raises(ValueError, match="at least 14"):
        update_business_settings({"inventory.near_expiry_days": 13}, lifecycle["owner"], "Professor requires two weeks")


def test_legacy_batch_verification_records_evidence_without_moving_stock(lifecycle, monkeypatch):
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("SELECT order_item_id FROM order_items WHERE order_id=%s;", (d["order"],))
        line_id = cur.fetchone()["order_item_id"]
        cur.execute("DELETE FROM fulfillment_allocations WHERE order_item_id=%s;", (line_id,))
    session = {
        "role_key": "team-leader",
        "team_leader_role": "inventory",
        "account_id": d["inventory"],
        "portal_expires_at": int(business_now().timestamp()) + 600,
    }
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    with TestClient(main.app) as client:
        response = client.get("/portal/team-leader/returns")
        assert response.status_code == 200
        assert "Verified delivered batch<select" in response.text
        assert "TEST-1" in response.text
    returns.verify_legacy_allocation(
        d["order"], line_id, d["batches"][0], 10, d["inventory"], "Original delivery sheet identifies this batch"
    )
    assert (
        fetch_one("SELECT quantity_available n FROM inventory_batches WHERE batch_id=%s;", (d["batches"][0],))["n"]
        == 10
    )
    assert returns.returnable_allocations(d["order"], d["reseller"])[0]["returnable_quantity"] == 10


def test_expiry_flags_orders_when_other_reservations_exhaust_remaining_stock(strict_inventory_database):  # noqa: F811
    _, product, batches, orders = _seed_fulfillment(
        strict_inventory_database, batch_quantities=[5, 5], order_quantities=[4, 4]
    )
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s;", (business_today(), batches[0]))
        for order in orders:
            cur.execute(
                "INSERT INTO order_stock_reservations(order_id,product_id,quantity) VALUES(%s,%s,4);", (order, product)
            )
    worker.scan_batch_expiry()
    warnings = fetch_all("SELECT dedupe_key FROM notifications WHERE title='Order needs stock review';")
    assert len(warnings) == 2


@pytest.mark.parametrize("days", [15, 14, 2, 1, 0])
def test_expiry_alert_boundaries_are_deduplicated(lifecycle, days):
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE inventory_batches SET expiry_date=%s WHERE batch_id=%s;",
            (business_today() + timedelta(days=days), d["batches"][1]),
        )
    worker.scan_batch_expiry()
    worker.scan_batch_expiry()
    alerts = fetch_all(
        "SELECT alert_type,severity FROM alerts WHERE product_batch_id=%s AND status='open';", (d["batches"][1],)
    )
    assert len(alerts) == (0 if days == 15 else 1)
    if days <= 14:
        assert alerts[0]["alert_type"] == ("expired_batch" if days == 0 else "near_expiry")
        assert alerts[0]["severity"] == ("critical" if days <= 2 else "warning")


def test_new_portal_pages_render_with_scoped_sessions(lifecycle, monkeypatch):
    d = lifecycle
    case = create_case(d)
    returns.review_product_return(case, d["sales"], True, "Approved return for portal preview")
    receive(d, case)
    worker.scan_batch_expiry()
    for role, actor, specialization in [
        ("reseller", d["reseller"], None),
        ("team-leader", d["sales"], "sales"),
        ("team-leader", d["inventory"], "inventory"),
        ("sales-supervisor", d["supervisor"], "sales-supervisor"),
    ]:
        session = {
            "role_key": role,
            "account_id": actor,
            "team_leader_role": specialization,
            "portal_expires_at": int(business_now().timestamp()) + 600,
        }
        monkeypatch.setattr(web_sessions, "_load_session", lambda token, s=session: dict(s))
        monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
        monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
        paths = [f"/portal/{role}/returns?case={case}"]
        if specialization == "inventory":
            paths.extend(
                [
                    "/portal/team-leader/expired-stock",
                    f"/portal/team-leader/product-details?product_id={d['product']}",
                    "/portal/team-leader/finished-products",
                ]
            )
        with TestClient(main.app) as client:
            for path in paths:
                response = client.get(path, follow_redirects=False)
                if path.startswith(f"/portal/{role}/returns") and (role == "reseller" or specialization == "sales"):
                    assert response.status_code == 303
                    response = client.get(response.headers["location"])
                assert response.status_code == 200, response.text[:1000]
                assert "lifecycle" in response.text or "product-details?product_id=" in response.text
