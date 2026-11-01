"""Private attachments for reseller conversations; unrelated to public Live Chat."""

from __future__ import annotations

import hashlib
import struct
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_TOTAL_BYTES = 30 * 1024 * 1024


def _mp4_boxes(content: bytes):
    offset = 0
    while offset < len(content):
        if len(content) - offset < 8:
            raise ValueError("Invalid MP4 box.")
        size, kind = struct.unpack_from(">I4s", content, offset)
        header = 8
        if size == 1:
            if len(content) - offset < 16:
                raise ValueError("Invalid MP4 box.")
            size = struct.unpack_from(">Q", content, offset + 8)[0]
            header = 16
        if size == 0:
            size = len(content) - offset
        if size < header or offset + size > len(content):
            raise ValueError("Invalid MP4 box length.")
        yield kind, content[offset + header : offset + size]
        offset += size


def _ebml_elements(content: bytes):
    offset = 0
    while offset < len(content):
        start = offset
        first = content[offset]
        if not first:
            raise ValueError("Invalid WebM element.")
        width = 9 - first.bit_length()
        if width > 4 or offset + width >= len(content):
            raise ValueError("Invalid WebM element ID.")
        kind = int.from_bytes(content[offset : offset + width], "big")
        offset += width
        first = content[offset]
        if not first:
            raise ValueError("Invalid WebM size.")
        width = 9 - first.bit_length()
        if offset + width > len(content):
            raise ValueError("Invalid WebM size.")
        size = int.from_bytes(content[offset : offset + width], "big") & ((1 << (width * 7)) - 1)
        offset += width
        if size == (1 << (width * 7)) - 1:
            size = len(content) - offset
        if offset + size > len(content) or offset <= start:
            raise ValueError("Truncated WebM element.")
        yield kind, content[offset : offset + size]
        offset += size


def validate_video(content: bytes, content_type: str) -> None:
    if content_type == "video/mp4":
        boxes = list(_mp4_boxes(content))
        if not boxes or boxes[0][0] != b"ftyp" or len(boxes[0][1]) < 8:
            raise ValueError("Choose a valid MP4 video.")
        movies = [body for kind, body in boxes if kind == b"moov"]
        if not movies or not any(kind == b"mdat" and body for kind, body in boxes):
            raise ValueError("The MP4 video is incomplete.")
        for kind, track in _mp4_boxes(movies[0]):
            if kind != b"trak":
                continue
            for child, media in _mp4_boxes(track):
                if child == b"mdia":
                    if any(k == b"hdlr" and len(v) >= 12 and v[8:12] == b"vide" for k, v in _mp4_boxes(media)):
                        return
        raise ValueError("The MP4 file does not contain a video track.")
    elements = list(_ebml_elements(content))
    if not elements or elements[0][0] != 0x1A45DFA3:
        raise ValueError("Choose a valid WebM video.")
    if not any(k == 0x4282 and v == b"webm" for k, v in _ebml_elements(elements[0][1])):
        raise ValueError("Choose a WebM video container.")
    segment = next((v for k, v in elements if k == 0x18538067), None)
    if segment is None:
        raise ValueError("The WebM video is incomplete.")
    video = False
    cluster = False
    for kind, body in _ebml_elements(segment):
        if kind == 0x1F43B675 and body:
            cluster = True
        if kind == 0x1654AE6B:
            for entry, track in _ebml_elements(body):
                if entry == 0xAE and any(k == 0x83 and int.from_bytes(v, "big") == 1 for k, v in _ebml_elements(track)):
                    video = True
    if not video or not cluster:
        raise ValueError("The WebM file must contain a video track and video data.")


async def parse_attachments(uploads: list[UploadFile]) -> list[dict]:
    files = [f for f in uploads if f.filename]
    if len(files) > 3:
        raise ValueError("Attach up to 3 images or videos per message.")
    result = []
    total = 0
    for upload in files:
        kind = upload.content_type or ""
        if kind not in {*IMAGE_TYPES.values(), "video/mp4", "video/webm"}:
            raise ValueError("Choose JPG, PNG, WebP, MP4, or WebM files.")
        limit = (25 if kind.startswith("video/") else 5) * 1024 * 1024
        content = await upload.read(min(limit, MAX_TOTAL_BYTES - total) + 1)
        total += len(content)
        if not content or len(content) > limit or total > MAX_TOTAL_BYTES:
            raise ValueError("Images may be up to 5 MB, videos up to 25 MB, and all attachments up to 30 MB.")
        if kind.startswith("image/"):
            try:
                with Image.open(BytesIO(content)) as img:
                    if IMAGE_TYPES.get(img.format or "") != kind or img.width * img.height > 25_000_000:
                        raise ValueError("Image format or dimensions are not supported.")
                    img.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError("Choose a valid JPG, PNG, or WebP image.") from exc
        else:
            validate_video(content, kind)
        filename = Path((upload.filename or "").replace("\\", "/")).name[:200]
        if not filename or any(ord(c) < 32 for c in filename):
            raise ValueError("The attachment filename is invalid.")
        result.append(
            dict(
                filename=filename,
                content_type=kind,
                content=content,
                size_bytes=len(content),
                checksum_sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return result


def attachment_response(request: Request, attachment: dict) -> Response:
    content = bytes(attachment["content"])
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(attachment['filename'], safe='')}",
    }
    requested = request.headers.get("range", "")
    status = 200
    if requested:
        try:
            if not requested.startswith("bytes=") or "," in requested:
                raise ValueError
            first, last = requested[6:].split("-", 1)
            start = int(first) if first else max(0, len(content) - int(last))
            end = min(int(last), len(content) - 1) if first and last else len(content) - 1
            if start < 0 or start >= len(content) or end < start:
                raise ValueError
        except ValueError:
            raise HTTPException(416, headers={"Content-Range": f"bytes */{len(content)}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
        content = content[start : end + 1]
        status = 206
    return Response(content, status_code=status, media_type=attachment["content_type"], headers=headers)
