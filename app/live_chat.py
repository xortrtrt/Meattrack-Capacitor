from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from typing import Iterable

from app.config import (
    ABLY_API_KEY,
    ABLY_REST_BASE_URL,
    ABLY_TOKEN_SIGNING_KEY,
    ABLY_TOKEN_TTL_SECONDS,
    CHAT_TRANSCRIPT_RETENTION_DAYS,
    LIVE_CHAT_ACCEPT_TIMEOUT_SECONDS,
    LIVE_CHAT_ENABLED,
    LIVE_CHAT_PRESENCE_STALE_SECONDS,
    LIVE_CHAT_RECONNECT_GRACE_SECONDS,
)
from app.database import clean_row, get_transaction_cursor


MESSAGE_MAX_LENGTH = 500
DISPLAY_NAME_MAX_LENGTH = 80
CONTROL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LiveChatError(ValueError):
    pass


class AblyUnavailable(RuntimeError):
    pass


def normalize_display_name(value: str) -> str:
    cleaned = " ".join(str(value).strip().split())
    if not 2 <= len(cleaned) <= DISPLAY_NAME_MAX_LENGTH or not any(char.isalpha() for char in cleaned):
        raise LiveChatError("Enter a valid display name using 2 to 80 characters.")
    if CONTROL_CHARACTERS_RE.search(cleaned):
        raise LiveChatError("The display name contains unsupported characters.")
    return cleaned


def normalize_message(value: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise LiveChatError("Enter a message before sending.")
    if len(cleaned) > MESSAGE_MAX_LENGTH:
        raise LiveChatError(f"Keep live-chat messages under {MESSAGE_MAX_LENGTH} characters.")
    if CONTROL_CHARACTERS_RE.search(cleaned):
        raise LiveChatError("The message contains unsupported control characters.")
    return cleaned


def normalize_contact(value: str) -> tuple[str, str]:
    cleaned = str(value).strip()
    if EMAIL_RE.fullmatch(cleaned.lower()):
        return "email", cleaned.lower()
    if re.fullmatch(r"[0-9+().\-\s]+", cleaned):
        digits = re.sub(r"\D+", "", cleaned)
        if 7 <= len(digits) <= 15 and ("+" not in cleaned or (cleaned.startswith("+") and cleaned.count("+") == 1)):
            return "phone", cleaned
    raise LiveChatError("Enter a valid email address or contact number with 7 to 15 digits.")


def conversation_channel(conversation_id: str) -> str:
    return f"support:{uuid.UUID(str(conversation_id))}"


def leader_channel(account_id: int) -> str:
    return f"leader:{int(account_id)}"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_ably_jwt(*, client_id: str, capabilities: dict[str, list[str]]) -> str:
    signing_key = ABLY_TOKEN_SIGNING_KEY or ABLY_API_KEY
    if not LIVE_CHAT_ENABLED or not signing_key:
        raise AblyUnavailable("Live messaging is not configured.")
    try:
        key_name, key_secret = signing_key.split(":", 1)
    except ValueError as exc:
        raise AblyUnavailable("The Ably API key is not configured correctly.") from exc
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": key_name}
    payload = {
        "iat": now,
        "exp": now + ABLY_TOKEN_TTL_SECONDS,
        "x-ably-clientId": client_id,
        "x-ably-capability": json.dumps(capabilities, separators=(",", ":")),
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(key_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url(signature)}"


def publish_event(channel: str, event_name: str, payload: dict) -> None:
    if not LIVE_CHAT_ENABLED or not ABLY_API_KEY:
        raise AblyUnavailable("Live messaging is not configured.")
    encoded_channel = urllib.parse.quote(channel, safe="")
    body = json.dumps({"name": event_name, "data": payload}, default=str).encode("utf-8")
    request = urllib.request.Request(
        f"{ABLY_REST_BASE_URL}/channels/{encoded_channel}/messages",
        data=body,
        method="POST",
        headers={
            "Authorization": "Basic " + base64.b64encode(ABLY_API_KEY.encode("utf-8")).decode("ascii"),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MEATTRACK/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status not in {200, 201}:
                raise AblyUnavailable(f"Ably returned HTTP {response.status}.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AblyUnavailable("Ably publication failed.") from exc


def _serialize(row: dict | None) -> dict | None:
    if row is None:
        return None
    clean = clean_row(row)
    for key, value in list(clean.items()):
        if isinstance(value, (datetime, uuid.UUID)):
            clean[key] = str(value)
    return clean


def create_conversation(display_name: str, escalation_reason: str) -> dict:
    conversation_id = str(uuid.uuid4())
    cleaned_name = normalize_display_name(display_name)
    reason = " ".join(str(escalation_reason or "requested").split())[:80] or "requested"
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_conversations (conversation_id, display_name, escalation_reason)
            VALUES (%s, %s, %s)
            RETURNING *;
            """,
            (conversation_id, cleaned_name, reason),
        )
        row = cur.fetchone()
        cur.execute(
            """
            INSERT INTO chat_messages (conversation_id, client_message_id, sender_type, content)
            VALUES (%s, %s, 'system', %s)
            RETURNING *;
            """,
            (conversation_id, str(uuid.uuid4()), "Waiting for an available sales team leader."),
        )
    return _serialize(row) or {}


def get_conversation(conversation_id: str) -> dict | None:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT c.*, a.name AS assigned_team_leader_name,
                   r.rating AS customer_service_rating,
                   EXISTS (
                       SELECT 1 FROM inquiries i WHERE i.chat_conversation_id = c.conversation_id
                   ) AS has_reseller_inquiry,
                   COALESCE(c.visitor_typing_until > now(), false) AS visitor_is_typing,
                   COALESCE(c.leader_typing_until > now(), false) AS leader_is_typing,
                   CASE
                       WHEN c.status = 'queued'
                        AND c.claimed_at IS NULL
                        AND c.queued_at <= now() - make_interval(secs => %s)
                       THEN true ELSE false
                   END AS queue_expired
            FROM chat_conversations c
            LEFT JOIN accounts a ON a.account_id = c.assigned_team_leader_account_id
            LEFT JOIN chat_service_ratings r ON r.conversation_id = c.conversation_id
            WHERE c.conversation_id = %s;
            """,
            (LIVE_CHAT_ACCEPT_TIMEOUT_SECONDS, conversation_id),
        )
        row = cur.fetchone()
        if row and row["queue_expired"]:
            cur.execute(
                """
                UPDATE chat_conversations
                SET status = 'awaiting_contact', updated_at = now()
                WHERE conversation_id = %s AND status = 'queued'
                RETURNING *;
                """,
                (conversation_id,),
            )
            changed = cur.fetchone()
            if changed:
                row = changed
                row["assigned_team_leader_name"] = None
    return _serialize(row)


def visitor_can_access(session: dict, conversation_id: str) -> bool:
    return str(session.get("live_chat_conversation_id") or "") == str(conversation_id)


def leader_can_access(account_id: int, conversation_id: str) -> bool:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM chat_conversations
            WHERE conversation_id = %s AND assigned_team_leader_account_id = %s
              AND status IN ('active', 'follow_up', 'closed');
            """,
            (conversation_id, account_id),
        )
        return cur.fetchone() is not None


def set_typing_state(conversation_id: str, sender_type: str, is_typing: bool) -> None:
    column = {
        "visitor": "visitor_typing_until",
        "team_leader": "leader_typing_until",
    }.get(sender_type)
    if column is None or not isinstance(is_typing, bool):
        raise LiveChatError("Invalid typing state.")
    value_sql = "now() + interval '3 seconds'" if is_typing else "NULL"
    with get_transaction_cursor() as cur:
        cur.execute(
            f"""
            UPDATE chat_conversations
            SET {column} = {value_sql}
            WHERE conversation_id = %s AND status = 'active';
            """,
            (conversation_id,),
        )
        if cur.rowcount != 1:
            raise LiveChatError("This live conversation is not active.")


def list_messages(conversation_id: str, after_id: int = 0, limit: int = 100) -> list[dict]:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT m.chat_message_id, m.client_message_id, m.sender_type, m.content,
                   m.publication_status, m.created_at, a.name AS sender_name
            FROM chat_messages m
            LEFT JOIN accounts a ON a.account_id = m.sender_account_id
            WHERE m.conversation_id = %s AND m.chat_message_id > %s
            ORDER BY m.chat_message_id
            LIMIT %s;
            """,
            (conversation_id, max(0, int(after_id)), min(max(1, int(limit)), 200)),
        )
        return [_serialize(dict(row)) for row in cur.fetchall()]


def add_message(
    conversation_id: str,
    *,
    client_message_id: str,
    sender_type: str,
    content: str,
    sender_account_id: int | None = None,
) -> tuple[dict, bool]:
    cleaned = normalize_message(content)
    try:
        message_uuid = str(uuid.UUID(str(client_message_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise LiveChatError("A valid message id is required.") from exc
    if sender_type not in {"visitor", "team_leader", "system"}:
        raise LiveChatError("Unsupported message sender.")
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM chat_conversations WHERE conversation_id = %s FOR UPDATE;", (conversation_id,))
        conversation = cur.fetchone()
        if not conversation:
            raise LiveChatError("Conversation not found.")
        if conversation["status"] != "active":
            raise LiveChatError("This live conversation is not active.")
        if sender_type == "team_leader" and int(conversation["assigned_team_leader_account_id"] or 0) != int(sender_account_id or 0):
            raise LiveChatError("This conversation is assigned to another team leader.")
        cur.execute(
            """
            INSERT INTO chat_messages (
                conversation_id, client_message_id, sender_type, sender_account_id, content
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id, client_message_id) DO NOTHING
            RETURNING *;
            """,
            (conversation_id, message_uuid, sender_type, sender_account_id, cleaned),
        )
        message = cur.fetchone()
        created = message is not None
        if not message:
            cur.execute(
                "SELECT * FROM chat_messages WHERE conversation_id = %s AND client_message_id = %s;",
                (conversation_id, message_uuid),
            )
            message = cur.fetchone()
        timestamp_field = "last_visitor_at" if sender_type == "visitor" else "last_leader_at"
        cur.execute(
            f"UPDATE chat_conversations SET {timestamp_field} = now(), updated_at = now() WHERE conversation_id = %s;",
            (conversation_id,),
        )
    return _serialize(message) or {}, created


def mark_published(message_id: int) -> None:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_messages
            SET publication_status = 'published', published_at = now(),
                publish_attempts = publish_attempts + 1, last_publish_error = NULL
            WHERE chat_message_id = %s;
            """,
            (message_id,),
        )


def mark_publish_failed(message_id: int, error: str) -> None:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_messages
            SET publication_status = 'failed', publish_attempts = publish_attempts + 1,
                last_publish_error = %s
            WHERE chat_message_id = %s;
            """,
            (str(error)[:300], message_id),
        )


def message_event(message: dict, conversation_id: str) -> dict:
    return {
        "conversation_id": str(conversation_id),
        "message_id": int(message["chat_message_id"]),
        "client_message_id": str(message["client_message_id"]),
        "sender_type": message["sender_type"],
        "sender_name": message.get("sender_name"),
        "text": message["content"],
        "created_at": str(message["created_at"]),
    }


def publish_message(message: dict, conversation_id: str) -> None:
    publish_event(conversation_channel(conversation_id), "chat.message", message_event(message, conversation_id))
    mark_published(int(message["chat_message_id"]))


def list_available_leader_ids() -> list[int]:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT p.account_id
            FROM team_leader_presence p
            JOIN accounts a ON a.account_id = p.account_id
            WHERE a.account_type = 'team_leader' AND a.team_leader_role = 'sales'
              AND a.is_active = true AND p.availability = 'available'
              AND p.active_conversation_id IS NULL
              AND p.heartbeat_at >= now() - make_interval(secs => %s)
            ORDER BY p.heartbeat_at DESC, p.account_id;
            """,
            (LIVE_CHAT_PRESENCE_STALE_SECONDS,),
        )
        return [int(row["account_id"]) for row in cur.fetchall()]


def notify_available_leaders(conversation: dict) -> None:
    payload = {
        "conversation_id": str(conversation["conversation_id"]),
        "display_name": conversation["display_name"],
        "status": conversation["status"],
        "queued_at": str(conversation["queued_at"]),
    }
    for account_id in list_available_leader_ids():
        try:
            publish_event(leader_channel(account_id), "queue.updated", payload)
        except AblyUnavailable:
            continue


def set_presence(account_id: int, availability: str) -> dict:
    if availability not in {"available", "busy", "offline"}:
        raise LiveChatError("Choose Available or Busy.")
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT account_id FROM accounts
            WHERE account_id = %s AND account_type = 'team_leader'
              AND team_leader_role = 'sales' AND is_active = true;
            """,
            (account_id,),
        )
        if not cur.fetchone():
            raise LiveChatError("Only active sales team leaders can use live chat.")
        cur.execute(
            """
            INSERT INTO team_leader_presence (account_id, availability, heartbeat_at)
            VALUES (%s, %s, now())
            ON CONFLICT (account_id) DO UPDATE
            SET availability = CASE
                    WHEN team_leader_presence.active_conversation_id IS NOT NULL THEN 'busy'
                    ELSE EXCLUDED.availability
                END,
                heartbeat_at = now(), updated_at = now()
            RETURNING *;
            """,
            (account_id, availability),
        )
        return _serialize(cur.fetchone()) or {}


def heartbeat(account_id: int) -> dict:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_leader_presence (account_id, availability, heartbeat_at)
            VALUES (%s, 'offline', now())
            ON CONFLICT (account_id) DO UPDATE
            SET heartbeat_at = now(), updated_at = now()
            RETURNING *;
            """,
            (account_id,),
        )
        return _serialize(cur.fetchone()) or {}


def claim_conversation(account_id: int, conversation_id: str) -> dict:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT p.*, a.name
            FROM team_leader_presence p
            JOIN accounts a ON a.account_id = p.account_id
            WHERE p.account_id = %s AND a.account_type = 'team_leader'
              AND a.team_leader_role = 'sales' AND a.is_active = true
            FOR UPDATE OF p;
            """,
            (account_id,),
        )
        presence = cur.fetchone()
        if not presence or presence["availability"] != "available" or presence["active_conversation_id"] is not None:
            raise LiveChatError("Set your status to Available before accepting a conversation.")
        if not presence["heartbeat_at"] or (
            datetime.now(presence["heartbeat_at"].tzinfo) - presence["heartbeat_at"]
        ).total_seconds() > LIVE_CHAT_PRESENCE_STALE_SECONDS:
            raise LiveChatError("Your live-chat session is offline. Refresh and try again.")
        cur.execute("SELECT * FROM chat_conversations WHERE conversation_id = %s FOR UPDATE;", (conversation_id,))
        conversation = cur.fetchone()
        if not conversation or conversation["status"] != "queued":
            raise LiveChatError("That conversation is no longer waiting.")
        cur.execute(
            """
            UPDATE chat_conversations
            SET status = 'active', assigned_team_leader_account_id = %s,
                claimed_at = now(), updated_at = now()
            WHERE conversation_id = %s
            RETURNING *;
            """,
            (account_id, conversation_id),
        )
        claimed = cur.fetchone()
        cur.execute(
            """
            UPDATE team_leader_presence
            SET availability = 'busy', active_conversation_id = %s,
                heartbeat_at = now(), updated_at = now()
            WHERE account_id = %s;
            """,
            (conversation_id, account_id),
        )
        cur.execute(
            """
            INSERT INTO chat_messages (conversation_id, client_message_id, sender_type, content)
            VALUES (%s, %s, 'system', %s)
            RETURNING *;
            """,
            (conversation_id, str(uuid.uuid4()), f"{presence['name']} joined the conversation."),
        )
        system_message = _serialize(cur.fetchone())
    result = _serialize(claimed) or {}
    result["assigned_team_leader_name"] = presence["name"]
    result["system_message"] = system_message
    return result


def transfer_conversation(account_id: int, conversation_id: str) -> dict:
    """Atomically return an assigned live conversation to the shared queue."""
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM chat_conversations WHERE conversation_id = %s FOR UPDATE;", (conversation_id,))
        conversation = cur.fetchone()
        if not conversation:
            raise LiveChatError("Conversation not found.")
        if conversation["status"] != "active" or int(conversation["assigned_team_leader_account_id"] or 0) != int(account_id):
            raise LiveChatError("Only your active conversation can be transferred.")
        cur.execute(
            """
            UPDATE chat_conversations
            SET status = 'queued', assigned_team_leader_account_id = NULL,
                queued_at = now(), updated_at = now()
            WHERE conversation_id = %s
            RETURNING *;
            """,
            (conversation_id,),
        )
        transferred = cur.fetchone()
        cur.execute(
            """
            UPDATE team_leader_presence
            SET active_conversation_id = NULL,
                availability = CASE
                    WHEN heartbeat_at >= now() - make_interval(secs => %s) THEN 'available'
                    ELSE 'offline'
                END,
                updated_at = now()
            WHERE account_id = %s;
            """,
            (LIVE_CHAT_PRESENCE_STALE_SECONDS, account_id),
        )
        cur.execute(
            """
            INSERT INTO chat_messages (conversation_id, client_message_id, sender_type, content)
            VALUES (%s, %s, 'system', 'The conversation was returned to the team queue.')
            RETURNING *;
            """,
            (conversation_id, str(uuid.uuid4())),
        )
        system_message = _serialize(cur.fetchone())
    result = _serialize(transferred) or {}
    result["system_message"] = system_message
    return result


def request_inquiry_form(account_id: int, conversation_id: str) -> dict:
    """Record and announce that the assigned leader offered the formal inquiry form."""
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM chat_conversations WHERE conversation_id = %s FOR UPDATE;", (conversation_id,))
        conversation = cur.fetchone()
        if not conversation:
            raise LiveChatError("Conversation not found.")
        if conversation["status"] != "active" or int(conversation["assigned_team_leader_account_id"] or 0) != int(account_id):
            raise LiveChatError("Only your active conversation can receive an inquiry form.")
        first_request = conversation.get("inquiry_form_requested_at") is None
        cur.execute(
            """
            UPDATE chat_conversations
            SET inquiry_form_requested_at = COALESCE(inquiry_form_requested_at, now()), updated_at = now()
            WHERE conversation_id = %s
            RETURNING *;
            """,
            (conversation_id,),
        )
        updated = cur.fetchone()
        system_message = None
        if first_request:
            cur.execute(
                """
                INSERT INTO chat_messages (conversation_id, client_message_id, sender_type, content)
                VALUES (%s, %s, 'system', 'The sales team leader shared a reseller inquiry form.')
                RETURNING *;
                """,
                (conversation_id, str(uuid.uuid4())),
            )
            system_message = _serialize(cur.fetchone())
    result = _serialize(updated) or {}
    result["system_message"] = system_message
    return result


def close_conversation(conversation_id: str, *, account_id: int | None = None, cancelled: bool = False) -> dict:
    with get_transaction_cursor() as cur:
        cur.execute("SELECT * FROM chat_conversations WHERE conversation_id = %s FOR UPDATE;", (conversation_id,))
        conversation = cur.fetchone()
        if not conversation:
            raise LiveChatError("Conversation not found.")
        if account_id is not None and int(conversation["assigned_team_leader_account_id"] or 0) != int(account_id):
            raise LiveChatError("This conversation is assigned to another team leader.")
        next_status = "cancelled" if cancelled else "closed"
        cur.execute(
            """
            UPDATE chat_conversations
            SET status = %s, closed_at = now(), updated_at = now()
            WHERE conversation_id = %s
            RETURNING *;
            """,
            (next_status, conversation_id),
        )
        closed = cur.fetchone()
        if conversation["assigned_team_leader_account_id"]:
            cur.execute(
                """
                UPDATE team_leader_presence
                SET active_conversation_id = NULL,
                    availability = CASE
                        WHEN heartbeat_at >= now() - make_interval(secs => %s) THEN 'available'
                        ELSE 'offline'
                    END,
                    updated_at = now()
                WHERE account_id = %s;
                """,
                (LIVE_CHAT_PRESENCE_STALE_SECONDS, conversation["assigned_team_leader_account_id"]),
            )
    return _serialize(closed) or {}


def rate_conversation(conversation_id: str, rating: int) -> dict:
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise LiveChatError("Choose a customer-service rating from 1 to 5.")
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT conversation_id, status, claimed_at, assigned_team_leader_account_id
            FROM chat_conversations
            WHERE conversation_id = %s
            FOR UPDATE;
            """,
            (conversation_id,),
        )
        conversation = cur.fetchone()
        if not conversation:
            raise LiveChatError("Conversation not found.")
        if conversation["status"] not in {"closed", "cancelled"}:
            raise LiveChatError("End the live conversation before rating customer service.")
        leader_id = conversation["assigned_team_leader_account_id"]
        if not conversation["claimed_at"] or not leader_id:
            raise LiveChatError("This conversation was not handled by a sales team leader.")
        cur.execute(
            "SELECT conversation_id, team_leader_account_id, rating, created_at FROM chat_service_ratings WHERE conversation_id = %s;",
            (conversation_id,),
        )
        existing = cur.fetchone()
        if existing:
            if int(existing["rating"]) != rating:
                raise LiveChatError("Customer service has already been rated for this conversation.")
            return _serialize(existing) or {}
        cur.execute(
            """
            INSERT INTO chat_service_ratings (conversation_id, team_leader_account_id, rating)
            VALUES (%s, %s, %s)
            RETURNING conversation_id, team_leader_account_id, rating, created_at;
            """,
            (conversation_id, leader_id, rating),
        )
        return _serialize(cur.fetchone()) or {}


def save_fallback_contact(conversation_id: str, contact: str) -> dict:
    contact_type, cleaned = normalize_contact(contact)
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_conversations
            SET fallback_contact_type = %s, fallback_contact = %s,
                status = 'follow_up', updated_at = now()
            WHERE conversation_id = %s AND status = 'awaiting_contact'
            RETURNING *;
            """,
            (contact_type, cleaned, conversation_id),
        )
        row = cur.fetchone()
        if not row:
            raise LiveChatError("This conversation is not waiting for follow-up details.")
        cur.execute(
            """
            SELECT a.account_id
            FROM accounts a
            LEFT JOIN chat_conversations c
              ON c.assigned_team_leader_account_id = a.account_id
             AND c.status IN ('active', 'follow_up')
            WHERE a.account_type = 'team_leader' AND a.team_leader_role = 'sales' AND a.is_active = true
            GROUP BY a.account_id
            ORDER BY count(c.conversation_id), a.account_id
            LIMIT 1;
            """
        )
        leader = cur.fetchone()
        if leader:
            cur.execute(
                "UPDATE chat_conversations SET assigned_team_leader_account_id = %s WHERE conversation_id = %s RETURNING *;",
                (leader["account_id"], conversation_id),
            )
            row = cur.fetchone()
    return _serialize(row) or {}


def workspace(account_id: int) -> dict:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE team_leader_presence
            SET availability = 'offline', updated_at = now()
            WHERE heartbeat_at < now() - make_interval(secs => %s)
              AND active_conversation_id IS NULL AND availability <> 'offline';
            """,
            (LIVE_CHAT_PRESENCE_STALE_SECONDS,),
        )
        cur.execute("SELECT * FROM team_leader_presence WHERE account_id = %s;", (account_id,))
        presence = _serialize(cur.fetchone())
        cur.execute(
            """
            SELECT conversation_id, display_name, status, escalation_reason, queued_at, updated_at
            FROM chat_conversations
            WHERE status = 'queued'
            ORDER BY queued_at
            LIMIT 50;
            """
        )
        queue = [_serialize(dict(row)) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT conversation_id, display_name, status, escalation_reason, fallback_contact_type,
                   fallback_contact, queued_at, claimed_at, inquiry_form_requested_at, updated_at,
                   EXISTS (
                       SELECT 1 FROM inquiries i WHERE i.chat_conversation_id = chat_conversations.conversation_id
                   ) AS has_reseller_inquiry,
                   COALESCE(visitor_typing_until > now(), false) AS visitor_is_typing,
                   COALESCE(leader_typing_until > now(), false) AS leader_is_typing
            FROM chat_conversations
            WHERE assigned_team_leader_account_id = %s
              AND status IN ('active', 'follow_up', 'closed')
            ORDER BY CASE WHEN status = 'active' THEN 0 WHEN status = 'follow_up' THEN 1 ELSE 2 END,
                     updated_at DESC
            LIMIT 50;
            """,
            (account_id,),
        )
        assigned = [_serialize(dict(row)) for row in cur.fetchall()]
    return {"account_id": int(account_id), "presence": presence, "queue": queue, "assigned": assigned}


def pending_messages(limit: int = 50) -> list[dict]:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT * FROM chat_messages
            WHERE publication_status <> 'published' AND publish_attempts < 8
              AND created_at >= now() - interval '1 day'
            ORDER BY chat_message_id
            LIMIT %s;
            """,
            (limit,),
        )
        return [_serialize(dict(row)) for row in cur.fetchall()]


def maintenance() -> dict[str, int]:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_conversations
            SET status = 'awaiting_contact', updated_at = now()
            WHERE status = 'queued'
              AND claimed_at IS NULL
              AND queued_at <= now() - make_interval(secs => %s);
            """,
            (LIVE_CHAT_ACCEPT_TIMEOUT_SECONDS,),
        )
        expired_queue = cur.rowcount
        cur.execute(
            """
            WITH stale AS (
                SELECT c.conversation_id, c.assigned_team_leader_account_id
                FROM chat_conversations c
                LEFT JOIN team_leader_presence p ON p.account_id = c.assigned_team_leader_account_id
                WHERE c.status = 'active'
                  AND (p.heartbeat_at IS NULL OR p.heartbeat_at < now() - make_interval(secs => %s))
                FOR UPDATE OF c
            )
            UPDATE chat_conversations c
            SET status = 'queued', assigned_team_leader_account_id = NULL,
                queued_at = now(), updated_at = now()
            FROM stale s
            WHERE c.conversation_id = s.conversation_id;
            """,
            (LIVE_CHAT_RECONNECT_GRACE_SECONDS,),
        )
        requeued = cur.rowcount
        cur.execute(
            """
            UPDATE team_leader_presence p
            SET active_conversation_id = NULL, availability = 'offline', updated_at = now()
            WHERE p.active_conversation_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM chat_conversations c
                  WHERE c.conversation_id = p.active_conversation_id AND c.status = 'active'
              );
            """
        )
        cur.execute(
            """
            DELETE FROM chat_messages m
            USING chat_conversations c
            WHERE c.conversation_id = m.conversation_id
              AND c.status IN ('closed', 'cancelled')
              AND c.closed_at < now() - make_interval(days => %s);
            """,
            (CHAT_TRANSCRIPT_RETENTION_DAYS,),
        )
        deleted = cur.rowcount
        cur.execute("DELETE FROM rate_limit_buckets WHERE expires_at < now() - interval '1 day';")
    return {"expired_queue": expired_queue, "requeued": requeued, "deleted_messages": deleted}
