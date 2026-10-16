from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import main


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_STYLESHEETS = (
    "/static/css/public/base.css",
    "/static/css/public/pages.css",
    "/static/css/public/landing.css",
    "/static/css/public/visitor-chat.css",
)
PUBLIC_SCRIPTS = (
    "/static/js/public-site.js",
    "/static/js/public-navigation.js",
    "/static/js/landing-experience.js",
    "/static/js/visitor-chat.js",
)


def test_media_route_redirects_to_local_static_assets():
    client = TestClient(main.app)

    response = client.get("/media/background.jpg", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/img/background.jpg"
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert client.get("/media/bad!.png", follow_redirects=False).status_code == 404


def test_external_font_and_icon_cdn_references_are_removed():
    relevant_files = [
        *PROJECT_ROOT.glob("app/templates/**/*.html"),
        *PROJECT_ROOT.glob("app/static/css/**/*.css"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in relevant_files)
    assert "fonts.googleapis.com" not in combined
    assert "unpkg.com" not in combined


def test_missing_pages_use_the_branded_404_and_api_errors_stay_json():
    client = TestClient(main.app)

    page_response = client.get("/this-page-does-not-exist")
    assert page_response.status_code == 404
    assert page_response.headers["content-type"].startswith("text/html")
    assert "This cut isn't" in page_response.text
    assert "Back to home" in page_response.text

    api_response = client.get("/api/this-endpoint-does-not-exist")
    assert api_response.status_code == 404
    assert api_response.headers["content-type"].startswith("application/json")
    assert api_response.json() == {"detail": "Not Found"}


def test_content_security_policy_allows_vendored_ably_runtime_hosts():
    response = TestClient(main.app).get("/")

    policy = response.headers["content-security-policy"]
    assert "https://*.ably.io" in policy
    assert "wss://*.ably.net" in policy
    assert "https://*.ably-realtime.com" in policy
    assert "wss://*.ably-realtime.com" in policy


def test_vendored_assets_exist_and_receive_immutable_cache_headers():
    client = TestClient(main.app)
    asset_paths = (
        "/static/vendor/lucide-0.514.0.min.js",
        "/static/fonts/rubik-latin-300-800-v31.woff2",
    )
    for asset_path in asset_paths:
        response = client.get(asset_path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_public_stylesheets_are_split_and_served():
    client = TestClient(main.app)
    for asset_path in PUBLIC_STYLESHEETS:
        response = client.get(asset_path)
        assert response.status_code == 200
        assert response.text.strip()

    public_templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PROJECT_ROOT.glob("app/templates/*.html")
    )
    assert "css/public.css" not in public_templates
    assert "css/public/base.css" in public_templates
    assert "css/public/pages.css" in public_templates
    base_css = (PROJECT_ROOT / "app/static/css/public/base.css").read_text(encoding="utf-8")
    assert 'url("../../fonts/rubik-latin-300-800-v31.woff2")' in base_css


def test_public_javascript_modules_are_split_and_served():
    client = TestClient(main.app)
    for asset_path in PUBLIC_SCRIPTS:
        response = client.get(asset_path)
        assert response.status_code == 200
        assert response.text.strip()

    entrypoint = (PROJECT_ROOT / "app/static/js/public-site.js").read_text(encoding="utf-8")
    assert 'from "./public-navigation.js"' in entrypoint
    assert 'from "./landing-experience.js"' in entrypoint
    assert 'from "./visitor-chat.js?v=20261016-product-preview-1"' in entrypoint


def test_cart_autosave_uses_the_form_action_attribute():
    source = (PROJECT_ROOT / "app/static/js/portal.js").read_text(encoding="utf-8")

    # A named input called "action" shadows HTMLFormElement.action in browsers.
    assert 'fetch(form.getAttribute("action") || window.location.pathname' in source


def test_portal_brand_does_not_link_out_of_the_authenticated_portal():
    template = (PROJECT_ROOT / "app/templates/portals/base.html").read_text(
        encoding="utf-8"
    )

    assert '<div class="brand portal-brand">' in template
    assert '<a class="brand portal-brand"' not in template


def test_portal_sign_out_uses_shared_confirmation_dialog():
    template = (PROJECT_ROOT / "app/templates/portals/base.html").read_text(
        encoding="utf-8"
    )
    script = (PROJECT_ROOT / "app/static/js/portal.js").read_text(encoding="utf-8")
    stylesheet = (PROJECT_ROOT / "app/static/css/portal_base.css").read_text(
        encoding="utf-8"
    )

    assert "data-logout-form" in template
    assert "data-logout-modal" in template
    assert "data-logout-confirm" in template
    assert "bindSignOutConfirmation" in script
    assert "HTMLFormElement.prototype.submit.call(form)" in script
    assert ".portal-logout-button" in stylesheet
    assert ".logout-confirmation-card" in stylesheet
