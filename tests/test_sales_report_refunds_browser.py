from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, complete_refund  # noqa: F401
from test_sales_report_refunds import submit, verified


@pytest.mark.postgres
@pytest.mark.e2e
def test_leader_and_owner_separate_late_refund_in_actual_browser(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    submitted = submit(d)
    complete_refund(d, verified(d))
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    output = Path("outputs/report-refunds")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://report.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def handle(route):
            request = route.request
            url = urlsplit(request.url)
            path = url.path + ("?" + url.query if url.query else "")
            response = client.request(
                request.method,
                path,
                headers={k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}},
                content=request.post_data_buffer,
            )
            route.fulfill(
                status=response.status_code,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
                },
                body=response.content,
            )

        page.route("http://report.test/**", handle)
        for width in (375, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            session.update(
                account_id=d["sales"],
                role_key="team-leader",
                team_leader_role="sales",
                portal_expires_at=int(business_now().timestamp()) + 600,
            )
            response = page.goto("http://report.test/portal/team-leader/reports", wait_until="networkidle")
            assert response.status == 200
            expect(page.locator("[data-report-total-sales]")).to_have_value("PHP 800.00")
            expect(page.locator("[data-report-refund-items]")).to_contain_text("PHP 200.00")
            expect(page.locator(".report-order-total")).to_contain_text("PHP 800.00")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"leader-{width}.png"), full_page=True)
            session.update(account_id=d["owner"], role_key="owner", team_leader_role=None)
            response = page.goto("http://report.test/portal/owner/reports", wait_until="networkidle")
            assert response.status == 200
            page.locator(f'[data-owner-modal-open="owner-report-{submitted["sales_report_id"]}"]').click()
            dialog = page.locator("dialog[open]")
            expect(dialog).to_contain_text("Completed refunds — excluded")
            expect(dialog).to_contain_text("Adjustment after submission")
            assert (
                "1,000.00" in dialog.inner_text()
                and "800.00" in dialog.inner_text()
                and "200.00" in dialog.inner_text()
            )
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(path=str(output / f"owner-{width}.png"), full_page=True)
            page.keyboard.press("Escape")
        assert not errors, errors
        browser.close()
