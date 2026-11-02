"""Real messenger presentation and interactions against disposable replacement cases."""

from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from app.repositories import replacement_messages as messages, product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_replacement_workflow import request

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


@pytest.mark.parametrize("width", [1440, 390])
def test_replacement_messenger_layout_switching_retry_and_role_views(lifecycle, monkeypatch, width):  # noqa: F811
    d = lifecycle
    case, _ = request(d)
    other, _ = request(d)
    with get_transaction_cursor() as cur:
        cur.execute(
            """UPDATE accounts SET profile_image_path='img/batangas_premium.png',
            profile_image_position_x=0,profile_image_position_y=75,profile_image_scale=1.4
            WHERE account_id IN (%s,%s)""", (d["reseller"], d["supervisor"])
        )
    messages.send(case, d["reseller"], "Two damaged packs are ready for collection.", str(uuid4()))
    messages.send(case, d["supervisor"], "Thanks. I will coordinate the return with Inventory.", str(uuid4()))
    messages.send(other, d["reseller"], "Separate replacement conversation", str(uuid4()))
    assert messages.threads(d["supervisor"])[0]["return_id"] == other
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, data, *args: session.update(data))
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)

    def as_role(role):
        session.clear()
        session.update(
            account_id=d[role], role_key={"reseller": "reseller", "supervisor": "sales-supervisor", "owner": "owner"}[role],
            team_leader_role="sales-supervisor" if role == "supervisor" else None,
            account_name="Conversation test user", account_email="conversation@example.test",
            portal_expires_at=int(business_now().timestamp()) + 1200,
        )

    output = Path("outputs/replacement-conversation")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://conversation.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(
                req.method, url.path + ("?" + url.query if url.query else ""), content=req.post_data_buffer,
                headers={k: v for k, v in req.headers.items() if k not in {"host", "content-length", "cookie"}},
                follow_redirects=True,
            )
            route.fulfill(status=response.status_code, body=response.content, headers={
                k: v for k, v in response.headers.items()
                if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
            })

        page.route("http://conversation.test/**", bridge)
        as_role("supervisor")
        page.goto(f"http://conversation.test/portal/sales-supervisor/messages?replacement={case}", wait_until="networkidle")
        chat = page.locator("[data-replacement-conversation]")
        log = chat.locator("[data-replacement-message-log]")
        expect(log).to_contain_text("Two damaged packs")
        expect(chat.locator(".replacement-participant h3")).to_have_text("Test Reseller")
        expect(chat.locator(".replacement-thread-head img")).to_have_css("object-position", "0% 75%")
        expect(chat.locator(".reseller-chat-message.is-mine")).to_contain_text("coordinate the return")
        expect(chat.locator(".reseller-chat-message.is-system")).to_be_visible()
        expect(chat.locator('[aria-current="page"]')).to_have_attribute("href", f"?replacement={case}")
        expect(chat.locator('[aria-current="page"] [data-replacement-thread-unread]')).to_have_count(0)
        page.screenshot(path=str(output / f"supervisor-{width}.png"), full_page=True)

        sent_ids = []
        def fail_once(route):
            if route.request.method != "POST":
                route.fallback()
                return
            match = re.search(rb'name="client_message_id"\r\n\r\n([^\r]+)', route.request.post_data_buffer)
            sent_ids.append(match.group(1))
            if len(sent_ids) == 1:
                route.fulfill(status=503, json={"error": "Connection interrupted. Send again to retry."})
            else:
                route.fallback()
        page.route(f"**/returns/{case}/messages", fail_once)
        message = page.get_by_label("Message", exact=True)
        message.fill("Collection arranged.")
        message.press("Shift+Enter")
        message.type("Please keep the packs separate.")
        expect(message).to_have_value("Collection arranged.\nPlease keep the packs separate.")
        message.press("Enter")
        expect(chat.locator("[data-replacement-message-error]")).to_contain_text("Connection interrupted")
        expect(message).to_have_value("Collection arranged.\nPlease keep the packs separate.")
        message.press("Enter")
        expect(message).to_have_value("")
        expect(log).to_contain_text("Please keep the packs separate.")
        assert len(sent_ids) == 2 and sent_ids[0] == sent_ids[1]
        assert fetch_one("SELECT COUNT(*) n FROM return_conversation_messages WHERE return_id=%s AND content LIKE 'Collection arranged.%%'", (case,))["n"] == 1
        assert messages.threads(d["supervisor"])[0]["return_id"] == case
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert log.evaluate("el => el.scrollWidth <= el.clientWidth")

        if width < 761:
            page.get_by_role("button", name="Show replacement conversations").click()
            expect(chat.locator(".replacement-message-stage")).to_be_hidden()
            expect(chat.locator('[aria-current="page"]')).to_be_focused()
            before = fetch_one("SELECT last_message_id FROM return_conversation_reads WHERE return_id=%s AND account_id=%s", (case, d["supervisor"]))["last_message_id"]
            incoming = messages.send(case, d["reseller"], "New message while conversation list is open", str(uuid4()))
            expect(log).to_contain_text(incoming["content"], timeout=10000)
            expect(chat.locator('[aria-current="page"] [data-replacement-thread-unread]')).to_have_text("1")
            assert fetch_one("SELECT last_message_id FROM return_conversation_reads WHERE return_id=%s AND account_id=%s", (case, d["supervisor"]))["last_message_id"] == before
            page.screenshot(path=str(output / f"conversation-list-{width}.png"), full_page=True)
            with page.expect_response(lambda response: response.url.endswith(f"/returns/{case}/read") and response.status == 200):
                chat.locator('[aria-current="page"]').click()
            expect(log).to_be_visible()
            page.get_by_role("button", name="Show replacement conversations").click()
        chat.locator(f'[data-replacement-thread-id="{other}"]').click()
        expect(page).to_have_url(re.compile(f"replacement={other}$"))
        expect(page.get_by_role("log")).to_contain_text("Separate replacement conversation")
        page.get_by_role("link", name="Back to messages", exact=True).click()
        expect(page.locator("[data-sales-reseller-messages]")).to_be_visible()
        expect(page.locator(".replacement-conversation-index")).to_be_visible()
        page.screenshot(path=str(output / f"message-index-{width}.png"), full_page=True)

        as_role("reseller")
        page.goto(f"http://conversation.test/portal/reseller/inbox?replacement={case}", wait_until="networkidle")
        expect(page.locator("[data-contact-name]")).to_have_text("Supervisor")
        log = page.locator("[data-contact-log]")
        expect(log).to_contain_text("Please keep the packs separate.")
        expect(log).to_contain_text("Separate replacement conversation")
        expect(page.locator("[data-contact-avatar] img")).to_be_visible()
        page.screenshot(path=str(output / f"reseller-{width}.png"), full_page=True)
        long_text = "<script>literal text</script> " + "x" * 900
        messages.send(case, d["supervisor"], long_text, str(uuid4()))
        expect(log).to_contain_text(long_text, timeout=10000)
        assert log.evaluate("el => el.scrollWidth <= el.clientWidth")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        as_role("owner")
        page.goto(f"http://conversation.test/portal/owner/messages?replacement={case}", wait_until="networkidle")
        expect(page.get_by_role("log")).to_contain_text("Please keep the packs separate.")
        expect(page.locator(".replacement-message-stage")).to_have_css("display", "grid")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        returns.review_product_return(
            other, d["supervisor"], False, "Synthetic request declined for testing",
            customer_message="This simulated request does not qualify for replacement.",
        )
        as_role("reseller")
        page.goto(f"http://conversation.test/portal/reseller/inbox?replacement={other}", wait_until="networkidle")
        expect(page.locator("[data-contact-status]")).to_contain_text("read-only")
        expect(page.locator("#contact-message")).to_be_disabled()
        assert not errors
        browser.close()
