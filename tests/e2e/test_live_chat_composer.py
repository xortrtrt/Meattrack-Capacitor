from __future__ import annotations

import json
import os
import time

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app import live_chat
from app.database import get_db_cursor, get_transaction_cursor
from app.security import hash_password


BASE_URL = os.getenv("E2E_BASE_URL")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not configured"),
]

LEADER_EMAIL = "live-chat.e2e@meattrack.test"
LEADER_PASSWORD = "Meattrack-Live-Chat-E2E-2026!"


@pytest.fixture
def page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        yield page
        context.close()
        browser.close()


@pytest.fixture
def active_live_chat() -> str:
    with get_transaction_cursor() as cursor:
        cursor.execute("DELETE FROM accounts WHERE lower(email) = lower(%s);", (LEADER_EMAIL,))
        cursor.execute(
            """
            INSERT INTO accounts (
                account_type, name, email, password_hash, team_leader_role,
                is_active, activation_status, activated_at
            )
            VALUES ('team_leader', 'E2E Live Chat Leader', %s, %s,
                    'sales', true, 'active', now())
            RETURNING account_id;
            """,
            (LEADER_EMAIL, hash_password(LEADER_PASSWORD)),
        )
        account_id = int(cursor.fetchone()["account_id"])

    live_chat.set_presence(account_id, "available")
    conversation = live_chat.create_conversation("E2E Live Chat Visitor", "support")
    conversation_id = str(conversation["conversation_id"])
    live_chat.claim_conversation(account_id, conversation_id)

    yield conversation_id

    with get_transaction_cursor() as cursor:
        cursor.execute("DELETE FROM chat_conversations WHERE conversation_id = %s;", (conversation_id,))
        cursor.execute("DELETE FROM accounts WHERE account_id = %s;", (account_id,))


def test_send_button_recovers_after_each_message(page: Page, active_live_chat: str) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.get_by_label("Email").fill(LEADER_EMAIL)
    page.get_by_label("Password").fill(LEADER_PASSWORD)
    page.locator('input[name="consent"]').check()
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url("**/portal/team-leader/dashboard")

    page.goto(f"{BASE_URL}/portal/team-leader/live-chat", wait_until="networkidle")
    workspace = page.evaluate("async () => (await fetch('/api/portal/live-chat/workspace')).json()")
    assert active_live_chat in {
        str(conversation["conversation_id"]) for conversation in workspace["assigned"]
    }
    page.locator("[data-live-assigned] button", has_text="E2E Live Chat Visitor").click()

    composer = page.locator("[data-live-message-form]")
    message_input = composer.locator("textarea")
    send_button = composer.get_by_role("button", name="Send")

    message_input.fill("First reply")
    message_input.press("Enter")
    expect(send_button).to_be_enabled()
    expect(send_button).not_to_have_class("is-loading")
    expect(message_input).to_be_enabled()
    expect(message_input).to_have_value("")

    message_input.fill("Second reply")
    send_button.click()
    expect(send_button).to_be_enabled()
    expect(page.locator("[data-live-messages] .live-chat-message.is-leader")).to_have_count(2)

    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) AS total
            FROM chat_messages
            WHERE conversation_id = %s AND sender_type = 'team_leader';
            """,
            (active_live_chat,),
        )
        assert int(cursor.fetchone()["total"]) == 2


def test_inquiry_prompt_opens_modal_and_complete_later_closes_it(page: Page) -> None:
    conversation_id = "e2e-visitor-inquiry"
    conversation = {
        "conversation_id": conversation_id,
        "display_name": "E2E Visitor",
        "status": "active",
        "claimed_at": "2026-10-02T10:00:00+08:00",
        "assigned_team_leader_account_id": 42,
        "assigned_team_leader_name": "E2E Leader",
        "inquiry_form_requested_at": "2026-10-02T10:01:00+08:00",
        "has_reseller_inquiry": False,
        "leader_is_typing": False,
    }
    inquiry_submissions: list[str] = []

    def handle_live_chat(route) -> None:
        if route.request.url.endswith("/inquiry"):
            inquiry_submissions.append(route.request.method)
            conversation["has_reseller_inquiry"] = True
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "conversation": conversation,
                        "message": {
                            "chat_message_id": 901,
                            "sender_type": "system",
                            "content": "Your reseller inquiry was submitted.",
                        },
                    }
                ),
            )
            return
        payload = {"messages": []} if "/messages" in route.request.url else {"conversation": conversation}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route(f"**/api/live-chat/{conversation_id}**", handle_live_chat)
    page.goto(f"{BASE_URL}/", wait_until="networkidle")
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, value)",
        [
            "meattrack_live_chat_v1",
            json.dumps({"conversation_id": conversation_id, "savedAt": int(time.time() * 1000)}),
        ],
    )
    page.reload(wait_until="networkidle")
    page.locator(".chatbot-toggle").evaluate("element => element.click()")
    expect(page.locator(".chatbot-toggle")).to_have_attribute("aria-expanded", "true")

    inquiry_prompt = page.locator(".chatbot-inquiry-prompt")
    inquiry_modal = page.locator(".chatbot-inquiry-modal")
    inquiry_dialog = page.locator(".chatbot-inquiry-dialog")
    inquiry_open = inquiry_prompt.get_by_role("button", name="Open form")
    expect(inquiry_prompt).to_be_visible()
    expect(inquiry_modal).to_be_hidden()
    assert inquiry_prompt.evaluate(
        "element => element.nextElementSibling?.matches('[data-chatbot-messages]')"
    )
    assert inquiry_prompt.evaluate("element => getComputedStyle(element).position") == "sticky"
    identity_name = page.locator(".chatbot-identity strong")
    identity_status = page.locator(".chatbot-identity > span")
    expect(identity_name).to_have_text("E2E Leader")
    expect(identity_status).to_have_text("Live sales support")

    conversation["assigned_team_leader_account_id"] = 43
    conversation["assigned_team_leader_name"] = "Transferred Leader"
    expect(identity_name).to_have_text("Transferred Leader", timeout=5000)

    inquiry_open.click()
    expect(inquiry_modal).to_be_visible()
    expect(inquiry_dialog.get_by_label("Full name")).to_be_focused()
    page.keyboard.press("Escape")
    expect(inquiry_modal).to_be_hidden()
    expect(inquiry_open).to_be_focused()

    inquiry_open.click()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(inquiry_prompt).to_be_visible()
    expect(inquiry_modal).to_be_visible()
    assert page.locator(".chatbot-panel").evaluate(
        "element => element.scrollWidth <= element.clientWidth"
    )

    inquiry_dialog.get_by_role("button", name="Complete later").click()
    expect(inquiry_modal).to_be_hidden()
    expect(inquiry_prompt).to_be_visible()

    page.wait_for_timeout(3500)
    expect(inquiry_modal).to_be_hidden()
    expect(inquiry_prompt).to_be_visible()
    assert inquiry_submissions == []

    inquiry_open.click()
    inquiry_dialog.get_by_label("Business name").fill("E2E Store")
    inquiry_dialog.get_by_label("Email").fill("visitor@example.test")
    inquiry_dialog.get_by_label("Contact number").fill("09171234567")
    inquiry_dialog.get_by_label("Business location").fill("Batangas City")
    inquiry_dialog.get_by_role("checkbox").check()
    inquiry_dialog.get_by_role("button", name="Submit inquiry").click()
    expect(inquiry_modal).to_be_hidden()
    expect(inquiry_prompt).to_be_hidden()
    assert inquiry_submissions == ["POST"]
