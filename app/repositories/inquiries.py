from __future__ import annotations


from ._shared import *
from .accounts import _create_activation_token_cursor, next_sales_supervisor
from .notifications import _create_notification_cursor


def list_inquiries(
    limit: int | None = None,
    q: str = "",
    status: str = "",
    assigned_team_leader_account_id: int | None = None,
    include_unassigned: bool = False,
    page: int | None = None,
    page_size: int = 10,
    sort: str = "",
) -> list[dict]:
    query = """
        SELECT i.inquiry_id, i.name, i.contact_number, i.email, i.business_name, i.message,
               i.rejection_reason, i.status,
               a.name AS assigned_to, i.created_at
        FROM inquiries i
        LEFT JOIN accounts a ON a.account_id = i.assigned_team_leader_account_id
    """
    where = []
    params: list[object] = []
    q = q.strip()
    status = status.strip()
    if q:
        where.append("(i.name ILIKE %s OR i.email ILIKE %s OR i.business_name ILIKE %s OR i.contact_number ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search, search])
    if status:
        where.append("i.status = %s")
        params.append(status)
    if assigned_team_leader_account_id is not None:
        where.append(
            "(i.assigned_team_leader_account_id = %s OR i.assigned_team_leader_account_id IS NULL)"
            if include_unassigned
            else "i.assigned_team_leader_account_id = %s"
        )
        params.append(assigned_team_leader_account_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += f" ORDER BY {safe_sort_sql(sort, INQUIRY_SORTS, INQUIRY_SORTS['newest'])}"
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        query += " LIMIT %s"
        params.append(limit)
    elif page is not None:
        if page < 1:
            raise ValueError("page must be positive")
        if page_size < 1:
            raise ValueError("page_size must be positive")
        query += " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])
    return clean_row(fetch_all(query + ";", tuple(params) or None))


def count_inquiries(
    q: str = "",
    status: str = "",
    assigned_team_leader_account_id: int | None = None,
    include_unassigned: bool = False,
) -> int:
    query = "SELECT COUNT(*) AS total FROM inquiries i"
    where = []
    params: list[object] = []
    q = q.strip()
    status = status.strip()
    if q:
        where.append("(i.name ILIKE %s OR i.email ILIKE %s OR i.business_name ILIKE %s OR i.contact_number ILIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search, search])
    if status:
        where.append("i.status = %s")
        params.append(status)
    if assigned_team_leader_account_id is not None:
        where.append(
            "(i.assigned_team_leader_account_id = %s OR i.assigned_team_leader_account_id IS NULL)"
            if include_unassigned
            else "i.assigned_team_leader_account_id = %s"
        )
        params.append(assigned_team_leader_account_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    row = fetch_one(query + ";", tuple(params) or None)
    return int(row["total"])


def add_inquiry(
    name: str,
    business_name: str,
    email: str,
    contact_number: str,
    message: str,
    chat_conversation_id: str | None = None,
    assigned_team_leader_account_id: int | None = None,
) -> dict:
    supervisor = next_sales_supervisor(assigned_team_leader_account_id)
    leader_id = supervisor["account_id"] if supervisor else None
    with get_transaction_cursor() as cur:
        cur.execute("""
            INSERT INTO inquiries (
                name, contact_number, email, business_name, message, status,
                assigned_team_leader_account_id, chat_conversation_id
            )
            VALUES (%s, %s, %s, %s, %s, 'assigned', %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING inquiry_id, name, contact_number, email, business_name, message, status, assigned_team_leader_account_id, created_at;
        """, (name, contact_number, email, business_name, message, leader_id, chat_conversation_id))
        inq = cur.fetchone()
        if not inq:
            raise ValueError("This conversation already has a reseller inquiry.")
        _add_log_cursor(cur, actor_account_id=None, action="created_inquiry", entity_type="inquiries", entity_id=inq["inquiry_id"])
        _create_notification_cursor(
            cur, recipient_account_id=leader_id, recipient_role=None if leader_id else "owner",
            category="inquiry", severity="info", title="New reseller inquiry",
            message=f"{business_name} is waiting for review.",
            target_url="/portal/sales-supervisor/inquiries" if leader_id else "/portal/owner/accounts",
            source_type="inquiries", source_id=inq["inquiry_id"], dedupe_key=f"inquiry-{inq['inquiry_id']}",
        )
    return clean_row(inq)


def due_inquiry_followups(limit: int = 20) -> list[dict]:
    return clean_row(fetch_all(
        """
        SELECT inquiry_id, name, email, business_name, created_at
        FROM inquiries
        WHERE status = 'assigned'
          AND follow_up_sent_at IS NULL
          AND created_at <= now() - interval '1 day'
        ORDER BY created_at ASC
        LIMIT %s;
        """,
        (limit,),
    ))


def mark_inquiry_followup_sent(inquiry_id: int) -> None:
    execute_write(
        """
        UPDATE inquiries
        SET follow_up_sent_at = now()
        WHERE inquiry_id = %s
          AND follow_up_sent_at IS NULL;
        """,
        (inquiry_id,),
    )


def add_reseller_from_inquiry(inquiry_id: int, approving_team_leader_account_id: int | None = None) -> dict | None:
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM inquiries WHERE inquiry_id = %s FOR UPDATE;", (inquiry_id,))
        inq = cur.fetchone()
        if not inq:
            return None
        if inq["status"] not in {"pending", "assigned", "contacted"}:
            raise ValueError(f"Inquiry is already {inq['status']} and cannot be reviewed again.")
        if approving_team_leader_account_id is not None and inq["assigned_team_leader_account_id"] not in {None, approving_team_leader_account_id}:
            return None

        cur.execute(
            """
            SELECT account_id, account_type
            FROM accounts
            WHERE lower(email) = lower(%s)
            LIMIT 1;
            """,
            (inq["email"],),
        )
        existing_account = cur.fetchone()
        if existing_account:
            raise ValueError("That email is already used by an existing portal account. Use a different reseller email before approval.")

        leader_id = approving_team_leader_account_id or inq["assigned_team_leader_account_id"]
        if leader_id is None:
            cur.execute("""
                SELECT account_id
                FROM accounts
                WHERE account_type = 'team_leader'
                  AND is_active = true
                  AND team_leader_role = 'sales-supervisor'
                ORDER BY account_id
                LIMIT 1;
            """)
            leader = cur.fetchone()
            leader_id = leader["account_id"] if leader else None
        if leader_id is None:
            raise ValueError("No active sales supervisor is available to review this inquiry.")

        cur.execute(
            """
            SELECT reseller_id
            FROM resellers
            WHERE inquiry_id = %s
            LIMIT 1;
            """,
            (inquiry_id,),
        )
        if cur.fetchone():
            raise ValueError("This inquiry already has a reseller account.")

        cur.execute("""
            UPDATE inquiries
            SET status = 'approved', assigned_team_leader_account_id = %s, reviewed_by_account_id = %s, reviewed_at = %s
            WHERE inquiry_id = %s;
        """, (leader_id, leader_id, business_now(), inquiry_id))

        cur.execute("""
            INSERT INTO resellers (inquiry_id, business_name, contact_person, email, contact_number, address, reseller_status, team_leader_account_id, approved_by_account_id, approved_at)
            VALUES (%s, %s, %s, %s, %s, 'Pending onboarding details', 'pending', %s, %s, %s)
            RETURNING reseller_id, business_name, contact_person, email, contact_number, address, reseller_status, team_leader_account_id, approved_by_account_id, created_at;
        """, (inquiry_id, inq["business_name"], inq["name"], inq["email"], inq["contact_number"], leader_id, leader_id, business_now()))
        res = cur.fetchone()

        cur.execute("""
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active, activation_status)
            VALUES ('reseller', %s, %s, %s, %s, false, 'pending')
            RETURNING account_id, email;
        """, (res["reseller_id"], res["business_name"], res["email"], hash_password(secrets.token_urlsafe(32))))
        account = cur.fetchone()

        cur.execute("SELECT name, email FROM accounts WHERE account_id = %s;", (leader_id,))
        leader = cur.fetchone()

        activation_token = _create_activation_token_cursor(cur, account["account_id"])
        _add_log_cursor(
            cur, actor_account_id=leader_id, action="approved_reseller_inquiry",
            entity_type="inquiries", entity_id=inquiry_id,
        )
        _create_notification_cursor(
            cur, recipient_role="owner", category="account", severity="info", title="Reseller approved",
            message=f"{res['business_name']} was approved from inquiry #{inquiry_id}.",
            target_url="/portal/owner/accounts", source_type="inquiries", source_id=inquiry_id,
            dedupe_key=f"inquiry-approved-{inquiry_id}",
        )
        _enqueue_outbox_cursor(
            cur, "account_activation",
            {"to_email": account["email"], "name": res["business_name"], "account_label": "reseller",
             "activation_path": f"/activate?token={activation_token}", "team_leader_name": leader["name"] if leader else "Sales team leader"},
            f"account-activation-{account['account_id']}-{hashlib.sha256(activation_token.encode()).hexdigest()[:12]}",
        )

    res = clean_row(res)

    res["account_id"] = account["account_id"] if account else None
    res["account_email"] = account["email"] if account else res["email"]
    res["team_leader_name"] = leader["name"] if leader else "Assigned sales team leader"
    res["team_leader_email"] = leader["email"] if leader else ""
    return res


def reject_inquiry(
    inquiry_id: int,
    rejection_reason: str,
    reviewing_team_leader_account_id: int | None = None,
) -> bool:
    cleaned_reason = "\n".join(line.strip() for line in str(rejection_reason).strip().splitlines() if line.strip())
    if not 5 <= len(cleaned_reason) <= 1000:
        raise ValueError("Enter a rejection reason using 5 to 1000 characters.")
    if any(ord(character) < 32 and character not in {"\n", "\t"} for character in cleaned_reason):
        raise ValueError("The rejection reason contains unsupported characters.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM inquiries WHERE inquiry_id = %s FOR UPDATE;", (inquiry_id,))
        inq = cur.fetchone()
        if not inq:
            return False
        if inq["status"] not in {"pending", "assigned", "contacted"}:
            raise ValueError(f"Inquiry is already {inq['status']} and cannot be reviewed again.")
        if reviewing_team_leader_account_id is not None and inq["assigned_team_leader_account_id"] not in {None, reviewing_team_leader_account_id}:
            return False
        reviewer_id = reviewing_team_leader_account_id or inq["assigned_team_leader_account_id"]
        cur.execute(
            """
            UPDATE inquiries SET status = 'rejected', rejection_reason = %s,
                assigned_team_leader_account_id = %s,
                reviewed_by_account_id = %s, reviewed_at = %s WHERE inquiry_id = %s;
            """,
            (cleaned_reason, reviewer_id, reviewer_id, business_now(), inquiry_id),
        )
        _add_log_cursor(cur, actor_account_id=reviewer_id, action="rejected_reseller_inquiry", entity_type="inquiries", entity_id=inquiry_id)
        _enqueue_outbox_cursor(
            cur,
            "inquiry_rejected",
            {
                "inquiry_id": inquiry_id,
                "to_email": inq["email"],
                "name": inq["name"],
                "business_name": inq["business_name"],
                "rejection_reason": cleaned_reason,
            },
            f"inquiry-rejected-{inquiry_id}",
        )
    return True


__all__ = ['list_inquiries', 'count_inquiries', 'add_inquiry', 'due_inquiry_followups', 'mark_inquiry_followup_sent', 'add_reseller_from_inquiry', 'reject_inquiry']
