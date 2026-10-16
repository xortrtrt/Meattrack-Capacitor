from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile
from PIL import Image

from app import receipt_ocr
from app.uploads import parse_payment_proof_attachments


def png_bytes(color: str = "white") -> bytes:
    stream = BytesIO()
    Image.new("RGB", (80, 120), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize(
    ("receipt_text", "transaction", "payment_date", "payment_time"),
    [
        (
            "Transaction ID: GCash-AB12 3456\nDate: October 15, 2026\nTime: 9:42 PM",
            "GCash-AB12 3456",
            "2026-10-15",
            "21:42",
        ),
        (
            "Reference No. 123456789012\n15/10/2026 21:42",
            "123456789012",
            "2026-10-15",
            "21:42",
        ),
        (
            "Ref # AB987654321\n2026-10-15 08:05:30",
            "AB987654321",
            "2026-10-15",
            "08:05",
        ),
    ],
)
def test_parse_receipt_text_supports_common_labels_and_philippine_dates(
    receipt_text, transaction, payment_date, payment_time
):
    result = receipt_ocr.parse_receipt_text(receipt_text, 0.8)

    assert result["transaction_number"] == transaction
    assert result["payment_date"] == payment_date
    assert result["payment_time"] == payment_time


def test_parse_receipt_keeps_date_out_of_reference_and_reads_money_sent():
    result = receipt_ocr.parse_receipt_text(
        "Amount 500.00\nTotal Amount Sent ₱500.00\n"
        "Ref No. 9045990596496 Oct 11, 2026 7:04 AM",
        0.9,
    )

    assert result["transaction_number"] == "9045990596496"
    assert result["payment_amount"] == "500.00"
    assert result["payment_date"] == "2026-10-11"
    assert result["payment_time"] == "07:04"


def test_parse_receipt_drops_copy_icon_artifact_after_numeric_reference():
    result = receipt_ocr.parse_receipt_text(
        "Total Amount PHP 454.00\nReference Number 900629 OD\nProcessing Time Realtime",
        0.9,
    )

    assert result["transaction_number"] == "900629"
    assert result["payment_amount"] == "454.00"


def test_scan_uses_highest_confidence_candidates_across_screenshots(monkeypatch):
    results = iter(
        [
            ("Transaction ID: LOW123456\nAmount 400.00\n15/10/2026 08:10", 0.35, "tesseract-test"),
            ("Reference No: HIGH987654\nTotal Amount Sent ₱500.00\nOctober 15, 2026 9:45 PM", 0.92, "tesseract-test"),
        ]
    )
    monkeypatch.setattr(receipt_ocr, "_run_tesseract", lambda _image: next(results))
    attachments = [
        {"filename": "one.png", "content": png_bytes("white")},
        {"filename": "two.png", "content": png_bytes("black")},
    ]

    result = receipt_ocr.scan_payment_proofs(attachments)

    assert result["transaction_number"] == "HIGH987654"
    assert result["payment_date"] == "2026-10-15"
    assert result["payment_time"] == "21:45"
    assert result["payment_amount"] == "500.00"
    assert result["warnings"] == []


def test_unreadable_receipt_returns_manual_entry_warnings(monkeypatch):
    monkeypatch.setattr(receipt_ocr, "_run_tesseract", lambda _image: ("unreadable", 0.08, "tesseract-test"))

    result = receipt_ocr.scan_payment_proofs([{"filename": "receipt.png", "content": png_bytes()}])

    assert result["transaction_number"] == ""
    assert result["payment_date"] == ""
    assert result["payment_time"] == ""
    assert len(result["warnings"]) == 3


def test_ocr_timeout_falls_back_to_manual_entry(monkeypatch):
    def timeout(_image):
        raise RuntimeError("OCR process timed out")

    monkeypatch.setattr(receipt_ocr, "_run_tesseract", timeout)

    result = receipt_ocr.scan_payment_proofs([{"filename": "receipt.png", "content": png_bytes()}])

    assert result["transaction_number"] == ""
    assert any("unavailable" in warning for warning in result["warnings"])


def test_low_confidence_ocr_is_flagged_for_customer_review(monkeypatch):
    monkeypatch.setattr(
        receipt_ocr,
        "_run_tesseract",
        lambda _image: ("Transaction ID: ABC123456\nAmount 500.00\n15/10/2026 08:10", 0.2, "tesseract-test"),
    )

    result = receipt_ocr.scan_payment_proofs([{"filename": "receipt.png", "content": png_bytes()}])

    assert result["transaction_number"] == "ABC123456"
    assert len(result["warnings"]) == 3
    assert all("low confidence" in warning for warning in result["warnings"])


def test_scan_token_is_bound_to_account_and_screenshot_and_tracks_corrections():
    attachments = [{"checksum_sha256": "a" * 64}]
    scan = {
        "transaction_number": "ABC123456",
        "payment_date": "2026-10-15",
        "payment_time": "10:30",
        "payment_amount": "500.00",
        "confidence": 0.88,
        "engine": "tesseract-test",
    }
    token = receipt_ocr.create_scan_token(42, attachments, scan)

    confirmed = receipt_ocr.confirmed_payment_details(
        42, attachments, token, "ABC123456", "2026-10-15", "10:30", "500.00", True
    )
    corrected = receipt_ocr.confirmed_payment_details(
        42, attachments, token, "ABC123457", "2026-10-15", "10:31", "500.01", True
    )

    assert confirmed["extraction_method"] == "ocr_confirmed"
    assert corrected["extraction_method"] == "manual_corrected"
    assert corrected["transaction_key"] == "ABC123457"
    assert str(corrected["payment_amount"]) == "500.01"
    with pytest.raises(ValueError, match="selected files"):
        receipt_ocr.verify_scan_token(42, [{"checksum_sha256": "b" * 64}], token)
    with pytest.raises(ValueError, match="does not match"):
        receipt_ocr.verify_scan_token(43, attachments, token)


def test_confirmed_payment_requires_a_positive_amount():
    attachments = [{"checksum_sha256": "a" * 64}]
    token = receipt_ocr.create_scan_token(42, attachments, {
        "transaction_number": "ABC123456",
        "payment_date": "2026-10-15",
        "payment_time": "10:30",
        "payment_amount": "500.00",
    })

    for invalid_amount in ("0", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="valid amount sent"):
            receipt_ocr.confirmed_payment_details(
                42, attachments, token, "ABC123456", "2026-10-15", "10:30", invalid_amount, True
            )


def test_expired_scan_token_is_rejected(monkeypatch):
    attachments = [{"checksum_sha256": "a" * 64}]
    token = receipt_ocr.create_scan_token(42, attachments, {})
    monkeypatch.setattr(receipt_ocr, "RECEIPT_SCAN_TOKEN_TTL_SECONDS", -1)

    with pytest.raises(ValueError, match="expired"):
        receipt_ocr.verify_scan_token(42, attachments, token)


def test_payment_upload_verifies_real_image_signature():
    valid = UploadFile(filename="receipt.png", file=BytesIO(png_bytes()), headers={"content-type": "image/png"})
    attachment = asyncio.run(parse_payment_proof_attachments([valid]))[0]

    assert attachment["content_type"] == "image/png"
    assert attachment["width"] == 80
    assert attachment["height"] == 120

    fake = UploadFile(filename="receipt.png", file=BytesIO(b"not an image"), headers={"content-type": "image/png"})
    with pytest.raises(ValueError, match="not a valid"):
        asyncio.run(parse_payment_proof_attachments([fake]))


def test_payment_upload_rejects_type_mismatch_and_duplicates():
    content = png_bytes()
    mismatched = UploadFile(
        filename="receipt.jpg", file=BytesIO(content), headers={"content-type": "image/jpeg"}
    )
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(parse_payment_proof_attachments([mismatched]))

    uploads = [
        UploadFile(filename=f"receipt-{index}.png", file=BytesIO(content), headers={"content-type": "image/png"})
        for index in range(2)
    ]
    with pytest.raises(ValueError, match="same receipt image"):
        asyncio.run(parse_payment_proof_attachments(uploads))


def test_payment_upload_rejects_excessive_files_bytes_and_pixels():
    content = png_bytes()
    too_many = [
        UploadFile(filename=f"receipt-{index}.png", file=BytesIO(content + bytes([index])), headers={"content-type": "image/png"})
        for index in range(4)
    ]
    with pytest.raises(ValueError, match="up to 3"):
        asyncio.run(parse_payment_proof_attachments(too_many))

    oversized = UploadFile(
        filename="huge.png",
        file=BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
        headers={"content-type": "image/png"},
    )
    with pytest.raises(ValueError, match="larger than 5 MB"):
        asyncio.run(parse_payment_proof_attachments([oversized]))

    pixel_stream = BytesIO()
    Image.new("1", (5001, 5000)).save(pixel_stream, format="PNG")
    too_many_pixels = UploadFile(
        filename="too-wide.png",
        file=BytesIO(pixel_stream.getvalue()),
        headers={"content-type": "image/png"},
    )
    with pytest.raises(ValueError, match="dimensions"):
        asyncio.run(parse_payment_proof_attachments([too_many_pixels]))
