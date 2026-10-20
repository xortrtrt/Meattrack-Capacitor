"""Browser regression coverage for portal gap repairs."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from app import owner_recovery, web_sessions
from app.business_time import business_now, business_today
from app.config import SESSION_COOKIE_NAME
from app.database import fetch_one

BASE_URL = os.getenv("E2E_BASE_URL", "")
pytestmark = [pytest.mark.e2e, pytest.mark.postgres, pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not configured")]


def test_draft_restoration_preserves_distinct_action_buttons():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("http://meattrack.test/portal/owner/alerts", lambda route: route.fulfill(content_type="text/html", body='''
          <main class="portal-main"><details><summary>Override</summary>
            <form action="/portal/owner/alerts/1/status" method="post">
              <input name="reason"><input type="checkbox" name="owner_critical_alerts" value="true" checked>
              <button name="status" value="acknowledged">Acknowledge</button>
              <button name="status" value="resolved">Resolve</button>
            </form></details></main>
          <script id="owner-action-resume" type="application/json">{"action":"/portal/owner/alerts/1/status","fields":{"reason":"Review the alert","status":"acknowledged","owner_critical_alerts":""}}</script>
        '''))
        page.goto("http://meattrack.test/portal/owner/alerts")
        posts = []
        page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
        page.add_script_tag(content=Path("app/static/js/owner_recovery.js").read_text(encoding="utf-8"))
        expect(page.locator('input[name="reason"]')).to_have_value("Review the alert")
        expect(page.get_by_role('button', name='Acknowledge')).to_have_attribute('value', 'acknowledged')
        expect(page.get_by_role('button', name='Resolve')).to_have_attribute('value', 'resolved')
        expect(page.locator('input[type="checkbox"]')).not_to_be_checked()
        assert page.locator('details').evaluate('e => e.open')
        assert not posts
        browser.close()


@pytest.mark.parametrize("width", [375, 390, 768, 1440])
def test_repaired_layouts_dates_and_one_use_owner_recovery(width):
    accounts = {
        "owner": fetch_one("SELECT account_id FROM accounts WHERE account_type='owner' AND is_active LIMIT 1"),
        "inventory": fetch_one("SELECT account_id FROM accounts WHERE team_leader_role='inventory' AND is_active LIMIT 1"),
        "sales": fetch_one("SELECT account_id FROM accounts WHERE team_leader_role='sales' AND is_active LIMIT 1"),
        "sales-supervisor": fetch_one("SELECT account_id FROM accounts WHERE team_leader_role='sales-supervisor' AND is_active LIMIT 1"),
    }
    if not all(accounts.values()):
        pytest.skip("Owner, Inventory leader, and Supervisor accounts are required")
    tokens = []
    screenshots = os.getenv("E2E_SCREENSHOT_DIR")
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": width, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))

            def authenticate(kind, draft=None):
                token = web_sessions._new_token()
                tokens.append(token)
                role = "team-leader" if kind in {"inventory", "sales"} else kind
                state = {"account_id": accounts[kind]["account_id"], "role_key": role,
                         "portal_expires_at": int(business_now().timestamp()) + 600}
                if kind != "owner":
                    state["team_leader_role"] = kind
                if draft:
                    state[owner_recovery.SESSION_KEY] = {**draft, "account_id": state["account_id"], "created_at": int(business_now().timestamp()), "ready": True}
                web_sessions._persist_session(token, state, 600)
                context.add_cookies([{"name": SESSION_COOKIE_NAME, "value": token, "url": BASE_URL}])

            for kind, sections in [("inventory", ["batches"]), ("sales", ["reports", "profile"]), ("sales-supervisor", ["accounts", "reports", "profile"]), ("owner", ["audit", "inventory", "batches"])]:
                authenticate(kind)
                role = "team-leader" if kind in {"inventory", "sales"} else kind
                for section in sections:
                    response = page.goto(f"{BASE_URL}/portal/{role}/{section}", wait_until="networkidle")
                    assert response and response.status == 200
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"{kind}/{section}: {width}"
                    if kind == "owner":
                        for field in page.locator('input[type="date"]').all():
                            expect(field).to_have_value("")
                    if kind == "sales-supervisor" and section == "accounts":
                        for control in page.locator('.inline-assignment-form select').all():
                            assert control.bounding_box()["height"] >= 44
                            assert control.evaluate("e => getComputedStyle(e).borderRadius") != "0px"
                    if kind == "sales-supervisor" and section == "reports":
                        expect(page.locator('input[name="period_start"]')).to_have_value(business_today().replace(day=1).isoformat())
                        expect(page.locator('input[name="period_end"]')).to_have_value(business_today().isoformat())
                    if screenshots:
                        target = Path(screenshots)
                        target.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(target / f"repaired-{kind}-{section}-{width}.png"), full_page=True)

            if width == 1440:
                count = fetch_one("SELECT COUNT(*) AS n FROM orders WHERE order_type='reseller'")["n"]
                last_page = max(1, (count + 5) // 6)
                first_records = []
                for requested in dict.fromkeys([50, 51, last_page]):
                    response = page.goto(f"{BASE_URL}/portal/owner/orders?page={requested}", wait_until="networkidle")
                    assert response and response.status == 200
                    effective = min(requested, last_page)
                    expect(page.locator('.owner-order-pagination')).to_contain_text(f"Page {effective} of {last_page}")
                    expected = fetch_one("SELECT order_id FROM orders WHERE order_type='reseller' ORDER BY order_id DESC LIMIT 1 OFFSET %s", ((effective - 1) * 6,))
                    if expected:
                        assert page.locator('.owner-order-card').first.get_attribute('id') == f"order-{expected['order_id']}"
                        first_records.append(expected['order_id'])
                if last_page > 51:
                    assert len(set(first_records)) == 3

            actor = accounts["owner"]["account_id"]
            action = f"/portal/owner/accounts/{actor}/password-reset"
            authenticate("owner", {"action": action, "return_to": "/portal/owner/accounts", "fields": {"reason": "Browser verification recovery review"}})
            posted = []
            page.on("request", lambda req: posted.append(req.url) if req.method == "POST" else None)
            page.goto(f"{BASE_URL}/portal/owner/accounts", wait_until="networkidle")
            dialog = page.locator("dialog[open]")
            expect(dialog).to_be_visible()
            form = dialog.locator(f'form[action="{action}"]')
            expect(form.locator('input[name="reason"]')).to_have_value("Browser verification recovery review")
            assert not posted
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            page.reload(wait_until="networkidle")
            expect(page.locator("dialog[open]")).to_have_count(0)
            assert not posted
            authenticate("owner", {"action": "/portal/owner/products", "return_to": "/portal/owner/products", "fields": {"product_id": "999999999", "base_price": "12.34"}})
            page.goto(f"{BASE_URL}/portal/owner/products", wait_until="networkidle")
            expect(page.get_by_text("The original action is no longer available on this page. Review the current records and restart the action.", exact=True)).to_be_visible()
            context.close()
            browser.close()
        assert not errors, errors
    finally:
        for token in tokens:
            web_sessions._delete_session(token)
