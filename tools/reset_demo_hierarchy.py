from __future__ import annotations

import hashlib
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import APP_BASE_URL
from app.database import get_transaction_cursor
from app.repositories import ensure_system_tables
from app.security import hash_password


SALES_LEADERS = [
    ("Sales Leader A", "sales.leader.a@batangaspremium.test"),
    ("Sales Leader B", "sales.leader.b@batangaspremium.test"),
]
INVENTORY_LEADER = ("Inventory Leader", "inventory.leader@batangaspremium.test")


def create_activation_link(cur, account_id: int) -> str:
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
    return f"{APP_BASE_URL}/activate?token={raw_token}"


def upsert_team_leader(cur, *, name: str, email: str, team_leader_role: str) -> tuple[int, str | None]:
    cur.execute(
        """
        SELECT account_id, activation_status
        FROM accounts
        WHERE lower(email) = lower(%s)
        LIMIT 1
        FOR UPDATE;
        """,
        (email,),
    )
    existing = cur.fetchone()
    if existing:
        cur.execute(
            """
            UPDATE accounts
            SET account_type = 'team_leader',
                reseller_id = NULL,
                name = %s,
                email = %s,
                team_leader_role = %s
            WHERE account_id = %s
            RETURNING account_id;
            """,
            (name, email, team_leader_role, existing["account_id"]),
        )
        account_id = int(cur.fetchone()["account_id"])
        activation_link = create_activation_link(cur, account_id) if existing["activation_status"] == "pending" else None
        return account_id, activation_link

    cur.execute(
        """
        INSERT INTO accounts (
            account_type, reseller_id, name, email, password_hash,
            team_leader_role, is_active, activation_status
        )
        VALUES ('team_leader', NULL, %s, %s, %s, %s, false, 'pending')
        RETURNING account_id;
        """,
        (name, email, hash_password(secrets.token_urlsafe(32)), team_leader_role),
    )
    account_id = int(cur.fetchone()["account_id"])
    return account_id, create_activation_link(cur, account_id)


def reset_demo_hierarchy() -> dict:
    ensure_system_tables()
    activation_links: list[tuple[str, str]] = []
    with get_transaction_cursor() as cur:
        cur.execute("DELETE FROM notifications;")
        cur.execute("DELETE FROM reseller_cart_items;")
        cur.execute("DELETE FROM sales_report_attachments;")
        cur.execute("DELETE FROM sales_report_items;")
        cur.execute("DELETE FROM sales_reports;")
        cur.execute("DELETE FROM order_items;")
        cur.execute("DELETE FROM orders;")
        cur.execute("DELETE FROM accounts WHERE account_type = 'reseller';")
        cur.execute("DELETE FROM resellers;")
        cur.execute("DELETE FROM inquiries;")

        sales_emails = [email for _, email in SALES_LEADERS]
        cur.execute(
            """
            SELECT account_id, activation_status
            FROM accounts
            WHERE account_type = 'team_leader'
              AND lower(email) <> ALL(%s)
            ORDER BY account_id
            LIMIT 1
            FOR UPDATE;
            """,
            ([email.lower() for email in sales_emails],),
        )
        inventory = cur.fetchone()
        if inventory:
            inventory_id = int(inventory["account_id"])
            cur.execute(
                """
                UPDATE accounts
                SET name = %s,
                    email = %s,
                    team_leader_role = 'inventory',
                    reseller_id = NULL
                WHERE account_id = %s;
                """,
                (
                    INVENTORY_LEADER[0],
                    INVENTORY_LEADER[1],
                    inventory_id,
                ),
            )
            if inventory["activation_status"] == "pending":
                activation_links.append((INVENTORY_LEADER[1], create_activation_link(cur, inventory_id)))
        else:
            inventory_id, activation_link = upsert_team_leader(
                cur,
                name=INVENTORY_LEADER[0],
                email=INVENTORY_LEADER[1],
                team_leader_role="inventory",
            )
            if activation_link:
                activation_links.append((INVENTORY_LEADER[1], activation_link))

        cur.execute(
            """
            DELETE FROM accounts
            WHERE account_type = 'team_leader'
              AND account_id <> %s
              AND lower(email) <> ALL(%s);
            """,
            (inventory_id, [email.lower() for email in sales_emails]),
        )

        sales_ids = []
        for name, email in SALES_LEADERS:
            account_id, activation_link = upsert_team_leader(
                cur,
                name=name,
                email=email,
                team_leader_role="sales",
            )
            sales_ids.append(account_id)
            if activation_link:
                activation_links.append((email, activation_link))

        cur.execute("SELECT COUNT(*) AS total FROM accounts WHERE account_type = 'owner';")
        owner_count = int(cur.fetchone()["total"])
        cur.execute("SELECT COUNT(*) AS total FROM accounts WHERE account_type = 'reseller';")
        reseller_count = int(cur.fetchone()["total"])
        cur.execute("SELECT COUNT(*) AS total FROM inquiries;")
        inquiry_count = int(cur.fetchone()["total"])
        cur.execute("SELECT COUNT(*) AS total FROM orders;")
        order_count = int(cur.fetchone()["total"])
        cur.execute("SELECT COUNT(*) AS total FROM sales_reports;")
        report_count = int(cur.fetchone()["total"])

    return {
        "owners": owner_count,
        "inventory_leader_id": inventory_id,
        "sales_leader_ids": sales_ids,
        "resellers": reseller_count,
        "inquiries": inquiry_count,
        "orders": order_count,
        "sales_reports": report_count,
        "activation_links": activation_links,
    }


if __name__ == "__main__":
    result = reset_demo_hierarchy()
    print("Demo hierarchy reset complete.")
    print(f"Owner accounts retained: {result['owners']}")
    print(f"Inventory leader: {INVENTORY_LEADER[1]}")
    for _, email in SALES_LEADERS:
        print(f"Sales leader: {email}")
    if result["activation_links"]:
        print("Pending team leaders must choose their own passwords using these one-time activation links:")
        for email, link in result["activation_links"]:
            print(f"{email}: {link}")
    else:
        print("Existing team leader passwords were preserved from the database.")
    print(f"Resellers remaining: {result['resellers']}")
    print(f"Inquiries remaining: {result['inquiries']}")
    print(f"Orders remaining: {result['orders']}")
    print(f"Sales reports remaining: {result['sales_reports']}")
