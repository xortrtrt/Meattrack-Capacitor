from __future__ import annotations

from tools import migrate_images_to_storage as migration


def test_existing_bucket_is_updated_to_required_public_settings(monkeypatch):
    calls = []

    def fake_request(method, url, secret_key, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return 200, b"{}"
        return 200, b"{}"

    monkeypatch.setattr(migration, "request_bytes", fake_request)

    migration.ensure_bucket("https://example.supabase.co", "secret", "meattrack-assets")

    assert [call[0] for call in calls] == ["GET", "PUT"]
    assert b'"public": true' in calls[1][2]["body"]
    assert b'"file_size_limit": 5242880' in calls[1][2]["body"]


def test_upload_is_repeatable_and_checksum_verified(monkeypatch, tmp_path):
    image = tmp_path / "example.png"
    image.write_bytes(b"stable-image-content")
    calls = []

    def fake_request(method, url, secret_key, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return 200, image.read_bytes()
        return 200, b"{}"

    monkeypatch.setattr(migration, "request_bytes", fake_request)

    first = migration.upload_and_verify(
        "https://example.supabase.co", "secret", "meattrack-assets", "images", image
    )
    second = migration.upload_and_verify(
        "https://example.supabase.co", "secret", "meattrack-assets", "images", image
    )

    assert first == second
    uploads = [call for call in calls if call[0] == "POST"]
    assert len(uploads) == 2
    assert all(call[2]["headers"]["x-upsert"] == "true" for call in uploads)
