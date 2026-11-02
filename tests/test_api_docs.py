from fastapi.testclient import TestClient
import pytest

from app.application import create_app


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_documentation_allows_its_cdn_assets(path):
    with TestClient(create_app()) as client:
        response = client.get(path)
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    directives = dict(part.strip().split(" ", 1) for part in policy.split(";") if part.strip())
    assert "https://cdn.jsdelivr.net" in directives["script-src"].split()
    assert "https://cdn.jsdelivr.net" in directives["style-src"].split()
    assert "https://cdn.jsdelivr.net" not in directives["default-src"].split()


def test_documentation_cdn_exception_does_not_apply_to_other_routes():
    with TestClient(create_app()) as client:
        for path in ("/openapi.json", "/docs/oauth2-redirect", "/missing-page"):
            response = client.get(path)
            assert "https://cdn.jsdelivr.net" not in response.headers["content-security-policy"]
