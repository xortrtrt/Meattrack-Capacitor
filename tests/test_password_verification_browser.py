import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from app.database import get_transaction_cursor
from app.portals import shared
from app.security import hash_password
from test_inventory_postgres import strict_inventory_database  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_all_portal_profiles_verify_before_password_entry(request, monkeypatch):
    request.getfixturevalue("strict_inventory_database")
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, value, seconds: session.update(value))
    monkeypatch.setattr(web_sessions, "_delete_session", lambda token: session.clear())
    codes = []
    monkeypatch.setattr(
        shared, "send_password_change_otp", lambda **kwargs: codes.append(kwargs["otp_code"]) or (True, "accepted")
    )
    output = Path("outputs/password-verification")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://password.test") as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        errors = []
        for label, kind, team, role in [
            ("owner", "owner", None, "owner"),
            ("reseller", "reseller", None, "reseller"),
            ("sales", "team_leader", "sales", "team-leader"),
            ("inventory", "team_leader", "inventory", "team-leader"),
            ("supervisor", "team_leader", "sales-supervisor", "sales-supervisor"),
        ]:
            with get_transaction_cursor() as cur:
                cur.execute(
                    "INSERT INTO accounts(account_type,team_leader_role,name,email,password_hash) VALUES(%s,%s,%s,%s,%s) RETURNING account_id",
                    (kind, team, label, f"{label}@browser.test", hash_password("Original-Password1!")),
                )
                account_id = cur.fetchone()["account_id"]
            session.clear()
            session.update(
                account_id=account_id,
                role_key=role,
                team_leader_role=team,
                portal_expires_at=int(business_now().timestamp()) + 600,
            )
            client.cookies.set(web_sessions.SESSION_COOKIE_NAME, "a" * 48)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.emulate_media(reduced_motion="reduce")
            page.on("pageerror", lambda err: errors.append(str(err)))

            def bridge(route):
                req = route.request
                parts = urlsplit(req.url)
                result = client.request(
                    req.method,
                    parts.path + ("?" + parts.query if parts.query else ""),
                    content=req.post_data_buffer,
                    headers={k: v for k, v in req.headers.items() if k not in {"host", "cookie", "content-length"}},
                    follow_redirects=True,
                )
                route.fulfill(
                    status=result.status_code,
                    body=result.content,
                    headers={
                        k: v
                        for k, v in result.headers.items()
                        if k not in {"content-length", "content-encoding", "transfer-encoding"}
                    },
                )

            page.route("http://password.test/**", bridge)
            page.goto(f"http://password.test/portal/{role}/profile", wait_until="networkidle")
            expect(page.locator('[name="new_password"]')).to_have_count(0)
            form = page.locator("[data-password-change-form]")
            form.get_by_role("button", name="Send OTP", exact=True).click()
            expect(form.locator('[aria-invalid="true"]')).to_have_count(1)
            form.locator('[name="current_password"]').fill("Original-Password1!")
            form.get_by_role("button", name="Send OTP", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('[name="otp_code"]')).to_be_visible()
            expect(page.locator('[name="new_password"]')).to_have_count(0)
            wrong = "000000" if codes[-1] != "000000" else "111111"
            page.locator('[name="otp_code"]').fill(wrong)
            page.get_by_role("button", name="Verify OTP", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('[name="new_password"]')).to_have_count(0)
            page.locator('[name="otp_code"]').fill(codes[-1])
            page.get_by_role("button", name="Verify OTP", exact=True).focus()
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle")
            expect(page.locator('[name="new_password"]')).to_be_visible()
            for width in [1440, 390]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.goto(f"http://password.test/portal/{role}/profile", wait_until="networkidle")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), label
                assert page.locator('[data-password-change-form]').evaluate("element => element.getBoundingClientRect().right <= innerWidth"), label
                page.screenshot(path=str(output / f"{label}-{width}.png"), full_page=True)
            page.locator('[name="new_password"]').fill("Updated-Password2!")
            page.locator('[name="confirm_password"]').fill("Different-Password2!")
            page.get_by_role("button", name="Save new password", exact=True).click()
            expect(page.locator('[name="confirm_password"]')).to_have_attribute("aria-invalid", "true")
            page.locator('[name="confirm_password"]').fill("Updated-Password2!")
            page.get_by_role("button", name="Save new password", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.locator('[name="password"]')).to_be_visible()
            assert not session.get("account_id")
            page.close()
        browser.close()
        assert errors == []
    (output / "browser-result.json").write_text(json.dumps({"roles": 5, "widths": [390, 1440], "errors": errors}))
