from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import database_dsn


SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"
MIGRATIONS_DIR = PROJECT_ROOT / "database" / "migrations"

# Migration 023's conflict target was relaxed so the latest baseline (which uses
# dedupe_key uniqueness) can safely replay it before migration 024. Databases that
# already recorded the original immutable payload may keep that checksum.
COMPATIBLE_MIGRATION_CHECKSUMS = {
    "023_separate_order_system_messages.sql": {
        "69789127def7dd552f5991c7dd65d4df13ee59d4a0dba5de73760aa3ab633ca8",
    },
}


def main() -> None:
    with psycopg2.connect(database_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.accounts');")
            if cursor.fetchone()[0] is None:
                print("Applying baseline schema...")
                baseline_sql = SCHEMA_FILE.read_text(encoding="utf-8").strip()
                baseline_sql = baseline_sql.removeprefix("BEGIN;").strip()
                baseline_sql = baseline_sql.removesuffix("COMMIT;").strip()
                cursor.execute(baseline_sql)

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    checksum_sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )
        connection.commit()

        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            sql = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT checksum_sha256 FROM schema_migrations WHERE version = %s;",
                    (migration.name,),
                )
                applied = cursor.fetchone()
                if applied:
                    compatible_checksums = COMPATIBLE_MIGRATION_CHECKSUMS.get(migration.name, set())
                    if applied[0] != checksum and applied[0] not in compatible_checksums:
                        raise RuntimeError(
                            f"Applied migration {migration.name} has a different checksum. "
                            "Never edit a migration after it has run."
                        )
                    continue
                print(f"Applying {migration.name}...")
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, checksum_sha256) VALUES (%s, %s);",
                    (migration.name, checksum),
                )
            connection.commit()

    print("Database migrations are up to date.")


if __name__ == "__main__":
    main()
