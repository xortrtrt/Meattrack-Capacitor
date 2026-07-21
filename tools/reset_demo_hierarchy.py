from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import TEAM_LEADER_PASSWORD
from app.database import get_transaction_cursor
from app.repositories import ensure_system_tables
from app.security import hash_password


SALES_LEADERS = [
    ("Sales Leader A", "sales.leader.a@batangaspremium.test"),
    ("Sales Leader B", "sales.leader.b@batangaspremium.test"),
]
INVENTORY_LEADER = ("Inventory Leader", "inventory.leader@batangaspremium.test")


def upsert_team_leader(cur, *, name: str, email: str, team_leader_role: str) -> int:
    cur.execute(
        """
        SELECT account_id
        FROM accounts
        WHERE lower(email) = lower(%s)
        LIMIT 1
        FOR UPDATE;
        """,
        (email,),
    )
    existing = cur.fetchone()
    password_hash = hash_password(TEAM_LEADER_PASSWORD)
    if existing:
        cur.execute(
            """
            UPDATE accounts
            SET account_type = 'team_leader',
                reseller_id = NULL,
                name = %s,
                email = %s,
                password_hash = %s,
                team_leader_role = %s,
                auth_user_id = NULL,
                auth_provider = NULL,
                is_active = true
            WHERE account_id = %s
            RETURNING account_id;
            """,
            (name, email, password_hash, team_leader_role, existing["account_id"]),
        )
        return int(cur.fetchone()["account_id"])

    cur.execute(
        """
        INSERT INTO accounts (
            account_type, reseller_id, name, email, password_hash,
            team_leader_role, auth_user_id, auth_provider, is_active
        )
        VALUES ('team_leader', NULL, %s, %s, %s, %s, NULL, NULL, true)
        RETURNING account_id;
        """,
        (name, email, password_hash, team_leader_role),
    )
    return int(cur.fetchone()["account_id"])


def reset_demo_hierarchy() -> dict:
    ensure_system_tables()
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
            SELECT account_id
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
                    password_hash = %s,
                    team_leader_role = 'inventory',
                    auth_user_id = NULL,
                    auth_provider = NULL,
                    reseller_id = NULL,
                    is_active = true
                WHERE account_id = %s;
                """,
                (
                    INVENTORY_LEADER[0],
                    INVENTORY_LEADER[1],
                    hash_password(TEAM_LEADER_PASSWORD),
                    inventory_id,
                ),
            )
        else:
            inventory_id = upsert_team_leader(
                cur,
                name=INVENTORY_LEADER[0],
                email=INVENTORY_LEADER[1],
                team_leader_role="inventory",
            )

        cur.execute(
            """
            DELETE FROM accounts
            WHERE account_type = 'team_leader'
              AND account_id <> %s
              AND lower(email) <> ALL(%s);
            """,
            (inventory_id, [email.lower() for email in sales_emails]),
        )

        sales_ids = [
            upsert_team_leader(cur, name=name, email=email, team_leader_role="sales")
            for name, email in SALES_LEADERS
        ]

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
    }


if __name__ == "__main__":
    result = reset_demo_hierarchy()
    print("Demo hierarchy reset complete.")
    print(f"Owner accounts retained: {result['owners']}")
    print(f"Inventory leader: {INVENTORY_LEADER[1]}")
    for _, email in SALES_LEADERS:
        print(f"Sales leader: {email}")
    print("Team leader password source: TEAM_LEADER_PASSWORD")
    print(f"Resellers remaining: {result['resellers']}")
    print(f"Inquiries remaining: {result['inquiries']}")
    print(f"Orders remaining: {result['orders']}")
    print(f"Sales reports remaining: {result['sales_reports']}")
