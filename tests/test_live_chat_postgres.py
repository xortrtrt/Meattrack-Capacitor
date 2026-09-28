from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
import pytest

from app import database, live_chat, security_controls


def _dsn_with_search_path(dsn: str, schema: str) -> str:
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@pytest.fixture
def live_chat_database(monkeypatch):
    schema = f"test_live_chat_{uuid.uuid4().hex}"
    try:
        admin = psycopg2.connect(database.DSN)
    except psycopg2.OperationalError:
        pytest.skip("Local PostgreSQL is unavailable")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    scoped_dsn = _dsn_with_search_path(database.DSN, schema)
    with psycopg2.connect(scoped_dsn) as scoped:
        with scoped.cursor() as cursor:
            baseline = Path("database/schema.sql").read_text(encoding="utf-8").strip()
            cursor.execute(baseline.removeprefix("BEGIN;").strip().removesuffix("COMMIT;").strip())

    previous_pool = database.pool
    database.pool = ThreadedConnectionPool(1, 16, scoped_dsn)
    monkeypatch.setattr(security_controls, "AUTH_RATE_LIMIT_ENABLED", True)
    try:
        yield scoped_dsn
    finally:
        if database.pool is not None:
            database.pool.closeall()
        database.pool = previous_pool
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def _seed_sales_leader(scoped_dsn: str, suffix: str) -> int:
    with psycopg2.connect(scoped_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO accounts (account_type, name, email, password_hash, team_leader_role)
                VALUES ('team_leader', %s, %s, 'not-used-for-login', 'sales')
                RETURNING account_id;
                """,
                (f"Sales Leader {suffix}", f"leader-{suffix}@test.local"),
            )
            return int(cursor.fetchone()[0])


@pytest.mark.postgres
def test_anonymous_handoff_creates_no_account_reseller_or_inquiry(live_chat_database):
    before = {}
    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            for table in ("accounts", "resellers", "inquiries"):
                cursor.execute(sql.SQL("SELECT count(*) FROM {};").format(sql.Identifier(table)))
                before[table] = cursor.fetchone()[0]

    conversation = live_chat.create_conversation("Fresh Prospect", "requested")

    assert conversation["status"] == "queued"
    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            for table in ("accounts", "resellers", "inquiries"):
                cursor.execute(sql.SQL("SELECT count(*) FROM {};").format(sql.Identifier(table)))
                assert cursor.fetchone()[0] == before[table]


@pytest.mark.postgres
def test_concurrent_claim_assigns_exactly_one_leader(live_chat_database):
    first = _seed_sales_leader(live_chat_database, "one")
    second = _seed_sales_leader(live_chat_database, "two")
    live_chat.set_presence(first, "available")
    live_chat.set_presence(second, "available")
    conversation = live_chat.create_conversation("Queue Prospect", "requested")

    def claim(account_id: int) -> bool:
        try:
            live_chat.claim_conversation(account_id, conversation["conversation_id"])
            return True
        except live_chat.LiveChatError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, (first, second)))

    assert sum(claimed) == 1
    stored = live_chat.get_conversation(conversation["conversation_id"])
    assert stored["status"] == "active"
    assert stored["assigned_team_leader_account_id"] in {first, second}


@pytest.mark.postgres
def test_message_idempotency_and_database_backfill(live_chat_database):
    leader = _seed_sales_leader(live_chat_database, "messages")
    live_chat.set_presence(leader, "available")
    conversation = live_chat.create_conversation("Message Prospect", "requested")
    live_chat.claim_conversation(leader, conversation["conversation_id"])
    client_message_id = str(uuid.uuid4())

    first, first_created = live_chat.add_message(
        conversation["conversation_id"],
        client_message_id=client_message_id,
        sender_type="visitor",
        content="Hello from a prospect",
    )
    duplicate, duplicate_created = live_chat.add_message(
        conversation["conversation_id"],
        client_message_id=client_message_id,
        sender_type="visitor",
        content="This retry must not create another row",
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate["chat_message_id"] == first["chat_message_id"]
    backfill = live_chat.list_messages(conversation["conversation_id"], after_id=first["chat_message_id"] - 1)
    assert [item["content"] for item in backfill] == ["Hello from a prospect"]


@pytest.mark.postgres
def test_queue_timeout_and_stale_leader_requeue(live_chat_database):
    conversation = live_chat.create_conversation("Waiting Prospect", "requested")
    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE chat_conversations SET queued_at = now() - interval '3 minutes' WHERE conversation_id = %s;",
                (conversation["conversation_id"],),
            )
    assert live_chat.get_conversation(conversation["conversation_id"])["status"] == "awaiting_contact"

    leader = _seed_sales_leader(live_chat_database, "stale")
    live_chat.set_presence(leader, "available")
    active = live_chat.create_conversation("Reconnect Prospect", "requested")
    live_chat.claim_conversation(leader, active["conversation_id"])
    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE team_leader_presence SET heartbeat_at = now() - interval '2 minutes' WHERE account_id = %s;",
                (leader,),
            )

    result = live_chat.maintenance()

    assert result["requeued"] == 1
    assert live_chat.get_conversation(active["conversation_id"])["status"] == "queued"


@pytest.mark.postgres
def test_rate_limit_counter_is_atomic_under_concurrency(live_chat_database):
    def consume(_index: int) -> bool:
        try:
            security_controls.consume_rate_limit("concurrent_test", "same-subject", 5, 60, "limited")
            return True
        except security_controls.RateLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        accepted = list(executor.map(consume, range(12)))

    assert sum(accepted) == 5


@pytest.mark.postgres
def test_completed_transcript_content_is_removed_after_retention(live_chat_database):
    conversation = live_chat.create_conversation("Old Prospect", "requested")
    live_chat.close_conversation(conversation["conversation_id"], cancelled=True)
    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE chat_conversations SET closed_at = now() - interval '31 days' WHERE conversation_id = %s;",
                (conversation["conversation_id"],),
            )

    result = live_chat.maintenance()

    assert result["deleted_messages"] == 1
    assert live_chat.list_messages(conversation["conversation_id"]) == []
