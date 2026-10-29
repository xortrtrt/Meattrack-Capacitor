"""Completed refunds must never be represented as retained report sales."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app import repositories
from app.business_time import business_today, business_now
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, create_case, receive, complete_refund, refund_proof, substitute  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("price,retained,refunded", [(75, 950, 50), (125, 1000, 0)])
def test_replacement_difference_separated_and_topup_not_ordinary_sales(lifecycle, price, retained, refunded):  # noqa: F811
    d = lifecycle
    case = create_case(d, "replace")
    product = substitute(price)
    returns.review_product_return(case, d["sales"], True, "Approved substitute for reporting test", [(product, 2)])
    receive(d, case)
    returns.accept_replacement_quote(case, d["reseller"], 1)
    returns.reserve_replacement(case, d["inventory"], "Reserved eligible replacement stock")
    if price > 100:
        returns.record_return_payment(case, d["reseller"], [refund_proof()])
        returns.verify_return_payment(
            case, d["sales"], "SIM" + uuid4().hex, "50", "SIMULATED additional payment confirmed"
        )
    returns.release_replacement(case, d["inventory"], "Released the verified replacement once")
    if refunded:
        complete_refund(d, case)
    assert submit(d)["total_sales"] == retained
    result = report(d)
    assert result["gross_sales"] == 1000 and result["refunded_sales"] == refunded
    from app.repositories.activity_reports import build_report
    activity = build_report(d["sales"], business_today(), business_today())
    assert activity["summary"]["retained_sales"] == retained
    assert activity["summary"]["sales_refunds"] == refunded
    assert activity["summary"]["gross_sales"] == 1000


def submit(d):
    return repositories.add_sales_report(
        "team_leader",
        "Sales",
        business_today(),
        business_today(),
        99999,
        999,
        "DEMO refund-separated report",
        account_id=d["sales"],
    )


def verified(d, quantity=2):
    case = create_case(d, quantity=quantity)
    returns.review_product_return(case, d["sales"], True, "Approved expired goods for report test")
    receive(d, case, quantity)
    return case


def report(d):
    return repositories.list_sales_reports(report_source="team_leader", team_leader_account_id=d["sales"])[0]


def test_partial_refund_is_separate_from_report_and_late_adjustment_preserves_snapshot(lifecycle):  # noqa: F811
    d = lifecycle
    first = verified(d)
    complete_refund(d, first)
    submitted = submit(d)
    assert submitted["total_sales"] == 800 and submitted["total_orders"] == 1
    result = report(d)
    assert result["gross_sales"] == 1000 and result["refunded_sales"] == 200 and result["total_sales"] == 800
    assert result["submitted_total_sales"] == 800 and result["late_refund_adjustment"] == 0
    assert len(result["refund_rows"]) == 1 and not result["refund_rows"][0]["late_adjustment"]
    second = verified(d)
    complete_refund(d, second)
    result = report(d)
    assert result["total_sales"] == 600 and result["submitted_total_sales"] == 800
    assert result["late_refund_adjustment"] == 200 and result["refunded_sales"] == 400
    assert sum(r["late_adjustment"] for r in result["refund_rows"]) == 1
    assert fetch_one("SELECT total_sales FROM sales_reports")["total_sales"] == 800
    assert fetch_one("SELECT total_amount,status FROM orders")["total_amount"] == 1000
    exported = repositories.owner_export_rows("reports")[0]
    assert exported["submitted_sales"] == 800 and exported["retained_sales"] == 600


def test_full_refund_excludes_order_from_retained_sales_count(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d, 10)
    complete_refund(d, case)
    result = submit(d)
    assert result["total_sales"] == result["total_orders"] == 0
    displayed = report(d)
    assert displayed["gross_sales"] == displayed["refunded_sales"] == 1000
    assert displayed["total_sales"] == displayed["total_orders"] == 0
    assert not displayed["reconciliation_required"]


def test_pending_failed_refunds_not_deducted_and_completion_is_late_adjustment(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d)
    submit(d)
    assert report(d)["total_sales"] == 1000 and report(d)["refund_rows"] == []
    refund = returns.get_product_return(case, d["supervisor"])["refund"]["order_refund_id"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    repositories.fail_order_refund(refund, d["supervisor"], "sales-supervisor", "SIMULATED failed transfer")
    assert report(d)["total_sales"] == 1000
    complete_refund(d, case)
    result = report(d)
    assert (
        result["submitted_total_sales"] == 1000
        and result["total_sales"] == 800
        and result["late_refund_adjustment"] == 200
    )
    assert result["refund_rows"][0]["late_adjustment"]


def test_report_ownership_and_preview_match_server(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d)
    complete_refund(d, case)
    totals = repositories.team_sales_report_totals(business_today(), business_today(), d["sales"])
    assert totals["total_sales"] == 800 and totals["gross_sales"] == 1000 and totals["refunded_sales"] == 200
    rows = repositories.team_sales_report_entries(d["sales"])
    assert len(rows) == 1 and rows[0]["order_retained_sales"] == 800 and rows[0]["total_sales"] == 1000
    refunds = repositories.team_sales_refund_entries(d["sales"])
    assert len(refunds) == 1 and refunds[0]["amount"] == 200
    assert repositories.team_sales_refund_entries(d["supervisor"]) == []
    submit(d)
    assert repositories.list_sales_reports(report_source="team_leader", team_leader_account_id=d["supervisor"]) == []


def test_snapshot_and_concurrent_refund_are_consistent(lifecycle):  # noqa: F811
    d = lifecycle
    case = verified(d)
    refund = returns.get_product_return(case, d["supervisor"])["refund"]["order_refund_id"]
    repositories.start_order_refund(refund, d["supervisor"], "sales-supervisor")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(submit, d)
        b = pool.submit(
            repositories.complete_order_refund,
            refund,
            d["supervisor"],
            "sales-supervisor",
            refund_method="SIMULATED bank",
            transaction_reference="SIM" + uuid4().hex,
            transferred_at=business_now(),
            internal_note="SIMULATED concurrent report settlement",
            proof=refund_proof(),
        )
        original = a.result(timeout=15)
        b.result(timeout=15)
    result = report(d)
    assert result["total_sales"] == 800 and result["refunded_sales"] == 200
    assert original["total_sales"] in (800, 1000)
    assert result["late_refund_adjustment"] == original["total_sales"] - 800
    assert not result["reconciliation_required"]


def test_legacy_report_recovered_only_when_original_membership_matches(lifecycle):  # noqa: F811
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute(
            "INSERT INTO sales_reports(report_source,submitted_by_account_id,period_start,period_end,total_sales,total_orders) VALUES('team_leader',%s,%s,%s,1000,1),('team_leader',%s,%s,%s,999,1)",
            (d["sales"], business_today(), business_today(), d["sales"], business_today(), business_today()),
        )
        cur.execute(Path("database/migrations/034_separate_refunds_from_sales_reports.sql").read_text())
        cur.execute(Path("database/migrations/034_separate_refunds_from_sales_reports.sql").read_text())
    case = verified(d)
    complete_refund(d, case)
    reports = repositories.list_sales_reports(report_source="team_leader", team_leader_account_id=d["sales"])
    mapped = next(r for r in reports if r["submitted_total_sales"] == 1000)
    unmatched = next(r for r in reports if r["submitted_total_sales"] == 999)
    assert (
        mapped["total_sales"] == 800
        and mapped["late_refund_adjustment"] == 200
        and not mapped["reconciliation_required"]
    )
    assert unmatched["reconciliation_required"] and unmatched["order_rows"] == []
