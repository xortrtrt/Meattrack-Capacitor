"""Email verification must precede choosing a new portal password."""

from __future__ import annotations

import hashlib
import secrets

from app.config import OTP_REQUESTS_PER_HOUR
from app.database import get_transaction_cursor
from app.security import hash_password, validate_password_policy, verify_password
from app.web_sessions import revoke_account_sessions

from ._shared import _add_log_cursor


def request_password_verification(account_id: int, current_password: str) -> dict:
    code = f"{secrets.randbelow(1_000_000):06d}"
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT * FROM accounts WHERE account_id=%s AND is_active AND activation_status='active' FOR UPDATE",
            (account_id,),
        )
        account = cur.fetchone()
        if not account or not verify_password(current_password, account["password_hash"]):
            raise ValueError("Current password is incorrect.")
        cur.execute(
            "SELECT count(*) FILTER (WHERE created_at > now() - interval '60 seconds') AS recent, count(*) AS hourly FROM account_password_change_challenges WHERE account_id=%s AND created_at > now() - interval '1 hour'",
            (account_id,),
        )
        limits = cur.fetchone()
        if limits["recent"]:
            raise ValueError("Please wait 60 seconds before requesting another password OTP.")
        if limits["hourly"] >= OTP_REQUESTS_PER_HOUR:
            raise ValueError("OTP request limit reached. Try again later.")
        cur.execute(
            "UPDATE account_password_change_challenges SET consumed_at=now() WHERE account_id=%s AND consumed_at IS NULL",
            (account_id,),
        )
        cur.execute(
            "INSERT INTO account_password_change_challenges(account_id, otp_hash, original_password_hash) VALUES (%s,%s,%s) RETURNING challenge_id",
            (account_id, hash_password(code), account["password_hash"]),
        )
        challenge_id = cur.fetchone()["challenge_id"]
    return dict(challenge_id=challenge_id, otp_code=code, email=account["email"], name=account["name"])


def cancel_password_verification(account_id: int, challenge_id: int) -> None:
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE account_password_change_challenges SET consumed_at=now() WHERE account_id=%s AND challenge_id=%s AND consumed_at IS NULL",
            (account_id, challenge_id),
        )


def verify_password_challenge(account_id: int, challenge_id: int, code: str) -> str:
    grant = secrets.token_urlsafe(32)
    invalid = False
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT * FROM account_password_change_challenges WHERE challenge_id=%s AND account_id=%s AND consumed_at IS NULL AND verified_at IS NULL AND expires_at>now() AND attempt_count<5 FOR UPDATE",
            (challenge_id, account_id),
        )
        pending = cur.fetchone()
        if not pending:
            raise ValueError("OTP expired or unavailable. Request a new code.")
        if len(code) != 6 or not code.isascii() or not code.isdigit() or not verify_password(code, pending["otp_hash"]):
            cur.execute(
                "UPDATE account_password_change_challenges SET attempt_count=attempt_count+1 WHERE challenge_id=%s",
                (challenge_id,),
            )
            invalid = True
        else:
            cur.execute(
                "UPDATE account_password_change_challenges SET verified_at=now(), grant_hash=%s, expires_at=LEAST(expires_at, now()+interval '5 minutes') WHERE challenge_id=%s",
                (hashlib.sha256(grant.encode()).hexdigest(), challenge_id),
            )
    if invalid:
        raise ValueError("OTP is incorrect. After five attempts, request a new code.")
    return grant


def password_change_stage(account_id: int, challenge_id: int, grant: str) -> str:
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT verified_at, grant_hash FROM account_password_change_challenges WHERE account_id=%s AND challenge_id=%s AND consumed_at IS NULL AND expires_at>now() AND attempt_count<5",
            (account_id, challenge_id),
        )
        pending = cur.fetchone()
    if not pending:
        return "request"
    if pending["verified_at"]:
        return (
            "change"
            if grant and secrets.compare_digest(pending["grant_hash"], hashlib.sha256(grant.encode()).hexdigest())
            else "request"
        )
    return "verify"


def complete_verified_password_change(account_id: int, challenge_id: int, grant: str, new_password: str) -> None:
    password = validate_password_policy(new_password)
    with get_transaction_cursor() as cur:
        # Lock the account before its challenge, matching the request lock order.
        cur.execute(
            "SELECT password_hash FROM accounts WHERE account_id=%s AND is_active AND activation_status='active' FOR UPDATE",
            (account_id,),
        )
        account = cur.fetchone()
        cur.execute(
            "SELECT * FROM account_password_change_challenges WHERE account_id=%s AND challenge_id=%s AND consumed_at IS NULL AND verified_at IS NOT NULL AND expires_at>now() FOR UPDATE",
            (account_id, challenge_id),
        )
        pending = cur.fetchone()
        if (
            not account
            or not pending
            or not grant
            or not secrets.compare_digest(pending["grant_hash"], hashlib.sha256(grant.encode()).hexdigest())
        ):
            raise ValueError("Verify an email OTP before changing your password.")
        if account["password_hash"] != pending["original_password_hash"]:
            raise ValueError("Your password changed. Start verification again.")
        if verify_password(password, account["password_hash"]):
            raise ValueError("Choose a password different from your current password.")
        cur.execute("UPDATE accounts SET password_hash=%s WHERE account_id=%s", (hash_password(password), account_id))
        cur.execute(
            "UPDATE account_password_change_challenges SET consumed_at=now() WHERE account_id=%s AND consumed_at IS NULL",
            (account_id,),
        )
        cur.execute(
            "UPDATE account_password_otps SET consumed_at=now() WHERE account_id=%s AND consumed_at IS NULL",
            (account_id,),
        )
        cur.execute("DELETE FROM trusted_login_devices WHERE account_id=%s", (account_id,))
        _add_log_cursor(
            cur, actor_account_id=account_id, action="changed_password", entity_type="accounts", entity_id=account_id
        )
    revoke_account_sessions(account_id)
