"""Replacement-first policy shared by transactional guards and read projections."""
from decimal import Decimal


def replacement_first(case: dict) -> bool:
    return case.get("return_policy_version", 1) == 2


def require_replacement_value(case: dict) -> None:
    if replacement_first(case) and case["resolution"] == "replace" and case["replacement_total"] != case["credit"]:
        raise ValueError("Replacement value must equal the approved return credit; replacements do not involve money.")


def refund_components(case: dict) -> tuple[Decimal, Decimal]:
    credit = Decimal(str(case["credit"]))
    if case["resolution"] != "refund":
        return max(Decimal(0), credit - Decimal(str(case["replacement_total"]))), Decimal(0)
    topup = Decimal(str(case.get("payment_amount") or 0)) if case.get("refund_converted_at") and case.get("payment_verified_at") else Decimal(0)
    return credit, topup


def refund_total(case: dict) -> Decimal:
    return sum(refund_components(case), Decimal(0))


def can_convert(case: dict) -> bool:
    return bool(replacement_first(case) and case["resolution"] == "replace"
                and case.get("approved_by_account_id") and not case.get("replacement_released_at")
                and case["status"] in {"awaiting_receipt", "settlement_pending"})
