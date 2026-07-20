from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_IMG_DIR = PROJECT_ROOT / "app" / "static" / "img"
DEFAULT_BUCKET = "meattrack-assets"
DEFAULT_FOLDER = "images"
MAX_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}
CACHE_SECONDS = 31536000


class StorageRequestError(RuntimeError):
    pass


def request_bytes(
    method: str,
    url: str,
    secret_key: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    expected_statuses: tuple[int, ...] = (200,),
) -> tuple[int, bytes]:
    request_headers = {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
    }
    if headers:
        request_headers.update(headers)
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            status = response.status
            response_body = response.read()
    except HTTPError as exc:
        status = exc.code
        response_body = exc.read()
    if status not in expected_statuses:
        detail = response_body.decode("utf-8", errors="replace")[:500]
        raise StorageRequestError(f"Storage request failed with HTTP {status}: {detail}")
    return status, response_body


def ensure_bucket(base_url: str, secret_key: str, bucket: str) -> None:
    bucket_url = f"{base_url}/storage/v1/bucket/{quote(bucket, safe='')}"
    status, _ = request_bytes(
        "GET",
        bucket_url,
        secret_key,
        expected_statuses=(200, 404),
    )
    payload = json.dumps(
        {
            "id": bucket,
            "name": bucket,
            "public": True,
            "file_size_limit": MAX_FILE_SIZE,
            "allowed_mime_types": sorted(ALLOWED_MIME_TYPES),
        }
    ).encode("utf-8")
    if status == 404:
        request_bytes(
            "POST",
            f"{base_url}/storage/v1/bucket",
            secret_key,
            body=payload,
            headers={"Content-Type": "application/json"},
            expected_statuses=(200,),
        )
        print(f"Created public Storage bucket: {bucket}")
        return

    update_payload = json.dumps(
        {
            "public": True,
            "file_size_limit": MAX_FILE_SIZE,
            "allowed_mime_types": sorted(ALLOWED_MIME_TYPES),
        }
    ).encode("utf-8")
    request_bytes(
        "PUT",
        bucket_url,
        secret_key,
        body=update_payload,
        headers={"Content-Type": "application/json"},
        expected_statuses=(200,),
    )
    print(f"Verified public Storage bucket settings: {bucket}")


def image_files() -> list[Path]:
    files = sorted(path for path in STATIC_IMG_DIR.iterdir() if path.is_file())
    if not files:
        raise RuntimeError(f"No image files found in {STATIC_IMG_DIR}")
    return files


def validate_image(path: Path) -> str:
    content_type = mimetypes.guess_type(path.name)[0] or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"Unsupported image type for {path.name}: {content_type or 'unknown'}")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(f"{path.name} exceeds the 5 MB bucket limit")
    return content_type


def object_path(folder: str, filename: str) -> str:
    parts = [part for part in (folder.strip("/"), filename) if part]
    return "/".join(quote(part, safe="") for part in parts)


def upload_and_verify(
    base_url: str,
    secret_key: str,
    bucket: str,
    folder: str,
    path: Path,
) -> str:
    content_type = validate_image(path)
    content = path.read_bytes()
    expected_checksum = hashlib.sha256(content).hexdigest()
    remote_path = object_path(folder, path.name)

    request_bytes(
        "POST",
        f"{base_url}/storage/v1/object/{quote(bucket, safe='')}/{remote_path}",
        secret_key,
        body=content,
        headers={
            "Content-Type": content_type,
            "Cache-Control": f"max-age={CACHE_SECONDS}",
            "x-upsert": "true",
        },
        expected_statuses=(200,),
    )

    _, downloaded = request_bytes(
        "GET",
        f"{base_url}/storage/v1/object/authenticated/{quote(bucket, safe='')}/{remote_path}",
        secret_key,
        expected_statuses=(200,),
    )
    actual_checksum = hashlib.sha256(downloaded).hexdigest()
    if len(downloaded) != len(content) or actual_checksum != expected_checksum:
        raise RuntimeError(f"Verification failed for {path.name}")

    print(f"Uploaded and verified {path.name} ({len(content)} bytes, {expected_checksum[:12]}...)")
    return expected_checksum


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently upload MEATTRACK public images to Supabase Storage."
    )
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--folder", default=DEFAULT_FOLDER)
    args = parser.parse_args()

    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    secret_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not base_url.startswith("https://") or not base_url.endswith(".supabase.co"):
        parser.error("SUPABASE_URL must be the project's https://PROJECT_REF.supabase.co URL")
    if not secret_key:
        parser.error("Set SUPABASE_SECRET_KEY locally; it is never printed or sent to the app")

    ensure_bucket(base_url, secret_key, args.bucket)
    files = image_files()
    for path in files:
        upload_and_verify(base_url, secret_key, args.bucket, args.folder, path)

    public_base = (
        f"{base_url}/storage/v1/object/public/"
        f"{quote(args.bucket, safe='')}/{quote(args.folder.strip('/'), safe='')}"
    ).rstrip("/")
    print(f"Migration complete: {len(files)} images verified.")
    print(f"Set MEDIA_BASE_URL to: {public_base}")


if __name__ == "__main__":
    main()
