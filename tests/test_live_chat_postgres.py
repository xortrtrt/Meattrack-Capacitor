from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
import pytest

from app import database, live_chat, repositories, security_controls


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
    requeued = live_chat.get_conversation(active["conversation_id"])
    assert requeued["status"] == "queued"
    assert requeued["claimed_at"] is not None

    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE chat_conversations SET queued_at = now() - interval '3 minutes' WHERE conversation_id = %s;",
                (active["conversation_id"],),
            )

    second_result = live_chat.maintenance()

    assert second_result["expired_queue"] == 0
    assert live_chat.get_conversation(active["conversation_id"])["status"] == "queued"


@pytest.mark.postgres
def test_transferred_accepted_chat_does_not_fall_back_to_contact_timeout(live_chat_database):
    leader = _seed_sales_leader(live_chat_database, "transfer-timeout")
    live_chat.set_presence(leader, "available")
    conversation = live_chat.create_conversation("Transferred Prospect", "requested")
    live_chat.claim_conversation(leader, conversation["conversation_id"])

    transferred = live_chat.transfer_conversation(leader, conversation["conversation_id"])
    assert transferred["status"] == "queued"
    assert transferred["claimed_at"] is not None

    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE chat_conversations SET queued_at = now() - interval '3 minutes' WHERE conversation_id = %s;",
                (conversation["conversation_id"],),
            )

    result = live_chat.maintenance()

    assert result["expired_queue"] == 0
    assert live_chat.get_conversation(conversation["conversation_id"])["status"] == "queued"


@pytest.mark.postgres
def test_transfer_to_second_leader_preserves_visitor_conversation(live_chat_database):
    first_leader = _seed_sales_leader(live_chat_database, "transfer-first")
    second_leader = _seed_sales_leader(live_chat_database, "transfer-second")
    live_chat.set_presence(first_leader, "available")
    live_chat.set_presence(second_leader, "available")
    conversation = live_chat.create_conversation("Transferred Visitor", "requested")
    conversation_id = conversation["conversation_id"]
    visitor_session = {"live_chat_conversation_id": conversation_id}

    first_claim = live_chat.claim_conversation(first_leader, conversation_id)
    transferred = live_chat.transfer_conversation(first_leader, conversation_id)
    second_claim = live_chat.claim_conversation(second_leader, conversation_id)
    stored = live_chat.get_conversation(conversation_id)

    assert first_claim["assigned_team_leader_account_id"] == first_leader
    assert transferred["conversation_id"] == conversation_id
    assert transferred["status"] == "queued"
    assert second_claim["conversation_id"] == conversation_id
    assert second_claim["assigned_team_leader_account_id"] == second_leader
    assert stored["status"] == "active"
    assert stored["assigned_team_leader_account_id"] == second_leader
    assert stored["assigned_team_leader_name"] == "Sales Leader transfer-second"
    assert live_chat.visitor_can_access(visitor_session, conversation_id) is True
    assert [message["content"] for message in live_chat.list_messages(conversation_id)] == [
        "Waiting for an available sales team leader.",
        "Sales Leader transfer-first joined the conversation.",
        "The conversation was returned to the team queue.",
        "Sales Leader transfer-second joined the conversation.",
    ]


@pytest.mark.postgres
def test_requested_inquiry_form_creates_one_inquiry_for_handling_leader(live_chat_database):
    leader = _seed_sales_leader(live_chat_database, "inquiry-form")
    live_chat.set_presence(leader, "available")
    conversation = live_chat.create_conversation("Qualified Prospect", "requested")
    conversation_id = conversation["conversation_id"]
    live_chat.claim_conversation(leader, conversation_id)

    requested = live_chat.request_inquiry_form(leader, conversation_id)
    repeated = live_chat.request_inquiry_form(leader, conversation_id)
    inquiry = repositories.add_inquiry(
        "Qualified Prospect",
        "Prospect Store",
        "prospect@example.test",
        "+63 917 123 4567",
        "Location: Lipa City\nSource: live sales conversation",
        chat_conversation_id=conversation_id,
        assigned_team_leader_account_id=leader,
    )

    assert requested["inquiry_form_requested_at"] is not None
    assert requested["system_message"]["content"] == "The sales team leader shared a reseller inquiry form."
    assert repeated["system_message"] is None
    assert inquiry["status"] == "assigned"
    assert inquiry["assigned_team_leader_account_id"] == leader
    assert live_chat.get_conversation(conversation_id)["status"] == "active"
    with pytest.raises(ValueError, match="already has a reseller inquiry"):
        repositories.add_inquiry(
            "Qualified Prospect",
            "Prospect Store",
            "prospect@example.test",
            "+63 917 123 4567",
            "Duplicate",
            chat_conversation_id=conversation_id,
            assigned_team_leader_account_id=leader,
        )


@pytest.mark.postgres
def test_rejected_inquiry_stores_reason_and_queues_recipient_email(live_chat_database):
    leader = _seed_sales_leader(live_chat_database, "reject-email")
    inquiry = repositories.add_inquiry(
        "Rejected Prospect",
        "Rejected Store",
        "rejected-prospect@example.test",
        "+63 917 000 1234",
        "Location: Batangas City",
        assigned_team_leader_account_id=leader,
    )

    rejected = repositories.reject_inquiry(
        inquiry["inquiry_id"],
        "The submitted business permit is incomplete.",
        reviewing_team_leader_account_id=leader,
    )

    assert rejected is True
    with psycopg2.connect(live_chat_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, rejection_reason FROM inquiries WHERE inquiry_id = %s;",
                (inquiry["inquiry_id"],),
            )
            assert cursor.fetchone() == ("rejected", "The submitted business permit is incomplete.")
            cursor.execute(
                "SELECT event_type, payload->>'to_email', payload->>'rejection_reason' "
                "FROM notification_outbox WHERE dedupe_key = %s;",
                (f"inquiry-rejected-{inquiry['inquiry_id']}",),
            )
            assert cursor.fetchone() == (
                "inquiry_rejected",
                "rejected-prospect@example.test",
                "The submitted business permit is incomplete.",
            )

    with pytest.raises(ValueError, match="5 to 1000"):
        repositories.reject_inquiry(inquiry["inquiry_id"], "", reviewing_team_leader_account_id=leader)


@pytest.mark.postgres
def test_completed_chat_rating_is_stored_once_for_assigned_leader(live_chat_database):
    leader = _seed_sales_leader(live_chat_database, "rated")
    live_chat.set_presence(leader, "available")
    conversation = live_chat.create_conversation("Rating Prospect", "requested")
    live_chat.claim_conversation(leader, conversation["conversation_id"])
    live_chat.close_conversation(conversation["conversation_id"], account_id=leader)

    rating = live_chat.rate_conversation(conversation["conversation_id"], 5)
    duplicate = live_chat.rate_conversation(conversation["conversation_id"], 5)

    assert rating["team_leader_account_id"] == leader
    assert rating["rating"] == 5
    assert duplicate["rating"] == 5
    assert live_chat.get_conversation(conversation["conversation_id"])["customer_service_rating"] == 5
    with pytest.raises(live_chat.LiveChatError, match="already been rated"):
        live_chat.rate_conversation(conversation["conversation_id"], 4)


@pytest.mark.postgres
def test_unaccepted_or_active_chat_cannot_be_rated(live_chat_database):
    waiting = live_chat.create_conversation("Waiting Rating Prospect", "requested")
    live_chat.close_conversation(waiting["conversation_id"], cancelled=True)
    with pytest.raises(live_chat.LiveChatError, match="not handled"):
        live_chat.rate_conversation(waiting["conversation_id"], 5)

    leader = _seed_sales_leader(live_chat_database, "active-rating")
    live_chat.set_presence(leader, "available")
    active = live_chat.create_conversation("Active Rating Prospect", "requested")
    live_chat.claim_conversation(leader, active["conversation_id"])
    with pytest.raises(live_chat.LiveChatError, match="End the live conversation"):
        live_chat.rate_conversation(active["conversation_id"], 5)

    for invalid in (0, 6, True):
        with pytest.raises(live_chat.LiveChatError, match="from 1 to 5"):
            live_chat.rate_conversation(active["conversation_id"], invalid)


@pytest.mark.postgres
def test_typing_state_is_ephemeral_and_available_to_polling_fallback(live_chat_database):
    leader = _seed_sales_leader(live_chat_database, "typing-fallback")
    live_chat.set_presence(leader, "available")
    conversation = live_chat.create_conversation("Typing Prospect", "requested")
    live_chat.claim_conversation(leader, conversation["conversation_id"])

    live_chat.set_typing_state(conversation["conversation_id"], "visitor", True)
    live_chat.set_typing_state(conversation["conversation_id"], "team_leader", True)

    active = live_chat.get_conversation(conversation["conversation_id"])
    assert active["visitor_is_typing"] is True
    assert active["leader_is_typing"] is True
    assigned = live_chat.workspace(leader)["assigned"]
    assert assigned[0]["visitor_is_typing"] is True

    live_chat.set_typing_state(conversation["conversation_id"], "visitor", False)
    live_chat.set_typing_state(conversation["conversation_id"], "team_leader", False)
    stopped = live_chat.get_conversation(conversation["conversation_id"])
    assert stopped["visitor_is_typing"] is False
    assert stopped["leader_is_typing"] is False


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
