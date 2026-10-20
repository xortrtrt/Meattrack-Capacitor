from decimal import Decimal
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData
from starlette.requests import Request

from app import inventory_measurements as measurements, main, owner_recovery, repositories, web_sessions
from app.portals import shared


def request(path="/portal/owner/products", query="", session=None):
    return Request({"type": "http", "scheme": "http", "method": "POST", "path": path,
                    "server": ("testserver", 80), "headers": [], "query_string": query.encode(),
                    "session": session if session is not None else {"account_id": 1, "role_key": "owner"}})


@pytest.mark.parametrize("value", ["1" + "0" * 29, "1000000000", "NaN", "Infinity", "1.0001", "1e3"])
def test_stock_parser_rejects_invalid_or_out_of_capacity_values(value):
    with pytest.raises(ValueError):
        measurements.parse_decimal_exact(value, "Stock")


def test_numeric_capacity_boundaries_and_derived_values():
    assert measurements.parse_whole_packs("999999999") == Decimal("999999999.000")
    assert measurements.parse_decimal_exact("9999999999.99", "Price", scale=2) == measurements.MAX_MONEY
    for value in ("1000000000", "1" + "0" * 40):
        with pytest.raises(ValueError):
            measurements.parse_whole_packs(value)
    with pytest.raises(ValueError):
        measurements.require_capacity(measurements.MAX_STOCK + Decimal("0.001"), "Resulting balance")


@pytest.mark.parametrize("value", ["²", "-1", "0", "", "1e2", "9223372036854775808", "1" * 5000])
def test_optional_ids_fail_safely(value):
    assert shared.optional_positive_id(value) is None


@pytest.mark.parametrize("page,expected", [("50", 50), ("51", 51), ("201", 201), ("999", 201), ("²", 1), ("-1", 1)])
def test_orders_page_counts_and_clamps_before_fetch(monkeypatch, page, expected):
    calls = []
    monkeypatch.setattr(repositories, "count_orders", lambda **kw: 1201)
    monkeypatch.setattr(repositories, "list_orders", lambda **kw: calls.append(kw) or [{"order_id": expected}])
    result = shared.orders_page(request(query="page=" + page), "reseller", page_size=6)
    assert calls[0]["page"] == expected
    assert result["pagination"]["page"] == expected


def test_owner_recovery_is_allowlisted_bound_and_one_use():
    req = request()
    req._form = FormData({"product_id": "7", "base_price": "129.75", "reason": "Review product pricing", "password": "secret", "otp_code": "123456", "csrf_token": "secret", "message": "private", "payment_amount": "99", "_owner_return_to": "/portal/owner/products?q=bacon&page=2"})
    owner_recovery.capture(req, "products")
    draft = req.session[owner_recovery.SESSION_KEY]
    assert draft["fields"] == {"product_id": "7", "base_price": "129.75", "reason": "Review product pricing"}
    assert owner_recovery.consume(req) is None
    assert owner_recovery.complete(req) == "/portal/owner/products?q=bacon&page=2"
    assert owner_recovery.consume(req)["fields"]["base_price"] == "129.75"
    assert owner_recovery.consume(req) is None


@pytest.mark.parametrize("target", ["https://evil.test/portal/owner/products", "//evil.test/portal/owner/products", "/portal/reseller/profile", "/portal/owner/system", "/portal/owner/unknown", "http://["])
def test_unsafe_resume_targets_are_rejected(target):
    assert owner_recovery.safe_destination(request(), target) == "/portal/owner/security"


def test_owner_recovery_expiry_account_change_and_oversized_draft():
    req = request()
    owner_recovery.capture(req, "products", {"reason": "x" * 9000})
    assert req.session[owner_recovery.SESSION_KEY]["fields"] == {}
    assert req.session[owner_recovery.SESSION_KEY]["discarded"]
    owner_recovery.capture(req, "products", {"base_price": "9"})
    req.session[owner_recovery.SESSION_KEY]["created_at"] -= 901
    assert owner_recovery.complete(req) == "/portal/owner/security"
    assert owner_recovery.SESSION_KEY not in req.session
    owner_recovery.capture(req, "products", {"base_price": "9"})
    req.session["account_id"] = 2
    assert owner_recovery.consume(req) is None
    assert owner_recovery.SESSION_KEY not in req.session


def test_step_up_http_captures_form_and_resumes_without_post_replay(monkeypatch):
    state = {"account_id": 1, "role_key": "owner"}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(state))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, seconds: state.update(data))
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)
    monkeypatch.setattr(repositories, "record_owner_audit", lambda *a, **kw: None)
    monkeypatch.setattr(repositories, "confirm_owner_step_up", lambda *a: None)
    writes = []
    monkeypatch.setattr(repositories, "update_product_price", lambda *a, **kw: writes.append(a))
    client = TestClient(main.app)
    response = client.post("/portal/owner/products", data={"product_id": "7", "base_price": "129.75", "reason": "Review price change", "_owner_return_to": "/portal/owner/products?q=bacon&page=2"}, follow_redirects=False)
    assert response.status_code == 303
    assert state[owner_recovery.SESSION_KEY]["fields"]["base_price"] == "129.75"
    state["owner_step_up_challenge_id"] = 1
    response = client.post("/portal/owner/security/step-up/confirm", data={"otp_code": "123456"}, follow_redirects=False)
    parsed = urlsplit(response.headers["location"])
    assert parsed.path == "/portal/owner/products"
    assert parse_qs(parsed.query)["q"] == ["bacon"]
    assert parse_qs(parsed.query)["page"] == ["2"]
    assert not writes


def test_recovery_retains_context_through_wrong_otp_and_resend(monkeypatch):
    from app.portals import owner

    state = {"account_id": 1, "role_key": "owner", "owner_step_up_challenge_id": 1}
    req = request(session=state)
    owner_recovery.capture(req, "products", {"product_id": "7", "reason": "Preserve this draft"})
    original = dict(state[owner_recovery.SESSION_KEY])
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(state))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, seconds: state.update(data))
    monkeypatch.setattr(shared, "require_portal_session", lambda *args: None)

    def wrong_code(*args):
        raise ValueError("Incorrect verification code.")

    monkeypatch.setattr(repositories, "confirm_owner_step_up", wrong_code)
    monkeypatch.setattr(repositories, "request_owner_step_up", lambda *args: {"challenge_id": 2, "email": "owner@test.invalid", "name": "Owner", "otp_code": "123456"})
    monkeypatch.setattr(owner, "send_owner_step_up_otp", lambda **kw: (True, "sent"))
    client = TestClient(main.app)
    assert client.post("/portal/owner/security/step-up/confirm", data={"otp_code": "000000"}, follow_redirects=False).status_code == 303
    assert state[owner_recovery.SESSION_KEY] == original
    assert client.post("/portal/owner/security/step-up/request", follow_redirects=False).status_code == 303
    assert state[owner_recovery.SESSION_KEY] == original
    assert state["owner_step_up_challenge_id"] == 2


def test_recovery_bounds_destination_and_preserves_unchecked_setting():
    req = request(path="/portal/owner/settings")
    target = "/portal/owner/settings?" + "&".join("q=" + "x" * 500 for _ in range(100))
    owner_recovery.capture(req, "settings", {"_owner_return_to": target, "business_hours": "Monday–Friday"})
    draft = req.session[owner_recovery.SESSION_KEY]
    assert draft["fields"]["owner_critical_alerts"] == ""
    assert len(parse_qs(urlsplit(draft["return_to"]).query)["q"]) == 1
    assert len(json.dumps(draft).encode("utf-8")) <= owner_recovery.MAX_BYTES


@pytest.mark.parametrize("path", ["/portal/owner/forecasts?run_id=%C2%B2", "/portal/owner/conversations?leader=%C2%B2"])
def test_invalid_query_ids_do_not_raise(monkeypatch, path):
    from test_portal_context import install_read_spies
    install_read_spies(monkeypatch)
    monkeypatch.setattr(shared, "require_portal_session", lambda *a: None)
    monkeypatch.setattr(repositories, "count_conversation_metadata", lambda **kw: 0)
    monkeypatch.setattr(repositories, "list_conversation_metadata", lambda **kw: [])
    assert TestClient(main.app).get(path).status_code == 200
