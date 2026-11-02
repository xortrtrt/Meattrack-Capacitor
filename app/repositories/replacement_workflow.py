"""Version-two replacement requests: comparison, direct handoff and delivery."""

from decimal import Decimal
import json
from uuid import uuid4

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import SESSION_SECRET_KEY
from app.database import get_transaction_cursor
from app.inventory_measurements import parse_whole_packs
from .processing_assignment import supervisor_for_case


def _signer():
    return URLSafeTimedSerializer(SESSION_SECRET_KEY, salt="replacement-comparison-v2")


def comparison(cur, order_id: int, actor_id: int, lines: list, replacements: list) -> dict:
    from .product_returns import _actor, return_request_window

    _actor(cur, actor_id, {"reseller"})
    cur.execute(
        "SELECT * FROM orders WHERE order_id=%s AND created_by_account_id=%s AND order_type='reseller' FOR UPDATE",
        (order_id, actor_id),
    )
    order = cur.fetchone()
    if not order or not return_request_window(order)["request_window_open"]:
        raise ValueError("Select your fulfilled order within its 120-hour return window.")
    if not lines or len({int(i) for i, _ in lines}) != len(lines):
        raise ValueError("Select each returned allocation once.")
    originals = []
    for allocation_id, value in sorted(lines, key=lambda row: int(row[0])):
        quantity = parse_whole_packs(value)
        cur.execute(
            """SELECT a.allocation_id,a.quantity,oi.product_id,oi.unit_price,i.name,
            a.quantity-COALESCE((SELECT SUM(l.quantity) FROM product_return_lines l JOIN product_returns r USING(return_id)
                WHERE l.allocation_id=a.allocation_id AND r.status<>'rejected'),0) available
            FROM fulfillment_allocations a JOIN order_items oi USING(order_item_id) JOIN inventory_items i ON i.item_id=oi.product_id
            WHERE a.allocation_id=%s AND oi.order_id=%s FOR UPDATE OF a""",
            (allocation_id, order_id),
        )
        row = cur.fetchone()
        if not row or quantity > row["available"]:
            raise ValueError("The selected returned quantity is no longer available to claim.")
        originals.append(
            dict(
                allocation_id=int(allocation_id),
                product_id=row["product_id"],
                name=row["name"],
                quantity=int(quantity),
                unit_price=str(row["unit_price"]),
                line_total=str(quantity * row["unit_price"]),
            )
        )
    return price_selection(cur, originals, replacements)


def price_selection(cur, originals: list[dict], replacements: list) -> dict:
    if not replacements or len({int(i) for i, _ in replacements}) != len(replacements):
        raise ValueError("Choose replacement products once each, with whole-pack quantities.")
    quoted = []
    for product_id, value in sorted(replacements, key=lambda row: int(row[0])):
        quantity = parse_whole_packs(value)
        cur.execute(
            "SELECT name,base_price FROM inventory_items WHERE item_id=%s AND item_type='finished_product' AND is_active",
            (product_id,),
        )
        product = cur.fetchone()
        if not product:
            raise ValueError("Choose an active finished product.")
        remaining, components = quantity, []
        for original in sorted(originals, key=lambda row: int(row["allocation_id"])):
            if int(original["product_id"]) != int(product_id) or not remaining:
                continue
            matched = min(remaining, Decimal(str(original["quantity"])))
            price = Decimal(str(original["unit_price"]))
            components.append(
                dict(
                    basis="original",
                    allocation_id=original["allocation_id"],
                    quantity=int(matched),
                    unit_price=str(price),
                    line_total=str(matched * price),
                )
            )
            remaining -= matched
        if remaining:
            price = product["base_price"]
            components.append(
                dict(basis="catalog", quantity=int(remaining), unit_price=str(price), line_total=str(remaining * price))
            )
        total = sum((Decimal(c["line_total"]) for c in components), Decimal(0))
        quoted.append(
            dict(
                product_id=int(product_id),
                name=product["name"],
                quantity=int(quantity),
                unit_price=str((total / quantity).quantize(Decimal(".01"))),
                line_total=str(total),
                pricing_components=components,
            )
        )
    credit = sum((Decimal(str(row["quantity"])) * Decimal(str(row["unit_price"])) for row in originals), Decimal(0))
    total = sum((Decimal(row["line_total"]) for row in quoted), Decimal(0))
    return dict(
        originals=originals,
        items=quoted,
        credit=str(credit),
        total=str(total),
        additional_due=str(max(total - credit, Decimal(0))),
        refund_due=str(max(credit - total, Decimal(0))),
    )


def preview(order_id: int, actor_id: int, lines: list, replacements: list) -> dict:
    with get_transaction_cursor() as cur:
        result = comparison(cur, order_id, actor_id, lines, replacements)
    return {**result, "token": _signer().dumps(dict(order_id=order_id, actor_id=actor_id, comparison=result))}


def verify_preview(token: str, order_id: int, actor_id: int, result: dict) -> None:
    try:
        signed = _signer().loads(token, max_age=3600)
    except (BadSignature, SignatureExpired) as exc:
        raise ValueError("Refresh the price comparison before submitting.") from exc
    if signed != dict(order_id=order_id, actor_id=actor_id, comparison=result):
        raise ValueError("Products, quantities, or prices changed. Review a refreshed comparison.")


def publish_quote(cur, case: dict, result: dict, *, accepted: bool) -> None:
    revision = case["quote_revision"] + 1
    for item in result["items"]:
        cur.execute(
            """INSERT INTO replacement_quote_lines(return_id,revision,product_id,quantity,unit_price,line_total,pricing_components)
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                case["return_id"],
                revision,
                item["product_id"],
                item["quantity"],
                item["unit_price"],
                item["line_total"],
                json.dumps(item["pricing_components"]),
            ),
        )
    cur.execute(
        """UPDATE product_returns SET quote_revision=%s,accepted_revision=%s,replacement_total=%s,
        replacement_prepared_at=NULL,replacement_prepared_by_account_id=NULL,replacement_payment_details=NULL,
        settlement_authorized_at=NULL,settlement_authorized_by_account_id=NULL,settlement_authorized_quote_revision=NULL
        WHERE return_id=%s RETURNING *""",
        (revision, revision if accepted else None, result["total"], case["return_id"]),
    )
    case.update(dict(cur.fetchone()))
    from .return_customer import snapshot_offer

    snapshot_offer(cur, case["return_id"])


def initialize(cur, case: dict, order: dict, actor_id: int, result: dict) -> None:
    processor = supervisor_for_case(cur, order.get("team_leader_account_id"))
    cur.execute(
        """UPDATE product_returns SET replacement_workflow_version=2,
        processing_supervisor_account_id=%s,handed_off_at=now() WHERE return_id=%s RETURNING *""",
        (processor, case["return_id"]),
    )
    case.update(dict(cur.fetchone()))
    publish_quote(cur, case, result, accepted=True)
    cur.execute(
        """INSERT INTO return_conversation_messages(return_id,client_message_id,content)
        VALUES(%s,%s,'Replacement request opened. Use this conversation to coordinate review and delivery.')""",
        (case["return_id"], str(uuid4())),
    )


def review(
    cur,
    case: dict,
    actor_id: int,
    approve: bool,
    remarks: str,
    replacements,
    revised_quantities,
    operation_key,
    customer_message,
    request_hash,
) -> None:
    from .product_returns import _actor, _text, _event

    role = _actor(cur, actor_id, {"sales-supervisor"})
    if role != "owner" and case.get("processing_supervisor_account_id") != actor_id:
        raise ValueError("Only the assigned Supervisor or Owner can review this replacement.")
    if (
        case["payment_verified_at"]
        or case["replacement_released_at"]
        or case["status"] not in {"submitted", "discrepancy", "awaiting_receipt", "settlement_pending"}
    ):
        raise ValueError("This paid or released replacement cannot be revised.")
    if not approve:
        if case["status"] != "submitted":
            raise ValueError("Only a new request can be rejected.")
        explanation = _text(customer_message, "Explanation for the reseller")
        cur.execute("UPDATE product_returns SET status='rejected' WHERE return_id=%s", (case["return_id"],))
        _event(
            cur,
            case,
            actor_id,
            "rejected",
            remarks,
            operation_key,
            customer_message=explanation,
            request_hash=request_hash,
        )
        return
    if revised_quantities:
        if case["status"] != "discrepancy" or case["verified_at"]:
            raise ValueError("Return quantities can be revised only during an unresolved receipt mismatch.")
        if len({int(i) for i, _ in revised_quantities}) != len(revised_quantities):
            raise ValueError("Revise each original return line once.")
        for line_id, value in revised_quantities:
            quantity = parse_whole_packs(value)
            cur.execute(
                """SELECT l.*,a.quantity fulfilled FROM product_return_lines l
                JOIN fulfillment_allocations a USING(allocation_id)
                WHERE l.return_id=%s AND l.return_line_id=%s FOR UPDATE OF l,a""",
                (case["return_id"], line_id),
            )
            line = cur.fetchone()
            if not line or quantity < line["received_quantity"]:
                raise ValueError("Revised quantities cannot be below recorded custody.")
            cur.execute(
                """SELECT COALESCE(SUM(l.quantity),0) claimed FROM product_return_lines l
                JOIN product_returns r USING(return_id) WHERE l.allocation_id=%s
                AND l.return_id<>%s AND r.status<>'rejected'""",
                (line["allocation_id"], case["return_id"]),
            )
            if quantity + cur.fetchone()["claimed"] > line["fulfilled"]:
                raise ValueError("Revised quantities exceed the original delivery's unclaimed packs.")
            cur.execute("UPDATE product_return_lines SET quantity=%s WHERE return_line_id=%s", (quantity, line_id))
        cur.execute(
            "SELECT SUM(quantity*unit_price) credit FROM product_return_lines WHERE return_id=%s", (case["return_id"],)
        )
        case["credit"] = cur.fetchone()["credit"]
        cur.execute("UPDATE product_returns SET credit=%s WHERE return_id=%s", (case["credit"], case["return_id"]))
    if case["status"] == "discrepancy":
        customer_message = _text(customer_message, "Explanation for the reseller")
    if replacements or revised_quantities:
        cur.execute(
            "SELECT product_id,quantity FROM replacement_quote_lines WHERE return_id=%s AND revision=%s ORDER BY product_id",
            (case["return_id"], case["quote_revision"]),
        )
        current = [(r["product_id"], int(r["quantity"])) for r in cur.fetchall()]
        chosen = sorted((int(i), int(parse_whole_packs(q))) for i, q in replacements) if replacements else current
        if chosen != current or revised_quantities:
            cur.execute(
                """SELECT l.allocation_id,oi.product_id,l.quantity,l.unit_price FROM product_return_lines l
                JOIN fulfillment_allocations a USING(allocation_id) JOIN order_items oi USING(order_item_id)
                WHERE l.return_id=%s ORDER BY l.allocation_id""",
                (case["return_id"],),
            )
            publish_quote(cur, case, price_selection(cur, [dict(r) for r in cur.fetchall()], chosen), accepted=False)
    cur.execute("DELETE FROM replacement_stock_reservations WHERE return_id=%s", (case["return_id"],))
    cur.execute(
        """UPDATE product_returns SET approved_by_account_id=%s,
        status=CASE WHEN verified_at IS NULL THEN 'awaiting_receipt' ELSE 'settlement_pending' END WHERE return_id=%s""",
        (actor_id, case["return_id"]),
    )
    _event(
        cur,
        case,
        actor_id,
        "sales_approved",
        remarks,
        operation_key,
        customer_message=customer_message
        or "Supervisor approved the requested replacement. Return the approved goods to Inventory for complete receipt.",
        request_hash=request_hash,
    )


def deliver(return_id: int, actor_id: int, remarks: str) -> None:
    from .product_returns import _case, _text, _event, _complete_if_ready

    with get_transaction_cursor() as cur:
        case = _case(cur, return_id, actor_id, {"sales-supervisor"})
        if case.get("replacement_workflow_version") != 2 or not case["replacement_released_at"]:
            raise ValueError("Inventory must release this replacement before delivery can be recorded.")
        if case["replacement_delivered_at"]:
            return
        cur.execute(
            "UPDATE product_returns SET replacement_delivered_at=now(),replacement_delivered_by_account_id=%s WHERE return_id=%s",
            (actor_id, return_id),
        )
        _event(
            cur,
            case,
            actor_id,
            "replacement_delivered",
            _text(remarks),
            customer_message="Your Supervisor recorded the replacement delivery.",
        )
        _complete_if_ready(cur, return_id)


def progress(case: dict) -> str:
    if case["status"] == "rejected":
        return "Request declined"
    if not case["approved_by_account_id"]:
        return "Supervisor review"
    if case["status"] == "discrepancy":
        return "Receipt mismatch — Supervisor review"
    if not case["verified_at"]:
        return "Awaiting complete return receipt"
    if case["accepted_revision"] != case["quote_revision"]:
        return "Accept the updated comparison"
    if case.get("replacement_delivered_at"):
        return "Delivered" if case["status"] == "completed" else "Delivered — refund settlement pending"
    if case["replacement_released_at"]:
        return "In transit — Supervisor records delivery"
    if case["replacement_total"] > case["credit"] and not case["payment_verified_at"]:
        from app.return_experience import payment_submission_ready
        return "Supervisor payment review" if payment_submission_ready(case) else "Additional payment required"
    return (
        "Ready for warehouse release"
        if case.get("replacement_prepared_at") and case.get("has_reservations")
        else "Awaiting valid stock and preparation"
    )


def enrich_progress(case: dict) -> None:
    if case.get("replacement_workflow_version") != 2:
        return
    from app.return_experience import payment_submission_ready

    title = progress(case)
    case["replacement_progress"] = title
    if not case.get("experience"):
        return
    payment = bool(
        case.get("verified_at")
        and case.get("sales_authorized")
        and case["accepted_revision"] == case["quote_revision"]
        and case["replacement_total"] > case["credit"]
        and not case.get("payment_verified_at")
    )
    receipt_ready = payment_submission_ready(case)
    if case["experience"]["sales"]["action"] in {"resolve_issue", "proceed", "reconcile"}:
        return
    case["experience"]["reseller"] = dict(
        title=title,
        action="pay" if payment and not receipt_ready else "",
        description="Complete payment in this return's payment form; Supervisor verifies receipt of funds before Inventory prepares stock."
        if payment
        else "Follow the recorded comparison, Supervisor conversation, and delivery updates for this replacement.",
    )
    case["experience"]["sales"] = dict(
        title=title,
        action="review" if case["status"] in {"submitted", "discrepancy"} else "verify_payment" if payment and receipt_ready else "",
        description="Review the request and evidence, verify any payment in the provider, and record delivery after Inventory release.",
    )
