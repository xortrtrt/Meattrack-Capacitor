from __future__ import annotations

import os

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
    send_button.click()
    expect(send_button).to_be_enabled()
    expect(send_button).not_to_have_class("is-loading")
    expect(message_input).to_be_enabled()

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
