"""Private, persistent conversations scoped to one version-two replacement case."""

from uuid import UUID

from app.database import clean_row, get_transaction_cursor
from .product_returns import _case, _actor
from .notifications import _create_notification_cursor


def participant(cur, return_id: int, actor_id: int) -> dict:
    case = _case(cur, return_id, actor_id, {"reseller", "sales-supervisor"}, read_only=True)
    role = _actor(cur, actor_id, {"reseller", "sales-supervisor"})
    if case.get("replacement_workflow_version") != 2:
        raise ValueError("This case uses its existing Sales conversation.")
    if role == "sales-supervisor" and case.get("processing_supervisor_account_id") != actor_id:
        raise ValueError("Only the assigned Supervisor can open this conversation.")
    return case


def _unread_count(cur, return_id: int, actor_id: int) -> int:
    cur.execute(
        """SELECT COUNT(*) unread_count FROM return_conversation_messages m
        LEFT JOIN return_conversation_reads rd ON rd.return_id=m.return_id AND rd.account_id=%s
        WHERE m.return_id=%s AND m.sender_account_id IS DISTINCT FROM %s
        AND m.message_id>COALESCE(rd.last_message_id,0)""",
        (actor_id, return_id, actor_id),
    )
    return cur.fetchone()["unread_count"]


def messages(return_id: int, actor_id: int, after_id: int = 0) -> dict:
    with get_transaction_cursor() as cur:
        case = participant(cur, return_id, actor_id)
        cur.execute(
            """SELECT m.message_id,m.sender_account_id,COALESCE(a.name,'MEATTRACK') sender_name,
            m.content,m.created_at FROM return_conversation_messages m LEFT JOIN accounts a ON a.account_id=m.sender_account_id
            WHERE m.return_id=%s AND m.message_id>%s ORDER BY m.message_id LIMIT 200""",
            (return_id, max(0, after_id)),
        )
        rows = clean_row(cur.fetchall())
        return dict(messages=rows, closed=case["status"] == "rejected", unread_count=_unread_count(cur, return_id, actor_id))


def send(return_id: int, actor_id: int, content: str, client_id: str) -> dict:
    content = str(content).strip()
    if not 1 <= len(content) <= 1000 or any(ord(c) < 32 and c not in "\r\n\t" for c in content):
        raise ValueError("Enter a message using 1 to 1000 characters.")
    client_id = str(UUID(client_id))
    with get_transaction_cursor() as cur:
        case = participant(cur, return_id, actor_id)
        cur.execute(
            "SELECT * FROM return_conversation_messages WHERE return_id=%s AND sender_account_id=%s AND client_message_id=%s",
            (return_id, actor_id, client_id),
        )
        previous = cur.fetchone()
        if previous:
            if previous["content"] != content:
                raise ValueError("This message reference already has different content.")
            return clean_row(previous)
        if case["status"] == "rejected":
            raise ValueError("This declined replacement conversation is read-only.")
        cur.execute(
            """INSERT INTO return_conversation_messages(return_id,sender_account_id,client_message_id,content)
            VALUES(%s,%s,%s,%s) RETURNING *""",
            (return_id, actor_id, client_id, content),
        )
        result = dict(cur.fetchone())
        reseller = case["reseller_account_id"]
        processor = case.get("processing_supervisor_account_id")
        if processor:
            cur.execute(
                "SELECT 1 FROM accounts WHERE account_id=%s AND is_active AND team_leader_role='sales-supervisor'",
                (processor,),
            )
            if not cur.fetchone():
                processor = None
        receiver = processor if actor_id == reseller else reseller
        receiver_role = (
            "sales-supervisor"
            if receiver == processor and processor
            else "reseller"
            if receiver == reseller
            else "owner"
        )
        path = "inbox" if receiver_role == "reseller" else "messages"
        _create_notification_cursor(
            cur,
            recipient_account_id=receiver,
            recipient_role="owner" if receiver is None else None,
            category="return",
            title=f"Replacement #{return_id}: new message",
            message="Open the case conversation to read the message.",
            target_url=f"/portal/{receiver_role}/{path}?replacement={return_id}",
            source_type="product_returns",
            source_id=return_id,
        )
        return clean_row(result)


def mark_read(return_id: int, actor_id: int, through_id: int) -> int:
    with get_transaction_cursor() as cur:
        participant(cur, return_id, actor_id)
        cur.execute(
            "SELECT message_id FROM return_conversation_messages WHERE return_id=%s AND message_id=%s",
            (return_id, through_id),
        )
        if not cur.fetchone():
            return _unread_count(cur, return_id, actor_id)
        cur.execute(
            """INSERT INTO return_conversation_reads(return_id,account_id,last_message_id) VALUES(%s,%s,%s)
            ON CONFLICT(return_id,account_id) DO UPDATE SET last_message_id=GREATEST(return_conversation_reads.last_message_id,EXCLUDED.last_message_id)""",
            (return_id, actor_id, through_id),
        )
        return _unread_count(cur, return_id, actor_id)


def threads(actor_id: int) -> list[dict]:
    with get_transaction_cursor() as cur:
        role = _actor(cur, actor_id, {"reseller", "sales-supervisor"})
        scope, params = (
            ("", [])
            if role == "owner"
            else (" AND r.requested_by_account_id=%s", [actor_id])
            if role == "reseller"
            else (" AND r.processing_supervisor_account_id=%s", [actor_id])
        )
        cur.execute(
            """SELECT r.return_id,r.order_id,r.status,a.name reseller_name,
            COALESCE(p.name,'Owner') processor_name,
            a.profile_image_path reseller_profile_image_path,
            a.profile_image_position_x reseller_profile_image_position_x,
            a.profile_image_position_y reseller_profile_image_position_y,
            a.profile_image_scale reseller_profile_image_scale,
            p.profile_image_path processor_profile_image_path,
            p.profile_image_position_x processor_profile_image_position_x,
            p.profile_image_position_y processor_profile_image_position_y,
            p.profile_image_scale processor_profile_image_scale,
            latest.content last_message,
            (SELECT COUNT(*) FROM return_conversation_messages m WHERE m.return_id=r.return_id
             AND m.sender_account_id IS DISTINCT FROM %s AND m.message_id>COALESCE(rd.last_message_id,0)) unread_count
            FROM product_returns r JOIN accounts a ON a.account_id=r.requested_by_account_id
            LEFT JOIN accounts p ON p.account_id=r.processing_supervisor_account_id
            LEFT JOIN LATERAL (
                SELECT content,created_at FROM return_conversation_messages
                WHERE return_id=r.return_id ORDER BY message_id DESC LIMIT 1
            ) latest ON TRUE
            LEFT JOIN return_conversation_reads rd ON rd.return_id=r.return_id AND rd.account_id=%s
            WHERE r.replacement_workflow_version=2"""
            + scope
            + " ORDER BY COALESCE(latest.created_at,r.updated_at) DESC,r.return_id DESC",
            [actor_id, actor_id, *params],
        )
        return clean_row(cur.fetchall())


def workspace(actor_id: int, return_id: int | None = None) -> dict:
    if not actor_id:
        return {"replacement_threads": [], "replacement_chat": None}
    rows = threads(actor_id)
    selected = next((r for r in rows if r["return_id"] == return_id), None)
    if return_id and not selected:
        raise ValueError("This replacement conversation is unavailable.")
    return {"replacement_threads": rows, "replacement_chat": selected, "replacement_chat_actor": actor_id}
