import asyncio
import json
from urllib.parse import unquote_plus

import pytest
from starlette.requests import Request

from app import emailer, main
from app.database import fetch_one, get_transaction_cursor
from app.portals import password_change as flow
from app.repositories import accounts, password_changes as data
from app.security import hash_password
from test_inventory_postgres import strict_inventory_database  # noqa: F401

ROLES = [
    ("owner", None, "owner"),
    ("reseller", None, "reseller"),
    ("team_leader", "sales", "team-leader"),
    ("team_leader", "inventory", "team-leader"),
    ("team_leader", "sales-supervisor", "sales-supervisor"),
]


@pytest.fixture
def account(request):
    request.getfixturevalue("strict_inventory_database")

    def create(kind="owner", team=None):
        with get_transaction_cursor() as cur:
            cur.execute(
                "INSERT INTO accounts(account_type,team_leader_role,name,email,password_hash) VALUES(%s,%s,'Test User','password@flow.test',%s) RETURNING account_id",
                (kind, team, hash_password("Original-Password1!")),
            )
            return cur.fetchone()["account_id"]

    return create


@pytest.mark.postgres
@pytest.mark.parametrize("kind,team,role", ROLES)
def test_otp_precedes_password_entry_and_save(account, monkeypatch, kind, team, role):
    account_id = account(kind, team)
    request = Request({"type": "http", "session": {"account_id": account_id, "role_key": role}})
    monkeypatch.setattr(flow, "guard", lambda *args: None)
    deliveries = []

    class Accepted:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"messageId":"local-test"}'

    monkeypatch.setattr(emailer, "BREVO_API_KEY", "test-key")
    monkeypatch.setattr(emailer, "BREVO_FROM_EMAIL", "sender@flow.test")
    monkeypatch.setattr(
        emailer.request, "urlopen", lambda req, **kwargs: deliveries.append(json.loads(req.data)) or Accepted()
    )
    assert flow.context(request)["password_change_stage"] == "request"
    denied = asyncio.run(flow.save_password(request, role, "Updated-Password2!", "Updated-Password2!"))
    assert "Verify an email OTP" in unquote_plus(denied.headers["location"])
    result = asyncio.run(flow.request_code(request, role, "Original-Password1!"))
    assert "OTP sent" in unquote_plus(result.headers["location"])
    assert len(deliveries) == 1
    assert deliveries[0]["to"] == [{"email": "password@flow.test"}]
    code = next(line for line in deliveries[0]["textContent"].splitlines() if len(line) == 6 and line.isdigit())
    pending = flow.state(request)
    assert flow.context(request)["password_change_stage"] == "verify"
    assert accounts.authenticate_account("password@flow.test", "Original-Password1!")
    denied = asyncio.run(flow.save_password(request, role, "Updated-Password2!", "Updated-Password2!"))
    assert "Verify an email OTP" in unquote_plus(denied.headers["location"])
    asyncio.run(flow.verify_code(request, role, code))
    assert flow.context(request)["password_change_stage"] == "change"
    grant = flow.state(request)["grant"]
    mismatch = asyncio.run(flow.save_password(request, role, "Updated-Password2!", "Different-Password2!"))
    assert "do not match" in unquote_plus(mismatch.headers["location"])
    saved = asyncio.run(flow.save_password(request, role, "Updated-Password2!", "Updated-Password2!"))
    assert "/login?" in saved.headers["location"]
    assert request.session == {}
    assert accounts.authenticate_account("password@flow.test", "Original-Password1!") is None
    assert accounts.authenticate_account("password@flow.test", "Updated-Password2!")
    with pytest.raises(ValueError):
        data.complete_verified_password_change(account_id, pending["challenge_id"], grant, "Third-Password3!")


@pytest.mark.postgres
def test_email_failure_does_not_allow_verification(account, monkeypatch):
    account_id = account()
    request = Request({"type": "http", "session": {"account_id": account_id}})
    monkeypatch.setattr(flow, "guard", lambda *args: None)
    monkeypatch.setattr(flow.shared, "send_password_change_otp", lambda **kwargs: (False, "Email unavailable"))
    result = asyncio.run(flow.request_code(request, "owner", "Original-Password1!"))
    assert "Email unavailable" in unquote_plus(result.headers["location"])
    assert flow.context(request)["password_change_stage"] == "request"
    assert fetch_one("SELECT consumed_at FROM account_password_change_challenges WHERE account_id=%s", (account_id,))[
        "consumed_at"
    ]


@pytest.mark.postgres
def test_wrong_expired_cross_account_and_resend_protection(account):
    account_id = account()
    pending = data.request_password_verification(account_id, "Original-Password1!")
    challenge_id = pending["challenge_id"]
    with pytest.raises(ValueError, match="60 seconds"):
        data.request_password_verification(account_id, "Original-Password1!")
    with pytest.raises(ValueError):
        data.verify_password_challenge(account_id + 1, challenge_id, pending["otp_code"])
    wrong = "000000" if pending["otp_code"] != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(ValueError, match="incorrect"):
            data.verify_password_challenge(account_id, challenge_id, wrong)
    assert (
        fetch_one(
            "SELECT attempt_count FROM account_password_change_challenges WHERE challenge_id=%s", (challenge_id,)
        )["attempt_count"]
        == 5
    )
    with pytest.raises(ValueError, match="expired or unavailable"):
        data.verify_password_challenge(account_id, challenge_id, pending["otp_code"])
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE account_password_change_challenges SET created_at=now()-interval '2 minutes' WHERE challenge_id=%s",
            (challenge_id,),
        )
    pending = data.request_password_verification(account_id, "Original-Password1!")
    grant = data.verify_password_challenge(account_id, pending["challenge_id"], pending["otp_code"])
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE account_password_change_challenges SET expires_at=now()-interval '1 second' WHERE challenge_id=%s",
            (pending["challenge_id"],),
        )
    with pytest.raises(ValueError):
        data.complete_verified_password_change(account_id, pending["challenge_id"], grant, "Updated-Password2!")


def test_only_shared_password_routes_are_registered():
    paths = [path for path in main.app.openapi()["paths"] if "/profile/password" in path]
    assert sorted(paths) == sorted(
        "/portal/{role_key}/profile/password" + suffix for suffix in ["", "/confirm", "/save", "/cancel"]
    )


@pytest.mark.postgres
def test_resend_cancel_weak_password_and_grant_tampering(account):
    account_id = account()
    pending = data.request_password_verification(account_id, "Original-Password1!")
    challenge_id = pending["challenge_id"]
    grant = data.verify_password_challenge(account_id, challenge_id, pending["otp_code"])
    for new_password, token in [("weak", grant), ("Original-Password1!", grant), ("Updated-Password2!", "tampered")]:
        with pytest.raises(ValueError):
            data.complete_verified_password_change(account_id, challenge_id, token, new_password)
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE account_password_change_challenges SET created_at=now()-interval '2 minutes' WHERE challenge_id=%s", (challenge_id,))
    replacement = data.request_password_verification(account_id, "Original-Password1!")
    with pytest.raises(ValueError):
        data.complete_verified_password_change(account_id, challenge_id, grant, "Updated-Password2!")
    replacement_grant = data.verify_password_challenge(account_id, replacement['challenge_id'], replacement['otp_code'])
    data.cancel_password_verification(account_id, replacement['challenge_id'])
    with pytest.raises(ValueError):
        data.complete_verified_password_change(account_id, replacement['challenge_id'], replacement_grant, "Updated-Password2!")
    assert accounts.authenticate_account("password@flow.test", "Original-Password1!")


@pytest.mark.postgres
def test_hourly_password_otp_limit_is_enforced(account, monkeypatch):
    account_id = account()
    pending = data.request_password_verification(account_id, "Original-Password1!")
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE account_password_change_challenges SET created_at=now()-interval '2 minutes' WHERE challenge_id=%s", (pending['challenge_id'],))
    monkeypatch.setattr(data, 'OTP_REQUESTS_PER_HOUR', 1)
    with pytest.raises(ValueError, match='request limit'):
        data.request_password_verification(account_id, "Original-Password1!")
