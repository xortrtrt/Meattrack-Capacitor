import asyncio

import pytest
from fastapi import HTTPException, Request

from app import portal_config
from app.portals import owner, shared, team_leader


@pytest.mark.parametrize("role_key", ["owner", "sales-supervisor"])
def test_legacy_inquiry_page_is_not_a_portal_destination(monkeypatch, role_key):
    monkeypatch.setattr(shared, "require_portal_session", lambda request, role: None)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": f"/portal/{role_key}/inquiries",
        "headers": [],
        "query_string": b"",
        "session": {},
    })

    with pytest.raises(HTTPException) as error:
        asyncio.run(shared.portal(request, role_key, "inquiries"))

    assert error.value.status_code == 404
    assert "inquiries" not in {section for section, *_ in portal_config.portal_nav_for(role_key)}


def test_legacy_inquiry_decision_endpoints_are_removed():
    for router in (owner.router, team_leader.router):
        assert not any("/inquiries/" in route.path for route in router.routes)
