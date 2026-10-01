from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


PAYMENT_PROOF_TYPES = {"image/jpeg", "image/png", "image/webp"}
PROFILE_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
PROFILE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
PROFILE_IMAGE_DIR = Path(__file__).resolve().parent / "static" / "uploads" / "profiles"


async def parse_payment_proof_attachments(uploads: list[UploadFile]) -> list[dict]:
    attachments: list[dict[str, object]] = []
    for upload in uploads:
        if not upload.filename:
            continue
        filename = Path(upload.filename).name
        content = await upload.read()
        if not content:
            continue
        if len(attachments) >= 3:
            raise ValueError("Attach up to 3 payment screenshots only.")
        if len(content) > 5 * 1024 * 1024:
            raise ValueError(f"{filename} is larger than 5 MB.")
        content_type = upload.content_type or "application/octet-stream"
        if content_type not in PAYMENT_PROOF_TYPES:
            raise ValueError(f"{filename} must be a JPG, PNG, or WebP screenshot.")
        attachments.append(
            {
                "filename": filename,
                "content_type": content_type,
                "content": content,
                "size_bytes": len(content),
                "checksum_sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return attachments


def _profile_image_extension(content_type: str, content: bytes) -> str:
    extension = PROFILE_IMAGE_TYPES.get(content_type)
    if extension == ".jpg" and content.startswith(b"\xff\xd8\xff"):
        return extension
    if extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return extension
    if extension == ".webp" and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return extension
    raise ValueError("Profile picture must be a valid JPG, PNG, or WebP image.")


async def save_profile_image_upload(account_id: int, upload: UploadFile) -> str:
    if not upload or not upload.filename:
        raise ValueError("Choose a profile picture to upload.")
    content = await upload.read(PROFILE_IMAGE_MAX_BYTES + 1)
    if not content:
        raise ValueError("Choose a profile picture to upload.")
    if len(content) > PROFILE_IMAGE_MAX_BYTES:
        raise ValueError("Profile picture must be 2 MB or smaller.")
    content_type = upload.content_type or "application/octet-stream"
    extension = _profile_image_extension(content_type, content)
    PROFILE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"account-{account_id}-{uuid4().hex}{extension}"
    (PROFILE_IMAGE_DIR / filename).write_bytes(content)
    return f"uploads/profiles/{filename}"


def remove_profile_image_file(profile_image_path: str | None) -> None:
    if not profile_image_path or not profile_image_path.startswith("uploads/profiles/"):
        return
    target = (Path(__file__).resolve().parent / "static" / profile_image_path).resolve()
    try:
        target.relative_to(PROFILE_IMAGE_DIR.resolve())
    except ValueError:
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
