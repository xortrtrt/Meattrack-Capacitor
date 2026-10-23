from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import get_transaction_cursor
from app.security import hash_password


E2E_EMAIL = os.getenv("E2E_SUPERVISOR_EMAIL", "supervisor.e2e@meattrack.test")
E2E_PASSWORD = os.getenv("E2E_SUPERVISOR_PASSWORD", "Meattrack-E2E-2026!")


def seed_sales_supervisor() -> None:
    password_hash = hash_password(E2E_PASSWORD)
    with get_transaction_cursor() as cursor:
        cursor.execute(
            """
            UPDATE accounts
            SET account_type = 'team_leader',
                reseller_id = NULL,
                name = 'E2E Sales Supervisor',
                email = %s,
                password_hash = %s,
                team_leader_role = 'sales-supervisor',
                is_active = true,
                activation_status = 'active',
                activated_at = COALESCE(activated_at, now()),
                profile_image_path = NULL,
                profile_image_position_x = 50,
                profile_image_position_y = 50,
                profile_image_scale = 1
            WHERE lower(email) = lower(%s);
            """,
            (E2E_EMAIL, password_hash, E2E_EMAIL),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO accounts (
                    account_type, name, email, password_hash, team_leader_role,
                    is_active, activation_status, activated_at
                )
                VALUES ('team_leader', 'E2E Sales Supervisor', %s, %s,
                        'sales-supervisor', true, 'active', now());
                """,
                (E2E_EMAIL, password_hash),
            )
        # The public smoke test opens a real catalog card on a fresh database.
        cursor.execute(
            """
            INSERT INTO inventory_items (
                item_type, category, name, description, unit, base_price,
                pack_size, pack_size_unit, pack_content_status
            )
            SELECT 'finished_product', 'Pork', 'E2E Catalog Bacon',
                   'Browser smoke-test catalog product.', 'pack', 100, 500, 'g', 'declared'
            WHERE NOT EXISTS (
                SELECT 1 FROM inventory_items WHERE name = 'E2E Catalog Bacon'
            );
            """
        )


if __name__ == "__main__":
    seed_sales_supervisor()
    print(f"Seeded deterministic sales-supervisor account: {E2E_EMAIL}")
