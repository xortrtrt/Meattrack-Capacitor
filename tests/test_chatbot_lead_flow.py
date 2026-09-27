from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.chatbot import MAX_HISTORY_MESSAGES, process_chatbot_message
from app import chatbot, main


def test_chatbot_collects_and_confirms_reseller_lead():
    state = None
    prompts = [
        "I want to be a reseller",
        "Juan Dela Cruz",
        "Juan Store",
        "juan@example.com",
        "09171234567",
        "Lipa City",
        "reseller package",
        "yes",
        "yes",
    ]

    result = {}
    for prompt in prompts:
        result = process_chatbot_message(prompt, state)
        state = result["state"]

    assert result["action"] == "create_lead"
    assert result["lead"] == {
        "name": "Juan Dela Cruz",
        "business_name": "Juan Store",
        "email": "juan@example.com",
        "contact_number": "09171234567",
        "location": "Lipa City",
        "interest": "reseller package",
    }


def test_chatbot_rejects_unrelated_messages():
    result = process_chatbot_message("who won the basketball game")
    assert "Batangas Premium-related" in result["reply"]


def test_chatbot_uses_live_catalog_and_remembers_follow_up(monkeypatch):
    monkeypatch.setattr(chatbot, "OPENROUTER_API_KEY", "")
    catalog = [
        {
            "name": "Tocino Ala Eh",
            "base_price": "88.00",
            "category": "Pork",
            "pack_size": 500,
            "pack_size_unit": "g",
        }
    ]

    first = process_chatbot_message("Tell me about Tocino Ala Eh", catalog=catalog)
    follow_up = process_chatbot_message("How much is it?", first["state"], catalog=catalog)

    assert "PHP 88" in first["reply"]
    assert "Tocino Ala Eh is PHP 88" in follow_up["reply"]
    assert follow_up["state"]["mode"] == "support"


def test_chatbot_caps_short_term_conversation_history(monkeypatch):
    monkeypatch.setattr(chatbot, "OPENROUTER_API_KEY", "")
    state = None
    for _ in range(7):
        result = process_chatbot_message("What products are available?", state, catalog=[])
        state = result["state"]

    assert len(state["history"]) == MAX_HISTORY_MESSAGES


def test_reseller_lead_can_go_back_edit_and_cancel():
    state = process_chatbot_message("I want to be a reseller")["state"]
    state = process_chatbot_message("Ana Santos", state)["state"]
    state = process_chatbot_message("Ana Store", state)["state"]

    back = process_chatbot_message("back", state)
    assert "business name" in back["reply"].lower()
    assert "business_name" not in back["state"]["data"]

    cancelled = process_chatbot_message("cancel", back["state"])
    assert cancelled["state"] == {}
    assert "cancelled" in cancelled["reply"].lower()


def test_reseller_lead_can_edit_one_confirmed_field():
    state = None
    for prompt in [
        "I want to be a reseller",
        "Ana Santos",
        "Ana Store",
        "old@example.com",
        "09171234567",
        "Lipa City",
        "reseller package",
    ]:
        result = process_chatbot_message(prompt, state)
        state = result["state"]

    edit = process_chatbot_message("change email", state)
    assert "email" in edit["reply"].lower()
    corrected = process_chatbot_message("new@example.com", edit["state"])
    assert corrected["state"]["data"]["email"] == "new@example.com"
    assert "Please confirm" in corrected["reply"]


@pytest.mark.parametrize("quick_reply", ["View products", "Delivery details"])
def test_root_quick_replies_are_never_collected_as_lead_fields(monkeypatch, quick_reply):
    monkeypatch.setattr(chatbot, "OPENROUTER_API_KEY", "")
    catalog = [{"name": "Tocino Ala Eh", "base_price": 88, "category": "Pork"}]
    state = process_chatbot_message("Become a reseller", catalog=catalog)["state"]
    state = process_chatbot_message("Ana Santos", state, catalog=catalog)["state"]
    state = process_chatbot_message("Ana Store", state, catalog=catalog)["state"]

    result = process_chatbot_message(quick_reply, state, catalog=catalog)

    assert result["state"]["mode"] == "support"
    assert "valid email" not in result["reply"].lower()
    assert result["suggestions"] == ["View products", "Delivery details", "Become a reseller"]


def test_become_reseller_quick_reply_restarts_lead_flow():
    state = process_chatbot_message("Become a reseller")["state"]
    state = process_chatbot_message("Ana Santos", state)["state"]

    restarted = process_chatbot_message("Become a reseller", state)

    assert restarted["state"]["data"] == {}
    assert "full name" in restarted["reply"].lower()


def test_chatbot_api_can_reset_server_side_conversation(monkeypatch):
    monkeypatch.setattr(main, "public_products", lambda *args, **kwargs: [])
    client = TestClient(main.app)

    started = client.post("/api/chatbot", json={"message": "I want to be a reseller"})
    reset = client.post("/api/chatbot", json={"action": "reset"})
    after_reset = client.post("/api/chatbot", json={"message": "Ana Santos"})

    assert started.status_code == 200
    assert reset.status_code == 200
    assert "Conversation reset" in reset.json()["reply"]
    assert "Batangas Premium-related" in after_reset.json()["reply"]


def test_public_inquiry_form_is_disabled(monkeypatch):
    monkeypatch.setattr(main.data, "add_inquiry", lambda *args, **kwargs: pytest.fail("public form must not create inquiries"))

    response = TestClient(main.app).post(
        "/inquiries",
        data={
            "name": "Juan Dela Cruz",
            "business_name": "Juan Store",
            "email": "juan@example.com",
            "contact_number": "09171234567",
            "message": "I want to be a reseller.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Public+sign-up+is+disabled" in response.headers["location"]
