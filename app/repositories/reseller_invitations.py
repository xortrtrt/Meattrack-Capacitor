from __future__ import annotations

import hashlib

from app.security import hash_password, validate_password_policy

from ._shared import *


INVITATION_TTL_DAYS = 7


def create_reseller_registration_invitation(email: str, owner_account_id: int) -> dict:
    invited_email = email.strip().lower()
    if not invited_email or "@" not in invited_email:
        raise ValueError("Enter a valid email address.")
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s));", (f"reseller-registration:{invited_email}",))
        cur.execute(
            "SELECT 1 FROM accounts WHERE account_id=%s AND account_type='owner' AND is_active=true AND activation_status='active' FOR UPDATE;",
            (owner_account_id,),
        )
        if not cur.fetchone():
            raise ValueError("Only an active Business Owner can issue reseller invitations.")
        cur.execute("SELECT account_id FROM accounts WHERE lower(email)=lower(%s) LIMIT 1;", (invited_email,))
        if cur.fetchone():
            raise ValueError("An account already uses that email address.")
        cur.execute(
            "SELECT invitation_id FROM reseller_registration_invitations WHERE lower(invited_email)=lower(%s) AND consumed_at IS NULL AND registration_account_id IS NOT NULL LIMIT 1 FOR UPDATE;",
            (invited_email,),
        )
        if cur.fetchone():
            raise ValueError("A registration is already in progress for that email address.")
        cur.execute(
            "UPDATE reseller_registration_invitations SET consumed_at=now() WHERE lower(invited_email)=lower(%s) AND consumed_at IS NULL AND registration_account_id IS NULL;",
            (invited_email,),
        )
        cur.execute(
            """
            INSERT INTO reseller_registration_invitations
                (invited_by_account_id, invited_email, token_hash, expires_at)
            VALUES (%s, %s, %s, now() + make_interval(days => %s))
            RETURNING invitation_id;
            """,
            (owner_account_id, invited_email, token_hash, INVITATION_TTL_DAYS),
        )
        invitation_id = int(cur.fetchone()["invitation_id"])
        _add_log_cursor(
            cur,
            actor_account_id=owner_account_id,
            action="invited_reseller_registration",
            entity_type="reseller_registration_invitations",
            entity_id=invitation_id,
        )
    return {"invitation_id": invitation_id, "email": invited_email, "token": raw_token}


def cancel_reseller_registration_invitation(invitation_id: int, owner_account_id: int) -> None:
    with get_transaction_cursor() as cur:
        cur.execute(
            "UPDATE reseller_registration_invitations SET consumed_at=now() WHERE invitation_id=%s AND invited_by_account_id=%s AND consumed_at IS NULL AND registration_account_id IS NULL;",
            (invitation_id, owner_account_id),
        )


def _invitation_by_token_cursor(cur, raw_token: str, *, for_update: bool = False) -> dict | None:
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    cur.execute(
        """
        SELECT i.invitation_id, i.invited_email, i.invited_by_account_id, i.expires_at,
               i.registration_account_id, i.consumed_at,
               a.activation_status AS registration_status, a.is_active AS registration_active
        FROM reseller_registration_invitations i
        LEFT JOIN accounts a ON a.account_id=i.registration_account_id
        WHERE i.token_hash=%s AND i.consumed_at IS NULL
          AND ((i.registration_account_id IS NULL AND i.expires_at >= now())
               OR (i.registration_account_id IS NOT NULL AND a.activation_status='pending' AND a.is_active=false))
        """ + (" FOR UPDATE OF i" if for_update else ""),
        (token_hash,),
    )
    return cur.fetchone()


def reseller_registration_invitation(raw_token: str) -> dict:
    if not raw_token.strip():
        raise ValueError("This registration invitation is invalid or expired.")
    invitation = fetch_one(
        """
        SELECT i.invitation_id, i.invited_email, i.expires_at, i.registration_account_id,
               a.activation_status AS registration_status, a.is_active AS registration_active
        FROM reseller_registration_invitations i
        LEFT JOIN accounts a ON a.account_id=i.registration_account_id
        WHERE i.token_hash=%s AND i.consumed_at IS NULL
          AND ((i.registration_account_id IS NULL AND i.expires_at >= now())
               OR (i.registration_account_id IS NOT NULL AND a.activation_status='pending' AND a.is_active=false))
        LIMIT 1;
        """,
        (hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest(),),
    )
    if not invitation:
        raise ValueError("This registration invitation is invalid, expired, or already used.")
    return clean_row(invitation)


def reseller_registration_for_account(account_id: int) -> dict:
    invitation = fetch_one(
        """
        SELECT i.invitation_id, i.invited_email, i.expires_at, i.registration_account_id,
               a.activation_status AS registration_status, a.is_active AS registration_active
        FROM reseller_registration_invitations i
        JOIN accounts a ON a.account_id=i.registration_account_id
        WHERE i.registration_account_id=%s AND i.consumed_at IS NULL
          AND a.account_type='reseller' AND a.activation_status='pending' AND a.is_active=false
        LIMIT 1;
        """,
        (account_id,),
    )
    if not invitation:
        raise ValueError("No pending reseller registration was found for this session.")
    return clean_row(invitation)


def begin_invited_reseller_registration(
    raw_token: str,
    *,
    full_name: str,
    business_name: str,
    contact_number: str,
    address: str,
    password: str,
    privacy_notice_version: str,
    terms_version: str,
) -> dict:
    name = " ".join(full_name.strip().split())
    business = " ".join(business_name.strip().split())
    phone = " ".join(contact_number.strip().split())
    location = " ".join(address.strip().split())
    if len(name) < 2 or len(name) > 120:
        raise ValueError("Enter your name using 2 to 120 characters.")
    if len(business) < 2 or len(business) > 160:
        raise ValueError("Enter your business name using 2 to 160 characters.")
    if not phone or len(phone) > 40:
        raise ValueError("Enter a valid contact number using up to 40 characters.")
    if len(location) > 500:
        raise ValueError("Keep your address under 500 characters.")
    if not privacy_notice_version.strip() or not terms_version.strip():
        raise ValueError("The policy versions are missing. Reload your invitation and try again.")
    password_hash = hash_password(validate_password_policy(password))
    with get_transaction_cursor() as cur:
        invitation = _invitation_by_token_cursor(cur, raw_token, for_update=True)
        if not invitation:
            raise ValueError("This registration invitation is invalid, expired, or already used.")
        if invitation["registration_account_id"]:
            raise ValueError("Registration was already started. Enter the OTP sent to the invited email.")
        cur.execute(
            "SELECT account_id FROM accounts WHERE lower(email)=lower(%s) LIMIT 1;",
            (invitation["invited_email"],),
        )
        if cur.fetchone():
            raise ValueError("An account already uses the invited email address. Contact the Business Owner.")
        cur.execute(
            """
            INSERT INTO resellers (business_name, contact_person, email, contact_number, address, reseller_status)
            VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING reseller_id;
            """,
            (business, name, invitation["invited_email"], phone, location or None),
        )
        reseller_id = int(cur.fetchone()["reseller_id"])
        cur.execute(
            """
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active, activation_status)
            VALUES ('reseller', %s, %s, %s, %s, false, 'pending')
            RETURNING account_id, account_type, team_leader_role, name, email;
            """,
            (reseller_id, name, invitation["invited_email"], password_hash),
        )
        account = cur.fetchone()
        cur.executemany(
            """
            INSERT INTO account_policy_acceptances
                (account_id, policy_type, policy_version, acceptance_source)
            VALUES (%s, %s, %s, 'reseller_registration');
            """,
            [
                (account["account_id"], "privacy_notice", privacy_notice_version),
                (account["account_id"], "terms", terms_version),
            ],
        )
        cur.execute(
            "UPDATE reseller_registration_invitations SET registration_account_id=%s WHERE invitation_id=%s AND registration_account_id IS NULL;",
            (account["account_id"], invitation["invitation_id"]),
        )
        if cur.rowcount != 1:
            raise ValueError("This registration invitation has already been used.")
        _add_log_cursor(
            cur, actor_account_id=invitation["invited_by_account_id"], action="started_invited_reseller_registration",
            entity_type="accounts", entity_id=account["account_id"],
        )
    clean = clean_row(account)
    clean["role_key"] = "reseller"
    return clean


__all__ = [
    "create_reseller_registration_invitation",
    "cancel_reseller_registration_invitation",
    "reseller_registration_invitation",
    "reseller_registration_for_account",
    "begin_invited_reseller_registration",
]

