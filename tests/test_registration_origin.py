import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import security_controls
from app.routers import reseller_registration


def test_registration_response_preserves_origin_without_referring_invitation(monkeypatch):
    class Templates:
        def TemplateResponse(self, *args, **kwargs):
            return kwargs

    monkeypatch.setattr(reseller_registration, "templates", Templates())
    request = Request({"type": "http", "session": {}})
    response = reseller_registration._render(request, invitation={"invitation_id": 1})
    assert response["headers"]["Referrer-Policy"] == "strict-origin"
    assert response["headers"]["Cache-Control"] == "no-store"


@pytest.mark.parametrize("origin,accepted", [
    ("https://registration.example.test", True),
    ("null", False),
    ("https://unrelated.example.test", False),
    ("", False),
])
def test_production_registration_origin_validation(monkeypatch, origin, accepted):
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", True)
    monkeypatch.setattr(security_controls, "APP_ENV", "production")
    monkeypatch.setattr(security_controls, "APP_BASE_URL", "https://registration.example.test")
    request = Request({
        "type": "http", "method": "POST", "path": "/register/reseller",
        "headers": [(b"host", b"registration.example.test"),
                    (b"origin", origin.encode()), (b"x-csrf-token", b"test-token")],
        "session": {"csrf_token": "test-token"},
    })
    if accepted:
        asyncio.run(security_controls.enforce_csrf(request))
    else:
        with pytest.raises(HTTPException, match="Invalid request origin"):
            asyncio.run(security_controls.enforce_csrf(request))
