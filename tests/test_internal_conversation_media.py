import asyncio
import struct
from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.internal_conversation_media import parse_attachments, validate_video
from app.repositories.orders import require_sufficient_order_payment


def upload(content, kind, name="example.png"):
    return UploadFile(BytesIO(content), filename=name, headers=Headers({"content-type": kind}))


def test_media_rejects_mime_spoofing_and_oversized_files():
    for files, message in [
        ([upload(b"not an image", "image/png")], "valid"),
        ([upload(b"text", "text/html")], "Choose"),
        ([upload(b"x" * (5 * 1024 * 1024 + 1), "image/png")], "5 MB"),
        ([upload(b"x", "image/png") for _ in range(4)], "3 images"),
    ]:
        with pytest.raises(ValueError, match=message):
            asyncio.run(parse_attachments(files))


@pytest.mark.parametrize(
    "kind,content",
    [
        ("video/mp4", b"not a video"),
        ("video/webm", b"\x1aE\xdf\xa3\xff"),
        ("video/mp4", struct.pack(">I4s", 100, b"ftyp") + b"truncated"),
    ],
)
def test_video_requires_a_complete_matching_container(kind, content):
    with pytest.raises(ValueError):
        validate_video(content, kind)


def test_decimal_payment_boundary():
    require_sufficient_order_payment("100.00", "100.00")
    require_sufficient_order_payment("100.01", "100.00")
    with pytest.raises(ValueError, match="Shortfall: PHP 0.01"):
        require_sufficient_order_payment("99.99", "100.00")
