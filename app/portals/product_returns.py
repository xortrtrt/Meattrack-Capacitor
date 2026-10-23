"""Role-scoped return cases and expired-stock custody screens."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from psycopg2.errors import UniqueViolation

from app.capabilities import capability_role_key, require_capability
from app.database import fetch_all, fetch_one
from app.repositories import product_returns as returns
from app.repositories.inventory import list_products
from app.portals import shared

router = APIRouter()


def _guard(request: Request, role_key: str, capability: str):
    if role_key not in {"owner", "team-leader", "sales-supervisor", "reseller"}:
        raise HTTPException(404)
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    require_capability(request, capability, role_key)
    return None


def returns_context(request: Request) -> dict:
    actor_id = shared.session_account_id(request)
    role = capability_role_key(request)
    cases = returns.list_product_returns(actor_id, request.query_params.get("status", ""))
    case_id = shared.optional_positive_id(request.query_params.get("case"))
    selected = None
    if case_id:
        try:
            selected = returns.get_product_return(case_id, actor_id)
            selected["additional_due"] = max(0, selected["replacement_total"] - selected["credit"])
            selected["unused_credit"] = max(0, selected["credit"] - selected["replacement_total"])
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
    order_id = shared.optional_positive_id(request.query_params.get("order"))
    legacy = []
    if role in {"owner", "team-leader:inventory"}:
        legacy = fetch_all("""SELECT oi.order_id,oi.order_item_id,oi.product_id,oi.quantity,i.name,
            oi.quantity-COALESCE((SELECT SUM(quantity) FROM fulfillment_allocations a WHERE a.order_item_id=oi.order_item_id),0) unresolved_quantity
            FROM order_items oi JOIN orders o USING(order_id) JOIN inventory_items i ON i.item_id=oi.product_id
            WHERE o.status='fulfilled' AND o.order_type='reseller' AND oi.quantity>COALESCE((SELECT SUM(quantity) FROM fulfillment_allocations a WHERE a.order_item_id=oi.order_item_id),0)
            ORDER BY oi.order_id DESC LIMIT 50;""")
    legacy_batches: dict[int, list[dict]] = {}
    if legacy:
        for batch in fetch_all(
            "SELECT batch_id,item_id,batch_code,expiry_date FROM inventory_batches WHERE item_id=ANY(%s) ORDER BY item_id,expiry_date,batch_id;",
            (list({line["product_id"] for line in legacy}),),
        ):
            legacy_batches.setdefault(batch["item_id"], []).append(batch)
    can_review = role in {"owner", "team-leader:sales", "sales-supervisor"}
    if selected and role != "owner" and selected["team_leader_account_id"] != actor_id:
        can_review = False
    return {
        "return_cases": cases,
        "return_case": selected,
        "return_order_id": order_id,
        "returnable_batches": returns.returnable_allocations(order_id, actor_id)
        if order_id and role == "reseller"
        else [],
        "replacement_products": list_products(active_only=True) if role != "reseller" else [],
        "return_summary": returns.return_settlement_totals(actor_id),
        "operation_key": lambda: str(uuid4()),
        "return_is_reseller": role == "reseller",
        "return_can_review": can_review,
        "return_can_receive": role in {"owner", "team-leader:inventory"},
        "legacy_lines": legacy,
        "legacy_batches": legacy_batches,
    }


def expired_context(request: Request) -> dict:
    return {
        **returns.expired_stock_workspace(),
        "replacement_products": list_products(),
        "operation_key": lambda: str(uuid4()),
    }


def product_details_context(request: Request) -> dict:
    product_id = shared.optional_positive_id(request.query_params.get("product_id"))
    product = (
        returns.product_lifecycle_details(
            product_id,
            batch_page=shared.requested_page(request.query_params.get("batch_page")),
            movement_page=shared.requested_page(request.query_params.get("movement_page")),
        )
        if product_id
        else None
    )
    if not product:
        raise HTTPException(404)
    base_url = request.url.include_query_params(
        batch_page=product["batch_pagination"]["page"],
        movement_page=product["movement_pagination"]["page"],
    )
    for key, parameter, anchor in (
        ("batch_pagination", "batch_page", "product-batches"),
        ("movement_pagination", "movement_page", "product-movements"),
    ):
        pagination = product[key]
        page, page_size, total = pagination["page"], pagination["page_size"], pagination["total"]
        first_page = max(1, min(page - 2, pagination["total_pages"] - 4))
        pagination.update(
            start=(page - 1) * page_size + 1 if total else 0,
            end=min(page * page_size, total),
            previous_url=str(base_url.include_query_params(**{parameter: page - 1}).replace(fragment=anchor)),
            next_url=str(base_url.include_query_params(**{parameter: page + 1}).replace(fragment=anchor)),
            links=[
                {
                    "page": number,
                    "url": str(base_url.include_query_params(**{parameter: number}).replace(fragment=anchor)),
                }
                for number in range(first_page, min(pagination["total_pages"], first_page + 4) + 1)
            ],
        )
    return {"lifecycle_product": product}


def _pairs(form, id_name: str, quantity_name: str) -> list[tuple[int, str]]:
    ids, quantities = form.getlist(id_name), form.getlist(quantity_name)
    if len(ids) != len(quantities):
        raise ValueError("The item quantities are incomplete.")
    result = []
    for item, quantity in zip(ids, quantities):
        if not str(item).strip() or not str(quantity).strip() or str(quantity).strip() == "0":
            continue
        result.append((int(str(item)), str(quantity)))
    return result


@router.post("/portal/{role_key}/returns/actions/{action}")
async def return_action(request: Request, role_key: str, action: str):
    capabilities = {
        "submit": "returns.create",
        "approve": "returns.review",
        "reject": "returns.review",
        "receive": "returns.receive",
        "discrepancy": "returns.receive",
        "accept": "returns.create",
        "reserve": "returns.receive",
        "release": "returns.receive",
        "payment": "returns.create",
        "verify-payment": "returns.review",
        "legacy": "returns.receive",
    }
    if action not in capabilities:
        raise HTTPException(404)
    guard = _guard(request, role_key, capabilities[action])
    if guard:
        return guard
    form = await request.form()
    actor_id = shared.session_account_id(request)
    return_id = shared.optional_positive_id(str(form.get("return_id", "")))
    params = {"case": str(return_id)} if return_id else {}
    try:
        if action == "submit":
            uploads = [u for u in form.getlist("evidence") if getattr(u, "filename", "")]
            attachments = await shared.parse_payment_proof_attachments(uploads)
            return_id = returns.create_product_return(
                int(str(form.get("order_id", "0"))),
                actor_id,
                str(form.get("resolution", "")),
                _pairs(form, "allocation_id", "quantity"),
                str(form.get("remarks", "")),
                attachments,
                str(form.get("operation_key", "")) or None,
            )
            params = {"case": str(return_id)}
        elif action in {"approve", "reject"}:
            returns.review_product_return(
                return_id,
                actor_id,
                action == "approve",
                str(form.get("remarks", "")),
                _pairs(form, "product_id", "replacement_quantity"),
                _pairs(form, "revised_line_id", "approved_quantity"),
                str(form.get("operation_key", "")) or None,
            )
        elif action == "receive":
            returns.receive_product_return(
                return_id,
                actor_id,
                _pairs(form, "line_id", "quantity"),
                str(form.get("location", "")),
                str(form.get("remarks", "")),
                str(form.get("operation_key", "")),
            )
        elif action == "discrepancy":
            returns.flag_return_discrepancy(return_id, actor_id, str(form.get("remarks", "")))
        elif action == "accept":
            returns.accept_replacement_quote(return_id, actor_id, int(str(form.get("revision", "0"))))
        elif action == "reserve":
            returns.reserve_replacement(return_id, actor_id, str(form.get("remarks", "")))
        elif action == "release":
            returns.release_replacement(return_id, actor_id, str(form.get("remarks", "")))
        elif action == "payment":
            attachments = await shared.parse_payment_proof_attachments(
                [u for u in form.getlist("payment_proofs") if getattr(u, "filename", "")]
            )
            returns.record_return_payment(return_id, actor_id, attachments)
        elif action == "verify-payment":
            returns.verify_return_payment(
                return_id,
                actor_id,
                str(form.get("reference", "")),
                str(form.get("amount", "")),
                str(form.get("remarks", "")),
            )
        elif action == "legacy":
            returns.verify_legacy_allocation(
                int(str(form.get("order_id", "0"))),
                int(str(form.get("order_item_id", "0"))),
                int(str(form.get("batch_id", "0"))),
                str(form.get("quantity", "")),
                actor_id,
                str(form.get("remarks", "")),
            )
    except (ValueError, UniqueViolation) as exc:
        if action == "submit":
            params = {"order": str(form.get("order_id", ""))}
        error = (
            str(exc)
            if isinstance(exc, ValueError)
            else "This operation or reference has already been recorded. Refresh the case."
        )
        return shared.redirect_to(shared.safe_portal_path(role_key, "returns", error=error, **params))
    return shared.redirect_to(shared.safe_portal_path(role_key, "returns", message="Return action recorded.", **params))


@router.post("/portal/{role_key}/expired-stock/actions/{action}")
async def expired_action(request: Request, role_key: str, action: str):
    guard = _guard(request, role_key, "inventory.manage")
    if guard:
        return guard
    form = await request.form()
    actor_id = shared.session_account_id(request)
    try:
        common = {
            "actor_id": actor_id,
            "quantity": str(form.get("quantity", "")),
            "remarks": str(form.get("remarks", "")),
            "operation_key": str(form.get("operation_key", "")),
        }
        if action == "pullout":
            returns.confirm_expired_pullout(
                int(str(form.get("batch_id", "0"))), location=str(form.get("location", "")), **common
            )
        elif action == "dispose":
            returns.dispose_expired_stock(
                int(str(form.get("holding_id", "0"))), method=str(form.get("method", "")), **common
            )
        elif action == "arrival":
            returns.record_expired_arrival(
                int(str(form.get("product_id", "0"))),
                batch_code=str(form.get("batch_code", "")),
                expiry_date=date.fromisoformat(str(form.get("expiry_date", ""))),
                location=str(form.get("location", "")),
                **common,
            )
        else:
            raise HTTPException(404)
    except (ValueError, UniqueViolation) as exc:
        return shared.redirect_to(
            shared.safe_portal_path(
                role_key,
                "expired-stock",
                error=str(exc) if isinstance(exc, ValueError) else "This operation has already been recorded.",
            )
        )
    return shared.redirect_to(shared.safe_portal_path(role_key, "expired-stock", message="Stock custody updated."))


@router.get("/portal/return-attachments/{attachment_id}")
def return_attachment(request: Request, attachment_id: int):
    actor_id = shared.session_account_id(request)
    if not actor_id:
        raise HTTPException(404)
    attachment = fetch_one("SELECT * FROM return_attachments WHERE attachment_id=%s;", (attachment_id,))
    if not attachment:
        raise HTTPException(404)
    try:
        returns.get_product_return(attachment["return_id"], actor_id)
    except ValueError as exc:
        raise HTTPException(404) from exc
    return Response(
        content=bytes(attachment["content"]),
        media_type=attachment["content_type"],
        headers={"Cache-Control": "private, no-store"},
    )


SECTION_LOADERS = {
    (role, "returns"): returns_context for role in ("owner", "team-leader", "sales-supervisor", "reseller")
}
SECTION_LOADERS.update({(role, "expired-stock"): expired_context for role in ("owner", "team-leader")})
SECTION_LOADERS.update({(role, "product-details"): product_details_context for role in ("owner", "team-leader")})
