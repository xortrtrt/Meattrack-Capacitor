from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.chatbot import process_chatbot_message
from app import main


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
