from __future__ import annotations

from app.portal_config import team_leader_role_labels

from ._shared import *


def list_accounts(
    q: str = "",
    account_type: str = "",
    page: int | None = None,
    page_size: int = 10,
    sort: str = "",
    account_types: tuple[str, ...] | None = None,
) -> list[dict]:
    where = []
    params: list[object] = []
    q = q.strip()
    account_type = account_type.strip()
    if q:
        where.append("(a.name ILIKE %s OR a.email ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if account_type:
        where.append("a.account_type = %s")
        params.append(account_type)
    elif account_types:
        placeholders = ", ".join(["%s"] * len(account_types))
        where.append(f"a.account_type IN ({placeholders})")
        params.extend(account_types)
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    paging_sql = ""
    if page is not None:
        paging_sql = " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    order_sql = safe_sort_sql(sort, ACCOUNT_SORTS, ACCOUNT_SORTS["oldest"])
    return clean_row(fetch_all("""
        SELECT a.account_id, a.account_type, a.team_leader_role, a.reseller_id, a.name, a.email,
               a.activation_status,
               (CASE WHEN a.activation_status = 'pending' THEN 'pending activation'
                     WHEN a.is_active THEN 'active' ELSE 'inactive' END) AS status,
               r.team_leader_account_id,
               tl.name AS team_leader_name
        FROM accounts a
        LEFT JOIN resellers r ON r.reseller_id = a.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        {where_sql}
        ORDER BY {order_sql}
        {paging_sql};
    """.format(where_sql=where_sql, order_sql=order_sql, paging_sql=paging_sql), tuple(params) or None))


def count_accounts(q: str = "", account_type: str = "", account_types: tuple[str, ...] | None = None) -> int:
    query = "SELECT COUNT(*) AS total FROM accounts a"
    where = []
    params: list[object] = []
    q = q.strip()
    account_type = account_type.strip()
    if q:
        where.append("(a.name ILIKE %s OR a.email ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if account_type:
        where.append("a.account_type = %s")
        params.append(account_type)
    elif account_types:
        placeholders = ", ".join(["%s"] * len(account_types))
        where.append(f"a.account_type IN ({placeholders})")
        params.extend(account_types)
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def list_team_leader_accounts() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT account_id, name, email, team_leader_role
        FROM accounts
        WHERE account_type = 'team_leader'
          AND is_active = true
          AND team_leader_role = 'sales'
        ORDER BY name, account_id;
    """))


def next_sales_team_leader() -> dict | None:
    leaders = list_team_leader_accounts()
    if not leaders:
        return None
    row = fetch_one("""
        SELECT COUNT(*) AS total
        FROM inquiries
        WHERE assigned_team_leader_account_id IS NOT NULL;
    """)
    index = int(row["total"] if row else 0) % len(leaders)
    return leaders[index]


def list_all_team_leader_accounts() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT account_id, name, email, team_leader_role
        FROM accounts
        WHERE account_type = 'team_leader'
          AND is_active = true
        ORDER BY team_leader_role, name, account_id;
    """))


def list_reseller_assignments() -> list[dict]:
    return clean_row(fetch_all("""
        SELECT r.reseller_id,
               r.business_name,
               r.contact_person,
               r.email,
               r.reseller_status,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               tl.email AS team_leader_email,
               a.account_id AS account_id
        FROM resellers r
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        LEFT JOIN accounts a ON a.reseller_id = r.reseller_id
        ORDER BY r.business_name, r.reseller_id;
    """))


def set_reseller_team_leader(reseller_id: int, team_leader_account_id: int, actor_account_id: int | None = None) -> dict:
    with get_transaction_cursor() as cur:
        cur.execute("""
            SELECT account_id, name FROM accounts
            WHERE account_id = %s AND account_type = 'team_leader' AND is_active = true
              AND team_leader_role = 'sales' LIMIT 1;
        """, (team_leader_account_id,))
        leader = cur.fetchone()
        if not leader:
            raise ValueError("Select an active sales team leader.")
        cur.execute("""
            UPDATE resellers SET team_leader_account_id = %s WHERE reseller_id = %s
            RETURNING reseller_id, business_name, team_leader_account_id;
        """, (team_leader_account_id, reseller_id))
        reseller = cur.fetchone()
        if not reseller:
            raise ValueError("Reseller was not found.")
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="assigned_reseller_team_leader", entity_type="resellers", entity_id=reseller_id)
    return clean_row(reseller)


def update_team_leader_account_role(account_id: int, team_leader_role: str, actor_account_id: int | None = None) -> dict:
    if team_leader_role not in {"sales", "sales-supervisor", "inventory"}:
        raise ValueError("Select a valid team leader role.")
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE accounts
            SET team_leader_role = %s
            WHERE account_id = %s
              AND account_type = 'team_leader'
            RETURNING account_id, account_type, team_leader_role, name, email, activation_status;
            """,
            (team_leader_role, account_id),
        )
        account = cur.fetchone()
        if not account:
            raise ValueError("Only team leader accounts can change team leader roles.")
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="updated_team_leader_role", entity_type="accounts", entity_id=account_id)
    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    return clean


def role_key_for_account_type(account_type: str, team_leader_role: str | None = None) -> str | None:
    if account_type == "team_leader":
        if team_leader_role == "sales-supervisor":
            return "sales-supervisor"
        return "team-leader"
    if account_type in {"owner", "reseller"}:
        return account_type
    return None


def authenticate_account(email: str, password: str) -> dict | None:
    account = fetch_one(
        """
        SELECT account_id, account_type, team_leader_role, name, email, password_hash,
               profile_image_path, profile_image_position_x, profile_image_position_y, profile_image_scale,
               is_active, activation_status
        FROM accounts
        WHERE lower(email) = lower(%s)
        LIMIT 1;
        """,
        (email,),
    )
    if account is None or not account["is_active"] or account["activation_status"] != "active":
        return None

    if not verify_password(password, account["password_hash"]):
        return None

    if password_needs_rehash(account["password_hash"]):
        execute_write(
            "UPDATE accounts SET password_hash = %s WHERE account_id = %s;",
            (hash_password(password), account["account_id"]),
        )

    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    return clean if clean["role_key"] else None


def change_account_password(account_id: int, current_password: str, new_password: str) -> None:
    account = fetch_one(
        """
        SELECT account_id, name, password_hash
        FROM accounts
        WHERE account_id = %s
          AND account_type = 'reseller'
          AND is_active = true
        LIMIT 1;
        """,
        (account_id,),
    )
    if not account:
        raise ValueError("Reseller account was not found.")
    if not verify_password(current_password, account["password_hash"]):
        raise ValueError("Current password is incorrect.")
    cleaned_password = validate_password_policy(new_password)
    execute_write(
        "UPDATE accounts SET password_hash = %s WHERE account_id = %s;",
        (hash_password(cleaned_password), account_id),
    )
    add_log(account["name"], "changed_password", "Reseller profile")


def request_account_password_change(
    account_id: int,
    current_password: str,
    new_password: str,
    allowed_account_types: tuple[str, ...] = ("owner", "reseller", "team_leader"),
) -> dict:
    enforce_otp_resend(account_id)
    account = fetch_one(
        """
        SELECT account_id, account_type, name, email, password_hash
        FROM accounts
        WHERE account_id = %s
          AND is_active = true
        LIMIT 1;
        """,
        (account_id,),
    )
    if not account or account["account_type"] not in allowed_account_types:
        raise ValueError("Account was not found.")
    if not verify_password(current_password, account["password_hash"]):
        raise ValueError("Current password is incorrect.")
    cleaned_password = validate_password_policy(new_password)

    otp_code = f"{secrets.randbelow(1_000_000):06d}"
    pending_password_hash = hash_password(cleaned_password)
    otp_hash = hash_password(otp_code)
    expires_at = business_now() + timedelta(minutes=10)

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE account_password_otps
            SET consumed_at = now()
            WHERE account_id = %s
              AND consumed_at IS NULL;
            """,
            (account_id,),
        )
        cur.execute(
            """
            INSERT INTO account_password_otps (account_id, otp_hash, pending_password_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING account_password_otp_id;
            """,
            (account_id, otp_hash, pending_password_hash, expires_at),
        )
        otp = cur.fetchone()

    record_otp_resend(account_id)
    record_event("otp_verify", f"account:{account_id}", succeeded=True)
    profile_label = {
        "team_leader": "Team leader profile",
        "reseller": "Reseller profile",
        "owner": "Owner profile",
    }.get(account["account_type"], "Account profile")
    add_log(account["name"], "requested_password_otp", profile_label)
    return {
        "otp_id": otp["account_password_otp_id"],
        "account_id": account["account_id"],
        "account_type": account["account_type"],
        "name": account["name"],
        "email": account["email"],
        "otp_code": otp_code,
    }


def request_reseller_password_change(account_id: int, current_password: str, new_password: str) -> dict:
    return request_account_password_change(account_id, current_password, new_password, ("reseller",))


def cancel_reseller_password_change(account_id: int, otp_id: int | None = None) -> None:
    params: list[object] = [account_id]
    where = "account_id = %s AND consumed_at IS NULL"
    if otp_id is not None:
        where += " AND account_password_otp_id = %s"
        params.append(otp_id)
    execute_write(f"UPDATE account_password_otps SET consumed_at = now() WHERE {where};", tuple(params))


def cancel_account_password_change(account_id: int, otp_id: int | None = None) -> None:
    cancel_reseller_password_change(account_id, otp_id)


def confirm_account_password_change(
    account_id: int,
    otp_code: str,
    allowed_account_types: tuple[str, ...] = ("owner", "reseller", "team_leader"),
) -> None:
    enforce_otp_attempt(account_id)
    cleaned_otp = "".join(ch for ch in otp_code.strip() if ch.isdigit())
    if len(cleaned_otp) != 6:
        raise ValueError("Enter the 6-digit OTP sent to your email.")

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT p.account_password_otp_id, p.otp_hash, p.pending_password_hash, a.name, a.account_type
            FROM account_password_otps p
            JOIN accounts a ON a.account_id = p.account_id
            WHERE p.account_id = %s
              AND p.consumed_at IS NULL
              AND p.expires_at >= now()
              AND a.is_active = true
            ORDER BY p.created_at DESC
            LIMIT 1
            FOR UPDATE OF p;
            """,
            (account_id,),
        )
        pending = cur.fetchone()
        if not pending:
            raise ValueError("OTP expired or no password change request is pending.")
        if pending["account_type"] not in allowed_account_types:
            raise ValueError("Account was not found.")
        if not verify_password(cleaned_otp, pending["otp_hash"]):
            record_event("otp_verify", f"account:{account_id}")
            raise ValueError("OTP is incorrect.")
        cur.execute(
            "UPDATE accounts SET password_hash = %s WHERE account_id = %s;",
            (pending["pending_password_hash"], account_id),
        )
        cur.execute(
            "UPDATE account_password_otps SET consumed_at = now() WHERE account_password_otp_id = %s;",
            (pending["account_password_otp_id"],),
        )

    record_event("otp_verify", f"account:{account_id}", succeeded=True)
    profile_label = "Team leader profile OTP" if pending["account_type"] == "team_leader" else "Account profile OTP"
    revoke_account_sessions(account_id)
    add_log(pending["name"], "changed_password", profile_label, actor_account_id=account_id)


def confirm_reseller_password_change(account_id: int, otp_code: str) -> None:
    confirm_account_password_change(account_id, otp_code, ("reseller",))


def request_login_otp(account_id: int) -> dict:
    enforce_otp_resend(account_id)
    account = fetch_one(
        """
        SELECT account_id, account_type, team_leader_role, name, email,
               profile_image_path, profile_image_position_x, profile_image_position_y, profile_image_scale,
               is_active, activation_status
        FROM accounts
        WHERE account_id = %s
        LIMIT 1;
        """,
        (account_id,),
    )
    if not account or not account["is_active"] or account["activation_status"] != "active":
        raise ValueError("Account was not found.")

    otp_code = f"{secrets.randbelow(1_000_000):06d}"
    otp_hash = hash_password(otp_code)
    expires_at = business_now() + timedelta(minutes=10)

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE account_login_otps
            SET consumed_at = now()
            WHERE account_id = %s
              AND consumed_at IS NULL;
            """,
            (account_id,),
        )
        cur.execute(
            """
            INSERT INTO account_login_otps (account_id, otp_hash, expires_at)
            VALUES (%s, %s, %s)
            RETURNING account_login_otp_id;
            """,
            (account_id, otp_hash, expires_at),
        )
        otp = cur.fetchone()

    record_otp_resend(account_id)
    record_event("otp_verify", f"account:{account_id}", succeeded=True)

    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    return {
        "otp_id": otp["account_login_otp_id"],
        "otp_code": otp_code,
        "account": clean,
    }


def cancel_login_otp(account_id: int, otp_id: int | None = None) -> None:
    params: list[object] = [account_id]
    where = "account_id = %s AND consumed_at IS NULL"
    if otp_id is not None:
        where += " AND account_login_otp_id = %s"
        params.append(otp_id)
    execute_write(f"UPDATE account_login_otps SET consumed_at = now() WHERE {where};", tuple(params))


def confirm_login_otp(account_id: int, otp_code: str) -> dict:
    cleaned_otp = "".join(ch for ch in otp_code.strip() if ch.isdigit())
    if len(cleaned_otp) != 6:
        raise ValueError("Enter the 6-digit OTP sent to your email.")

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT p.account_login_otp_id, p.otp_hash,
                   a.account_id, a.account_type, a.team_leader_role, a.name, a.email,
                   a.profile_image_path, a.profile_image_position_x, a.profile_image_position_y, a.profile_image_scale,
                   a.is_active, a.activation_status
            FROM account_login_otps p
            JOIN accounts a ON a.account_id = p.account_id
            WHERE p.account_id = %s
              AND p.consumed_at IS NULL
              AND p.expires_at >= now()
              AND a.is_active = true
              AND a.activation_status = 'active'
            ORDER BY p.created_at DESC
            LIMIT 1
            FOR UPDATE OF p;
            """,
            (account_id,),
        )
        pending = cur.fetchone()
        if not pending:
            raise ValueError("OTP expired or no login request is pending.")
        if not verify_password(cleaned_otp, pending["otp_hash"]):
            raise ValueError("OTP is incorrect.")
        cur.execute(
            "UPDATE account_login_otps SET consumed_at = now() WHERE account_login_otp_id = %s;",
            (pending["account_login_otp_id"],),
        )

    clean = clean_row(pending)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    return clean


def update_reseller_profile(
    account_id: int,
    name: str,
    business_name: str,
    contact_number: str,
    address: str,
) -> dict:
    cleaned_name = " ".join(name.strip().split())
    cleaned_business = " ".join(business_name.strip().split())
    cleaned_contact = " ".join(contact_number.strip().split())
    cleaned_address = " ".join(address.strip().split())
    if not cleaned_name:
        raise ValueError("Name is required.")
    if not cleaned_business:
        raise ValueError("Business name is required.")
    if not cleaned_contact:
        raise ValueError("Contact number is required.")
    if not cleaned_address:
        raise ValueError("Address is required.")

    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT a.account_id, a.name, a.reseller_id
            FROM accounts a
            WHERE a.account_id = %s
              AND a.account_type = 'reseller'
              AND a.is_active = true
            LIMIT 1
            FOR UPDATE;
            """,
            (account_id,),
        )
        account = cur.fetchone()
        if not account or account["reseller_id"] is None:
            raise ValueError("Reseller account was not found.")
        cur.execute(
            "UPDATE accounts SET name = %s WHERE account_id = %s;",
            (cleaned_name, account_id),
        )
        cur.execute(
            """
            UPDATE resellers
            SET business_name = %s,
                contact_person = %s,
                contact_number = %s,
                address = %s
            WHERE reseller_id = %s;
            """,
            (cleaned_business, cleaned_name, cleaned_contact, cleaned_address, account["reseller_id"]),
        )

    add_log(cleaned_name, "updated_profile", "Reseller profile")
    return reseller_account_profile(account_id)


def account_portal_profile(account_id: int) -> dict | None:
    account = fetch_one("""
        SELECT account_id, account_type, team_leader_role, name, email,
               profile_image_path, profile_image_position_x, profile_image_position_y, profile_image_scale,
               is_active, activation_status
        FROM accounts
        WHERE account_id = %s
        LIMIT 1;
    """, (account_id,))
    if not account or not account["is_active"] or account["activation_status"] != "active":
        return None
    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    return clean


def update_account_profile_image(
    account_id: int,
    profile_image_path: str | None,
    position_x: object = 50,
    position_y: object = 50,
    scale: object = 1,
) -> dict:
    cleaned_path = profile_image_path.strip() if profile_image_path else None
    if cleaned_path and not cleaned_path.startswith("uploads/profiles/"):
        raise ValueError("Profile image path is invalid.")
    clean_x = max(0, min(100, float(position_x or 50)))
    clean_y = max(0, min(100, float(position_y or 50)))
    clean_scale = max(1, min(2, float(scale or 1)))
    account = execute_write(
        """
        UPDATE accounts
        SET profile_image_path = %s,
            profile_image_position_x = %s,
            profile_image_position_y = %s,
            profile_image_scale = %s
        WHERE account_id = %s
          AND is_active = true
          AND activation_status = 'active'
        RETURNING account_id, account_type, team_leader_role, name, email,
                  profile_image_path, profile_image_position_x, profile_image_position_y, profile_image_scale,
                  is_active, activation_status;
        """,
        (cleaned_path, clean_x, clean_y, clean_scale, account_id),
        returning=True,
    )
    if not account:
        raise ValueError("Account was not found.")
    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    if clean["account_type"] == "team_leader" and not clean.get("team_leader_role"):
        clean["team_leader_role"] = "sales"
    add_log(clean["name"], "updated_profile_picture", "Account profile")
    return clean


def active_session_account(account_id: int) -> dict | None:
    return account_portal_profile(account_id)


def record_user_consent(account_id: int, policy_version: str, consent_source: str, provider: str | None = None) -> None:
    execute_write(
        """
        INSERT INTO user_consents (account_id, policy_version, consent_source, provider)
        VALUES (%s, %s, %s, %s);
        """,
        (account_id, policy_version, consent_source, provider),
    )


def _create_activation_token_cursor(cur, account_id: int) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    cur.execute(
        "UPDATE account_activation_tokens SET consumed_at = now() WHERE account_id = %s AND consumed_at IS NULL;",
        (account_id,),
    )
    cur.execute(
        """
        INSERT INTO account_activation_tokens (account_id, token_hash, expires_at)
        VALUES (%s, %s, now() + interval '24 hours');
        """,
        (account_id, token_hash),
    )
    return raw_token


def reseller_account_profile(account_id: int) -> dict:
    row = fetch_one("""
        SELECT a.account_id, a.name, a.email, a.reseller_id,
               COALESCE(r.business_name, a.name) AS business_name,
               r.contact_person,
               r.contact_number,
               r.address,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               tl.email AS team_leader_email
        FROM accounts a
        LEFT JOIN resellers r ON r.reseller_id = a.reseller_id
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        WHERE a.account_id = %s
          AND a.account_type = 'reseller'
        LIMIT 1;
    """, (account_id,))
    if not row:
        raise ValueError("Reseller account was not found.")
    if row["reseller_id"] is None:
        reseller = fetch_one("""
            SELECT r.reseller_id,
                   r.business_name,
                   r.contact_person,
                   r.contact_number,
                   r.address,
                   r.team_leader_account_id,
                   tl.name AS team_leader_name,
                   tl.email AS team_leader_email
            FROM resellers r
            LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
            WHERE lower(r.email) = lower(%s)
            LIMIT 1;
        """, (row["email"],))
        if reseller:
            row["reseller_id"] = reseller["reseller_id"]
            row["business_name"] = reseller["business_name"]
            row["contact_person"] = reseller["contact_person"]
            row["contact_number"] = reseller["contact_number"]
            row["address"] = reseller["address"]
            row["team_leader_account_id"] = reseller["team_leader_account_id"]
            row["team_leader_name"] = reseller["team_leader_name"]
            row["team_leader_email"] = reseller["team_leader_email"]
    if row["reseller_id"] is None:
        raise ValueError("This reseller account is not linked to an approved reseller profile.")
    return clean_row(row)


def require_reseller_team_leader(account_id: int) -> dict:
    profile = reseller_account_profile(account_id)
    if not profile.get("team_leader_account_id"):
        raise ValueError("Your reseller account is not assigned to a team leader yet. Please contact the owner before submitting orders or reports.")
    return profile


def add_account(account_type: str, name: str, email: str, team_leader_role: str | None = None) -> dict:
    reseller_id = None
    cleaned_name = " ".join(name.strip().split())
    cleaned_email = email.strip().lower()

    if account_type == "reseller":
        raise ValueError("Reseller accounts are created only by approving chatbot inquiries.")
    if account_type == "team_leader":
        team_leader_role = team_leader_role if team_leader_role in {"inventory", "sales", "sales-supervisor"} else "sales"
    else:
        team_leader_role = None
    if fetch_one("SELECT account_id FROM accounts WHERE lower(email) = lower(%s) LIMIT 1;", (cleaned_email,)):
        raise ValueError("That email is already used by an existing portal account.")

    with get_transaction_cursor() as cur:
        cur.execute("""
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, team_leader_role,
                                  is_active, activation_status)
            VALUES (%s, %s, %s, %s, %s, %s, false, 'pending')
            RETURNING account_id, account_type, team_leader_role, name, email;
        """, (account_type, reseller_id, cleaned_name, cleaned_email, hash_password(secrets.token_urlsafe(32)), team_leader_role))
        account = cur.fetchone()
        activation_token = _create_activation_token_cursor(cur, account["account_id"])
        cur.execute("SELECT account_id FROM accounts WHERE account_type = 'owner' AND is_active = true ORDER BY account_id LIMIT 1;")
        owner = cur.fetchone()
        _add_log_cursor(cur, actor_account_id=owner["account_id"] if owner else None, action="created_account", entity_type="accounts", entity_id=account["account_id"])
        _enqueue_outbox_cursor(
            cur, "account_activation",
            {"to_email": account["email"], "name": account["name"],
             "account_label": team_leader_role_labels.get(team_leader_role or "", "team leader") if account_type == "team_leader" else "owner",
             "activation_path": f"/activate?token={activation_token}"},
            f"account-activation-{account['account_id']}-{hashlib.sha256(activation_token.encode()).hexdigest()[:12]}",
        )
    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    return clean


def activate_account(raw_token: str, new_password: str) -> dict:
    token = raw_token.strip()
    if not token:
        raise ValueError("Activation link is invalid or expired.")
    password = validate_password_policy(new_password)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT t.activation_token_id, a.account_id, a.account_type, a.team_leader_role, a.name, a.email
            FROM account_activation_tokens t
            JOIN accounts a ON a.account_id = t.account_id
            WHERE t.token_hash = %s AND t.consumed_at IS NULL AND t.expires_at >= now()
              AND a.activation_status = 'pending'
            FOR UPDATE OF t, a;
            """,
            (token_hash,),
        )
        account = cur.fetchone()
        if not account:
            raise ValueError("Activation link is invalid or expired.")
        cur.execute(
            """
            UPDATE accounts SET password_hash = %s, is_active = true,
                activation_status = 'active', activated_at = now() WHERE account_id = %s;
            """,
            (hash_password(password), account["account_id"]),
        )
        cur.execute(
            """
            UPDATE resellers SET reseller_status = 'active'
            WHERE reseller_id = (SELECT reseller_id FROM accounts WHERE account_id = %s)
              AND reseller_status = 'pending';
            """,
            (account["account_id"],),
        )
        cur.execute("UPDATE account_activation_tokens SET consumed_at = now() WHERE activation_token_id = %s;", (account["activation_token_id"],))
        _add_log_cursor(cur, actor_account_id=account["account_id"], action="activated_account", entity_type="accounts", entity_id=account["account_id"])
    clean = clean_row(account)
    clean["role_key"] = role_key_for_account_type(clean["account_type"], clean.get("team_leader_role"))
    return clean


def resend_activation(account_id: int, actor_account_id: int, account_types: tuple[str, ...] | None = None) -> None:
    with get_transaction_cursor() as cur:
        cur.execute(
            "SELECT account_id, name, email, account_type FROM accounts WHERE account_id = %s AND activation_status = 'pending' FOR UPDATE;",
            (account_id,),
        )
        account = cur.fetchone()
        if not account:
            raise ValueError("Pending account was not found.")
        if account_types and account["account_type"] not in account_types:
            raise ValueError("You do not have access to manage this account activation.")
        token = _create_activation_token_cursor(cur, account_id)
        _enqueue_outbox_cursor(
            cur, "account_activation",
            {"to_email": account["email"], "name": account["name"], "account_label": account["account_type"].replace("_", " "),
             "activation_path": f"/activate?token={token}"},
            f"account-activation-{account_id}-{hashlib.sha256(token.encode()).hexdigest()[:12]}",
        )
        _add_log_cursor(cur, actor_account_id=actor_account_id, action="resent_activation", entity_type="accounts", entity_id=account_id)


__all__ = ['list_accounts', 'count_accounts', 'list_team_leader_accounts', 'next_sales_team_leader', 'list_all_team_leader_accounts', 'list_reseller_assignments', 'set_reseller_team_leader', 'update_team_leader_account_role', 'role_key_for_account_type', 'authenticate_account', 'change_account_password', 'request_account_password_change', 'request_reseller_password_change', 'cancel_reseller_password_change', 'cancel_account_password_change', 'confirm_account_password_change', 'confirm_reseller_password_change', 'request_login_otp', 'cancel_login_otp', 'confirm_login_otp', 'update_reseller_profile', 'account_portal_profile', 'update_account_profile_image', 'active_session_account', 'record_user_consent', '_create_activation_token_cursor', 'reseller_account_profile', 'require_reseller_team_leader', 'add_account', 'activate_account', 'resend_activation']
