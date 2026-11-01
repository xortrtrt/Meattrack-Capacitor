from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import overload

from app.database import clean_row, get_transaction_cursor


MESSAGE_MAX_LENGTH = 500
CONTROL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResellerMessagingError(ValueError):
    pass


def normalize_message(value: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ResellerMessagingError("Enter a message before sending.")
    if len(cleaned) > MESSAGE_MAX_LENGTH:
        raise ResellerMessagingError(f"Keep messages under {MESSAGE_MAX_LENGTH} characters.")
    if CONTROL_CHARACTERS_RE.search(cleaned):
        raise ResellerMessagingError("The message contains unsupported control characters.")
    return cleaned


@overload
def _serialize(row: dict) -> dict: ...


@overload
def _serialize(row: None) -> None: ...


def _serialize(row: dict | None) -> dict | None:
    if row is None:
        return None
    serialized = clean_row(row)
    for key, value in list(serialized.items()):
        if isinstance(value, (datetime, uuid.UUID)):
            serialized[key] = str(value)
    return serialized


def _reseller_participant(cur, account_id: int, *, for_update: bool = False) -> dict:
    cur.execute(
        f"""
        SELECT a.account_id AS reseller_account_id,
               a.name AS reseller_name,
               a.profile_image_path AS reseller_profile_image_path,
               a.profile_image_position_x AS reseller_profile_image_position_x,
               a.profile_image_position_y AS reseller_profile_image_position_y,
               a.profile_image_scale AS reseller_profile_image_scale,
               a.email AS reseller_email,
               r.business_name,
               r.team_leader_account_id,
               tl.name AS team_leader_name,
               tl.email AS team_leader_email,
               tl.profile_image_path AS team_leader_profile_image_path,
               tl.profile_image_position_x AS team_leader_profile_image_position_x,
               tl.profile_image_position_y AS team_leader_profile_image_position_y,
               tl.profile_image_scale AS team_leader_profile_image_scale
        FROM accounts a
        JOIN resellers r
          ON r.reseller_id = a.reseller_id
          OR (a.reseller_id IS NULL AND lower(r.email) = lower(a.email))
        LEFT JOIN accounts tl ON tl.account_id = r.team_leader_account_id
        WHERE a.account_id = %s
          AND a.account_type = 'reseller'
          AND a.is_active = true
          AND r.reseller_status = 'active'
        LIMIT 1
        {"FOR UPDATE OF a, r" if for_update else ""};
        """,
        (account_id,),
    )
    participant = cur.fetchone()
    if not participant:
        raise ResellerMessagingError("Your active reseller profile was not found.")
    return dict(participant)


def _conversation_for_pair(cur, reseller_account_id: int, team_leader_account_id: int) -> dict | None:
    cur.execute(
        """
        SELECT *
        FROM reseller_conversations
        WHERE reseller_account_id = %s AND team_leader_account_id = %s;
        """,
        (reseller_account_id, team_leader_account_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _ensure_conversation(cur, participant: dict) -> dict:
    leader_id = participant.get("team_leader_account_id")
    if not leader_id:
        raise ResellerMessagingError("A sales leader must be assigned before you can send messages.")
    cur.execute(
        """
        INSERT INTO reseller_conversations (reseller_account_id, team_leader_account_id)
        VALUES (%s, %s)
        ON CONFLICT (reseller_account_id, team_leader_account_id) DO UPDATE
        SET updated_at = now()
        RETURNING *;
        """,
        (participant["reseller_account_id"], leader_id),
    )
    return dict(cur.fetchone())


def _list_messages(
    cur,
    conversation_id: int,
    after_id: int = 0,
    *,
    visible_after: datetime | None = None,
    limit: int = 200,
) -> list[dict]:
    cur.execute(
        """
        SELECT m.reseller_message_id, m.client_message_id, m.sender_account_id,
               m.content, m.created_at, m.read_at, m.return_id,
               a.name AS sender_name, a.account_type AS sender_account_type,
               COALESCE((SELECT jsonb_agg(jsonb_build_object('attachment_id',f.attachment_id,
                   'filename',f.filename,'content_type',f.content_type,'size_bytes',f.size_bytes,
                   'checksum_sha256',f.checksum_sha256) ORDER BY f.attachment_id)
                   FROM reseller_message_attachments f WHERE f.reseller_message_id=m.reseller_message_id),'[]'::jsonb) attachments
        FROM reseller_messages m
        JOIN accounts a ON a.account_id = m.sender_account_id
        WHERE m.conversation_id = %s
          AND m.reseller_message_id > %s
          AND (%s::timestamptz IS NULL OR m.created_at > %s::timestamptz)
        ORDER BY m.reseller_message_id
        LIMIT %s;
        """,
        (
            conversation_id,
            max(0, int(after_id)),
            visible_after,
            visible_after,
            min(max(1, int(limit)), 200),
        ),
    )
    return [_serialize(dict(row)) for row in cur.fetchall()]


def reseller_workspace(account_id: int, *, after_id: int = 0) -> dict:
    with get_transaction_cursor() as cur:
        participant = _reseller_participant(cur, account_id)
        leader_id = participant.get("team_leader_account_id")
        conversation = (
            _conversation_for_pair(cur, account_id, int(leader_id))
            if leader_id
            else None
        )
        messages = (
            _list_messages(
                cur,
                int(conversation["conversation_id"]),
                after_id,
                visible_after=conversation.get("reseller_deleted_at"),
            )
            if conversation
            else []
        )
    return {
        "participant": _serialize(participant),
        "conversation": _serialize(conversation),
        "messages": messages,
        "can_message": bool(leader_id),
    }


def mark_read_for_reseller(account_id: int, through_id: int = 0) -> int:
    with get_transaction_cursor() as cur:
        participant = _reseller_participant(cur, account_id)
        leader_id = participant.get("team_leader_account_id")
        if not leader_id:
            return 0
        conversation = _conversation_for_pair(cur, account_id, int(leader_id))
        if not conversation:
            return 0
        cur.execute(
            """
            UPDATE reseller_messages
            SET read_at = now()
            WHERE conversation_id = %s
              AND sender_account_id <> %s
              AND reseller_message_id <= %s
              AND (%s::timestamptz IS NULL OR created_at > %s::timestamptz)
              AND read_at IS NULL;
            """,
            (conversation["conversation_id"], account_id, max(0,through_id),conversation.get("reseller_deleted_at"),conversation.get("reseller_deleted_at")),
        )
        return cur.rowcount


def delete_for_reseller(account_id: int) -> bool:
    with get_transaction_cursor() as cur:
        participant = _reseller_participant(cur, account_id)
        leader_id = participant.get("team_leader_account_id")
        if not leader_id:
            return False
        cur.execute(
            """
            UPDATE reseller_conversations
            SET reseller_deleted_at = now(), updated_at = now()
            WHERE reseller_account_id = %s AND team_leader_account_id = %s;
            """,
            (account_id, leader_id),
        )
        return cur.rowcount == 1


def send_from_reseller(account_id: int, client_message_id: str, content: str, *, return_id: int | None = None,
                       report_issue: bool = False, attachments: list[dict] | None = None) -> dict:
    return _send_message(
        sender_account_id=account_id,
        reseller_account_id=account_id,
        client_message_id=client_message_id,
        content=content,
        leader_account_id=None,
        return_id=return_id, report_issue=report_issue, attachments=attachments,
    )


def send_from_leader(
    leader_account_id: int,
    reseller_account_id: int,
    client_message_id: str,
    content: str,
    *, return_id: int | None = None, attachments: list[dict] | None = None,
) -> dict:
    return _send_message(
        sender_account_id=leader_account_id,
        reseller_account_id=reseller_account_id,
        client_message_id=client_message_id,
        content=content,
        leader_account_id=leader_account_id,
        return_id=return_id, attachments=attachments,
    )


def _send_message(
    *,
    sender_account_id: int,
    reseller_account_id: int,
    client_message_id: str,
    content: str,
    leader_account_id: int | None,
    return_id: int | None = None,
    report_issue: bool = False,
    attachments: list[dict] | None = None,
) -> dict:
    attachments = attachments or []
    if len(attachments)>3 or sum(a["size_bytes"] for a in attachments)>30*1024*1024:
        raise ResellerMessagingError("Attach up to 3 files totaling no more than 30 MB.")
    cleaned = normalize_message(content) if str(content).strip() or not attachments else ""
    try:
        message_uuid = str(uuid.UUID(str(client_message_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ResellerMessagingError("A valid message id is required.") from exc

    if report_issue and (not return_id or leader_account_id is not None):
        raise ResellerMessagingError("Select your released replacement to report an issue.")
    with get_transaction_cursor() as cur:
        if return_id:
            from app.repositories.product_returns import _case
            case = _case(cur, return_id, sender_account_id, {"reseller"} if leader_account_id is None else {"sales", "sales-supervisor"}, read_only=leader_account_id is not None)
            if case["reseller_account_id"] != reseller_account_id:
                raise ResellerMessagingError("This return does not belong to this conversation.")
            cur.execute("""SELECT m.*,a.name sender_name,a.account_type sender_account_type,
                EXISTS(SELECT 1 FROM return_followup_messages f WHERE f.reseller_message_id=m.reseller_message_id) reported_issue
                FROM reseller_messages m JOIN accounts a ON a.account_id=m.sender_account_id
                WHERE m.sender_account_id=%s AND m.client_message_id=%s ORDER BY m.reseller_message_id LIMIT 1""",
                (sender_account_id, message_uuid))
            previous = cur.fetchone()
            if previous:
                if previous["return_id"] != return_id or previous["content"] != cleaned or previous["reported_issue"] != report_issue:
                    raise ResellerMessagingError("This message reference was used with different details.")
                previous = dict(previous)
                previous["attachments"] = _message_attachments(cur, previous["reseller_message_id"])
                _check_attachment_retry(previous["attachments"],attachments)
                return _serialize(previous) or {}
            if report_issue:
                if not cleaned:
                    raise ResellerMessagingError("Describe the replacement issue before attaching supporting media.")
                from app.repositories.return_customer import report_followup
                report_followup(cur, case, sender_account_id, cleaned, message_uuid)
        try:
            participant = _reseller_participant(cur, reseller_account_id, for_update=True)
            current_leader_id = int(participant.get("team_leader_account_id") or 0)
            if not current_leader_id:
                raise ResellerMessagingError("A sales leader must be assigned before you can send messages.")
            if return_id:
                cur.execute("SELECT 1 FROM accounts WHERE account_id=%s AND is_active AND account_type='team_leader' AND team_leader_role IN ('sales','sales-supervisor')", (current_leader_id,))
                if not cur.fetchone():
                    raise ResellerMessagingError("The assigned Sales contact is unavailable.")
            if leader_account_id is not None and current_leader_id != int(leader_account_id):
                raise ResellerMessagingError("This reseller is assigned to another sales leader.")
        except ResellerMessagingError:
            if report_issue:
                return {"followup_saved": True, "return_id": return_id,
                        "attachments_not_sent": bool(attachments),
                        "warning": ("Your issue text was saved for Supervisor/Owner follow-up. Attachments were not sent because chat is unavailable." if attachments else "Your issue was saved for Supervisor/Owner follow-up. Chat with Sales is currently unavailable.")}
            raise
        conversation = _ensure_conversation(cur, participant)
        cur.execute(
            """
            INSERT INTO reseller_messages (
                conversation_id, client_message_id, sender_account_id, content, return_id, attachment_count
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id, client_message_id) DO NOTHING
            RETURNING *;
            """,
            (conversation["conversation_id"], message_uuid, sender_account_id, cleaned, return_id,len(attachments)),
        )
        message = cur.fetchone()
        inserted = bool(message)
        if not message:
            cur.execute(
                """
                SELECT * FROM reseller_messages
                WHERE conversation_id = %s AND client_message_id = %s;
                """,
                (conversation["conversation_id"], message_uuid),
            )
            message = cur.fetchone()
            if message["content"] != cleaned or message["return_id"] != return_id or message["sender_account_id"] != sender_account_id:
                raise ResellerMessagingError("This message reference was used with different details.")
        if inserted:
            for attachment in attachments:
                cur.execute("""INSERT INTO reseller_message_attachments
                    (reseller_message_id,filename,content_type,content,size_bytes,checksum_sha256)
                    VALUES(%s,%s,%s,%s,%s,%s)""",(message["reseller_message_id"],attachment["filename"],
                    attachment["content_type"],attachment["content"],attachment["size_bytes"],attachment["checksum_sha256"]))
        else:
            _check_attachment_retry(_message_attachments(cur,message["reseller_message_id"]),attachments)
        if report_issue:
            cur.execute("UPDATE return_followup_messages SET reseller_message_id=%s WHERE client_message_id=%s", (message["reseller_message_id"], message_uuid))
        cur.execute(
            """
            UPDATE reseller_conversations
            SET last_message_at = %s, updated_at = now()
            WHERE conversation_id = %s;
            """,
            (message["created_at"], conversation["conversation_id"]),
        )
        serialized = _serialize(dict(message)) or {}
        serialized["attachments"] = _message_attachments(cur,message["reseller_message_id"])
        serialized["sender_name"] = (
            participant["team_leader_name"]
            if leader_account_id is not None
            else participant["reseller_name"]
        )
        serialized["sender_account_type"] = (
            "team_leader" if leader_account_id is not None else "reseller"
        )
    return serialized


def leader_conversations(account_id: int) -> list[dict]:
    with get_transaction_cursor() as cur:
        cur.execute(
            """
            SELECT a.account_id AS reseller_account_id,
                   a.name AS reseller_name,
               a.profile_image_path AS reseller_profile_image_path,
               a.profile_image_position_x AS reseller_profile_image_position_x,
               a.profile_image_position_y AS reseller_profile_image_position_y,
               a.profile_image_scale AS reseller_profile_image_scale,
                   r.business_name,
                   c.conversation_id,
                   c.last_message_at,
                   (r.team_leader_account_id = %s) AS is_current_assignment,
                   latest.content AS last_message,
                   latest.sender_account_id AS last_sender_account_id,
                   COALESCE(unread.unread_count, 0) AS unread_count
            FROM resellers r
            JOIN accounts a
              ON (a.reseller_id = r.reseller_id
                  OR (a.reseller_id IS NULL AND lower(a.email) = lower(r.email)))
             AND a.account_type = 'reseller'
             AND a.is_active = true
            LEFT JOIN reseller_conversations c
              ON c.reseller_account_id = a.account_id
             AND c.team_leader_account_id = %s
            LEFT JOIN LATERAL (
                SELECT content, sender_account_id
                FROM reseller_messages
                WHERE conversation_id = c.conversation_id
                ORDER BY reseller_message_id DESC
                LIMIT 1
            ) latest ON true
            LEFT JOIN LATERAL (
                SELECT count(*) AS unread_count
                FROM reseller_messages
                WHERE conversation_id = c.conversation_id
                  AND sender_account_id = a.account_id
                  AND read_at IS NULL
                  AND (c.team_leader_deleted_at IS NULL OR created_at > c.team_leader_deleted_at)
            ) unread ON true
            WHERE r.reseller_status = 'active'
              AND (
                  (c.conversation_id IS NULL AND r.team_leader_account_id = %s)
                  OR (
                      c.conversation_id IS NOT NULL
                      AND (c.team_leader_deleted_at IS NULL OR c.last_message_at > c.team_leader_deleted_at)
                  )
              )
            ORDER BY c.last_message_at DESC NULLS LAST, r.business_name, a.account_id;
            """,
            (account_id, account_id, account_id),
        )
        return [_serialize(dict(row)) for row in cur.fetchall()]


def leader_thread(account_id: int, reseller_account_id: int, *, after_id: int = 0) -> dict:
    with get_transaction_cursor() as cur:
        participant = _reseller_participant(cur, reseller_account_id)
        current_assignment = int(participant.get("team_leader_account_id") or 0) == int(account_id)
        conversation = _conversation_for_pair(cur, reseller_account_id, account_id)
        if not current_assignment and not conversation:
            raise ResellerMessagingError("This conversation is not available to your account.")
        messages = (
            _list_messages(
                cur,
                int(conversation["conversation_id"]),
                after_id,
                visible_after=conversation.get("team_leader_deleted_at"),
            )
            if conversation
            else []
        )
    return {
        "participant": _serialize(participant),
        "conversation": _serialize(conversation),
        "messages": messages,
        "can_message": current_assignment,
    }


def mark_read_for_leader(account_id: int, reseller_account_id: int, through_id: int = 0) -> int:
    with get_transaction_cursor() as cur:
        participant = _reseller_participant(cur, reseller_account_id)
        conversation = _conversation_for_pair(cur, reseller_account_id, account_id)
        current_assignment = int(participant.get("team_leader_account_id") or 0) == int(account_id)
        if not conversation:
            if current_assignment:
                return 0
            raise ResellerMessagingError("This conversation is not available to your account.")
        cur.execute(
            """
            UPDATE reseller_messages
            SET read_at = now()
            WHERE conversation_id = %s
              AND sender_account_id = %s
              AND reseller_message_id <= %s
              AND (%s::timestamptz IS NULL OR created_at > %s::timestamptz)
              AND read_at IS NULL;
            """,
            (conversation["conversation_id"], reseller_account_id,max(0,through_id),conversation.get("team_leader_deleted_at"),conversation.get("team_leader_deleted_at")),
        )
        return cur.rowcount


def delete_for_leader(account_id: int, reseller_account_id: int) -> bool:
    with get_transaction_cursor() as cur:
        conversation = _conversation_for_pair(cur, reseller_account_id, account_id)
        if not conversation:
            return False
        cur.execute(
            """
            UPDATE reseller_conversations
            SET team_leader_deleted_at = now(), updated_at = now()
            WHERE conversation_id = %s;
            """,
            (conversation["conversation_id"],),
        )
        return cur.rowcount == 1


def _message_attachments(cur, message_id: int) -> list[dict]:
    cur.execute("""SELECT attachment_id,filename,content_type,size_bytes,checksum_sha256
        FROM reseller_message_attachments WHERE reseller_message_id=%s ORDER BY attachment_id""",(message_id,))
    return [dict(row) for row in cur.fetchall()]


def _check_attachment_retry(previous: list[dict], current: list[dict]) -> None:
    fields = ("filename","content_type","size_bytes","checksum_sha256")
    if [tuple(a[f] for f in fields) for a in previous] != [tuple(a[f] for f in fields) for a in current]:
        raise ResellerMessagingError("This message reference was used with different attachments.")


def get_attachment(account_id: int, role_key: str, attachment_id: int) -> dict:
    with get_transaction_cursor() as cur:
        cur.execute("""SELECT a.*,m.created_at,m.conversation_id,c.reseller_account_id,c.team_leader_account_id,
            c.reseller_deleted_at,c.team_leader_deleted_at FROM reseller_message_attachments a
            JOIN reseller_messages m USING(reseller_message_id) JOIN reseller_conversations c USING(conversation_id)
            WHERE attachment_id=%s""", (attachment_id,))
        row = cur.fetchone()
        if not row:
            raise ResellerMessagingError("Attachment not found.")
        if role_key == "reseller":
            participant = _reseller_participant(cur,account_id)
            allowed = row["reseller_account_id"] == account_id and row["team_leader_account_id"] == participant["team_leader_account_id"]
            deleted = row["reseller_deleted_at"]
        else:
            allowed = role_key in {"team-leader","sales-supervisor"} and row["team_leader_account_id"] == account_id
            deleted = row["team_leader_deleted_at"]
        if not allowed or (deleted and row["created_at"] <= deleted):
            raise ResellerMessagingError("Attachment not found.")
        return dict(row)
