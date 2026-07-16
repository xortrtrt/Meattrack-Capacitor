from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"
DATA_FILE = PROJECT_ROOT / "database" / "supabase_data_import.sql"
SECURITY_FILE = PROJECT_ROOT / "database" / "supabase_security.sql"
APP_TABLES = (
    "forecast_results",
    "forecast_runs",
    "alerts",
    "sales_reports",
    "order_items",
    "orders",
    "product_recipes",
    "inventory_batches",
    "inventory_items",
    "media_assets",
    "activity_logs",
    "accounts",
    "resellers",
    "inquiries",
    "departments",
)


def secured_url(value: str) -> str:
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if not (host.endswith(".supabase.co") or host.endswith(".pooler.supabase.com")):
        raise ValueError("SUPABASE_DB_URL must point to a Supabase database or session pooler")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def without_transaction_markers(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )


def url_from_credentials_file(path: Path) -> str:
    """Read the simple password + project URL note without echoing either value."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    connection_url = next(
        (line for line in lines if line.startswith(("postgresql://", "postgres://"))),
        None,
    )
    if connection_url:
        return connection_url
    project_url = next(
        (
            line
            for line in lines
            if line.startswith("https://")
            and (urlsplit(line).hostname or "").endswith(".supabase.co")
        ),
        None,
    )
    password = next(
        (
            line
            for line in lines
            if "://" not in line and len(line) >= 12 and line.count(".") < 2
        ),
        None,
    )
    if not project_url or not password:
        raise ValueError("Credentials file must contain a database password and Supabase project HTTPS URL")
    project_ref = (urlsplit(project_url).hostname or "").removesuffix(".supabase.co")
    return f"postgresql://postgres:{quote(password, safe='')}@db.{project_ref}.supabase.co:5432/postgres"


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the MEATTRACK schema and exported data into Supabase.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--reset",
        action="store_true",
        help="Replace the MEATTRACK tables and import all exported data.",
    )
    action.add_argument("--check", action="store_true", help="Only verify the target connection.")
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="Note containing the password and project HTTPS URL; values are never printed.",
    )
    args = parser.parse_args()

    raw_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not raw_url and args.credentials_file:
        raw_url = url_from_credentials_file(args.credentials_file)
    if not raw_url:
        parser.error("Set SUPABASE_DB_URL or pass --credentials-file")
    url = secured_url(raw_url)

    if args.check:
        print("Checking the Supabase connection over TLS...")
        with psycopg2.connect(url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT tablename, rowsecurity
                    FROM pg_tables
                    WHERE schemaname = 'public' AND tablename = ANY(%s)
                    ORDER BY tablename
                    """,
                    (list(APP_TABLES),),
                )
                table_security = dict(cursor.fetchall())
                missing_tables = sorted(set(APP_TABLES) - set(table_security))
                unsecured_tables = sorted(
                    table for table, row_security in table_security.items() if not row_security
                )
                if missing_tables:
                    raise RuntimeError(
                        f"Migration verification failed; missing tables: {', '.join(missing_tables)}"
                    )
                if unsecured_tables:
                    raise RuntimeError(
                        "Migration verification failed; RLS is disabled on: "
                        + ", ".join(unsecured_tables)
                    )
                cursor.execute("SELECT count(*) FROM accounts")
                account_count = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM inventory_items")
                item_count = cursor.fetchone()[0]
        print(
            "Connection and migration verified; "
            f"{len(table_security)} app tables have RLS enabled, with "
            f"{account_count} accounts and {item_count} inventory items."
        )
        return

    schema_sql = without_transaction_markers(SCHEMA_FILE.read_text(encoding="utf-8"))
    data_sql = without_transaction_markers(DATA_FILE.read_text(encoding="utf-8"))
    security_sql = SECURITY_FILE.read_text(encoding="utf-8")

    print("Connecting to Supabase over TLS...")
    with psycopg2.connect(url, connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            print("Replacing MEATTRACK tables while preserving Supabase's public schema grants...")
            for table in APP_TABLES:
                cursor.execute(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
            cursor.execute(schema_sql)
            cursor.execute(data_sql)
            cursor.execute(security_sql)
            cursor.execute("SELECT count(*) FROM accounts")
            account_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM inventory_items")
            item_count = cursor.fetchone()[0]
    print(f"Migration complete: {account_count} accounts and {item_count} inventory items verified.")


if __name__ == "__main__":
    main()
