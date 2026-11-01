"""Cursor-only routing shared by paid-order and return handoffs."""


def supervisor_for_case(cur, preferred_id: int | None = None) -> int | None:
    cur.execute(
        """SELECT account_id FROM accounts
        WHERE account_type='team_leader' AND team_leader_role='sales-supervisor' AND is_active
        ORDER BY (account_id=%s) DESC NULLS LAST,name,account_id""",
        (preferred_id,),
    )
    supervisors = [row["account_id"] for row in cur.fetchall()]
    if not supervisors:
        return None
    if preferred_id in supervisors:
        return preferred_id
    cur.execute("""SELECT COUNT(*) total FROM inquiries i JOIN accounts a
        ON a.account_id=i.assigned_team_leader_account_id WHERE a.team_leader_role='sales-supervisor'""")
    return supervisors[int(cur.fetchone()["total"]) % len(supervisors)]
