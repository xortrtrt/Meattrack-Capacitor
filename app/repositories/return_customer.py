"""Customer communication and follow-ups, independent of inventory custody."""
from uuid import UUID

from app.database import clean_row, get_transaction_cursor
from app.return_experience import experience


def snapshot_offer(cur, return_id: int) -> None:
    cur.execute("""INSERT INTO return_offer_snapshots
        (return_id,revision,credit,replacement_total,returned_items,replacement_items)
        SELECT r.return_id,r.quote_revision,r.credit,r.replacement_total,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('product_id',oi.product_id,'name',i.name,
          'quantity',l.quantity,'batch_code',b.batch_code,'unit_price',l.unit_price) ORDER BY l.return_line_id)
          FROM product_return_lines l JOIN fulfillment_allocations a USING(allocation_id)
          JOIN order_items oi USING(order_item_id) JOIN inventory_items i ON i.item_id=oi.product_id
          JOIN inventory_batches b USING(batch_id) WHERE l.return_id=r.return_id),'[]'::jsonb),
        COALESCE((SELECT jsonb_agg(jsonb_build_object('product_id',q.product_id,'name',i.name,
          'quantity',q.quantity,'unit_price',q.unit_price,'line_total',q.line_total) ORDER BY q.product_id)
          FROM replacement_quote_lines q JOIN inventory_items i ON i.item_id=q.product_id
          WHERE q.return_id=r.return_id AND q.revision=r.quote_revision),'[]'::jsonb)
        FROM product_returns r WHERE r.return_id=%s AND r.resolution='replace' AND r.quote_revision>0
        ON CONFLICT DO NOTHING""", (return_id,))


def enrich_case(cur, case: dict) -> None:
    cur.execute("SELECT * FROM return_customer_followups WHERE return_id=%s ORDER BY followup_id DESC", (case["return_id"],))
    case["followups"] = clean_row(cur.fetchall())
    for followup in case["followups"]:
        cur.execute("SELECT content,created_at FROM return_followup_messages WHERE followup_id=%s ORDER BY created_at,client_message_id", (followup["followup_id"],))
        followup["messages"] = clean_row(cur.fetchall())
    case["has_open_followup"] = any(row["status"] == "open" for row in case["followups"])
    cur.execute("SELECT created_at FROM product_return_events WHERE return_id=%s AND action='replacement_received'", (case["return_id"],))
    ack = cur.fetchone()
    case["replacement_received_at"] = ack["created_at"] if ack else None
    case["has_payment_proof"] = any(a["purpose"] == "payment" and a["revision"] == case["quote_revision"] for a in case.get("attachments", []))
    cur.execute("SELECT * FROM return_offer_snapshots WHERE return_id=%s ORDER BY revision DESC LIMIT 2", (case["return_id"],))
    snapshots = clean_row(cur.fetchall())
    case["offer_snapshot"] = snapshots[0] if snapshots else None
    case["offer_changes"] = []
    if len(snapshots) == 2 and snapshots[0]["revision"] == case["quote_revision"]:
        current, previous = snapshots
        for field, label in (("returned_items", "Approved return products or quantities"),
                             ("replacement_items", "Replacement products, quantities, or prices"),
                             ("credit", "Return credit"), ("replacement_total", "Replacement value")):
            if current[field] != previous[field]:
                case["offer_changes"].append({"label": label, "before": previous[field], "after": current[field], "field": field})
        case["previous_offer"] = previous
    case["experience"] = experience(case)


def acknowledge_replacement(return_id: int, actor_id: int) -> None:
    from .product_returns import _case, _event
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"reseller"})
        if case["reseller_account_id"] != actor_id:
            raise ValueError("Only the reseller can acknowledge receiving this replacement.")
        if not case["replacement_released_at"] or case["resolution"] != "replace":
            raise ValueError("A replacement must be released before you can acknowledge receipt.")
        cur.execute("SELECT 1 FROM product_return_events WHERE return_id=%s AND action='replacement_received'", (return_id,))
        if cur.fetchone():
            return
        _event(cur, case, actor_id, "replacement_received", "Reseller acknowledged receiving the replacement.",
               customer_message="You acknowledged receiving the replacement. Settlement and any open issue are unchanged.")


def report_followup(cur, case: dict, actor_id: int, content: str, key: str) -> tuple[int, bool]:
    from .product_returns import _event
    if not case["replacement_released_at"] or case["resolution"] != "replace":
        raise ValueError("Report replacement problems after the replacement has been released.")
    cur.execute("""SELECT m.*,f.return_id FROM return_followup_messages m
        JOIN return_customer_followups f USING(followup_id) WHERE m.client_message_id=%s""", (key,))
    previous = cur.fetchone()
    if previous:
        if previous["return_id"] != case["return_id"] or previous["content"] != content:
            raise ValueError("This message reference was used with different details.")
        return previous["followup_id"], False
    cur.execute("SELECT followup_id FROM return_customer_followups WHERE return_id=%s AND status='open'", (case["return_id"],))
    row = cur.fetchone()
    if not row:
        cur.execute("""INSERT INTO return_customer_followups(return_id,reported_by_account_id,report_key,message)
            VALUES(%s,%s,%s,%s) RETURNING followup_id""", (case["return_id"], actor_id, str(UUID(key)), content))
        row = cur.fetchone()
    followup_id = row["followup_id"]
    cur.execute("INSERT INTO return_followup_messages(followup_id,client_message_id,content) VALUES(%s,%s,%s)", (followup_id, key, content))
    _event(cur, case, actor_id, "customer_issue_reported", "Reseller reported a replacement issue: " + content,
           customer_message="Your replacement issue was sent to Sales for follow-up.")
    return followup_id, True


def resolve_followup(return_id: int, actor_id: int, followup_id: int, customer_message: str) -> None:
    from .product_returns import _case, _event, _text
    customer_message = _text(customer_message, "Explanation for the reseller")
    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"sales", "sales-supervisor"})
        cur.execute("SELECT * FROM return_customer_followups WHERE followup_id=%s AND return_id=%s FOR UPDATE", (followup_id, return_id))
        followup = cur.fetchone()
        if not followup:
            raise ValueError("This follow-up was not found.")
        if followup["status"] == "resolved":
            if followup["resolution_message"] != customer_message:
                raise ValueError("This follow-up has already been resolved. Reload the case.")
            return
        cur.execute("""UPDATE return_customer_followups SET status='resolved',resolved_at=now(),
            resolved_by_account_id=%s,resolution_message=%s WHERE followup_id=%s""", (actor_id, customer_message, followup_id))
        _event(cur, case, actor_id, "customer_issue_resolved", "Sales resolved the reseller follow-up.", customer_message=customer_message)
