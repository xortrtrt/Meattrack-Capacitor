import asyncio

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import portal_config
from app.main import app
from app.chatbot.lead_flow import process_chatbot_message
from app.portals import shared


def test_chatbot_directs_human_request_to_contact_details(monkeypatch):
    from app.chatbot import provider
    monkeypatch.setattr(provider, "OPENROUTER_API_KEY", "")
    result = process_chatbot_message("I want to talk to a human", {})

    assert "pat.clem.inc@gmail.com" in result["reply"]
    assert result["handoff"]["offered"] is False
    assert "Talk to a team leader" not in result["suggestions"]


@pytest.mark.parametrize("role_key", ["team-leader", "sales-supervisor"])
def test_staff_live_chat_page_removed(monkeypatch, role_key):
    assert "live-chat" not in {item[0] for item in portal_config.portal_nav_for(role_key, "sales")}
    monkeypatch.setattr(shared, "require_portal_session", lambda request, role: None)
    request = Request({
        "type": "http", "method": "GET", "path": f"/portal/{role_key}/live-chat",
        "headers": [], "query_string": b"", "session": {},
    })
    with pytest.raises(HTTPException) as error:
        asyncio.run(shared.portal(request, role_key, "live-chat"))
    assert error.value.status_code == 404


def test_public_live_chat_routes_removed_but_reseller_messages_remain():
    routes = {(method.upper(), path) for path, operations in app.openapi()["paths"].items()
              for method in operations}

    assert ("POST", "/api/live-chat/start") not in routes
    assert ("GET", "/api/portal/live-chat/workspace") not in routes
    assert ("POST", "/api/portal/live-chat/{conversation_id}/claim") not in routes
    assert ("GET", "/api/portal/reseller/inbox") in routes
    assert ("POST", "/api/portal/reseller/inbox/messages") in routes
    assert TestClient(app).post("/api/live-chat/start", json={}).status_code == 404
