from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import main


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_media_route_redirects_without_querying_database(monkeypatch):
    monkeypatch.setattr(
        main.data,
        "media_asset_by_filename",
        lambda filename: (_ for _ in ()).throw(AssertionError("database media lookup was called")),
    )
    monkeypatch.setattr(
        main,
        "MEDIA_BASE_URL",
        "https://example.supabase.co/storage/v1/object/public/meattrack-assets/images",
    )
    client = TestClient(main.app)

    response = client.get("/media/background.jpg", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/images/background.jpg")
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
