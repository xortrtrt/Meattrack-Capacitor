from __future__ import annotations

import re
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

import pytesseract
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, ImageOps
from pytesseract import Output

from app.business_time import BUSINESS_ZONE, business_now
from app.config import RECEIPT_SCAN_TOKEN_TTL_SECONDS, SESSION_SECRET_KEY, TESSERACT_CMD


if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

OCR_TIMEOUT_SECONDS = 8
OCR_MAX_PIXELS = 25_000_000
OCR_TOKEN_SALT = "meattrack-receipt-scan-v1"
TRANSACTION_PATTERN = re.compile(
    r"(?:transaction|reference|ref(?:erence)?|trace|receipt)\s*"
    r"(?:id|no\.?|number|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9 -]{5,63})",
    re.IGNORECASE,
)
MONTH_NAME_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
TRANSACTION_BOUNDARY_PATTERN = re.compile(
    rf"\s+(?={MONTH_NAME_PATTERN}\s+\d{{1,2}}\b)"
    r"|\s+(?=\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?\b)"
    r"|\s{2,}|\b(?:date|time|amount|paid)\b",
    re.IGNORECASE,
)
AMOUNT_PATTERN = re.compile(
    r"(?P<label>total\s+amount\s+sent|amount\s+sent|money\s+sent|total\s+amount|amount|paid)"
    r"\s*(?:\(php\))?\s*[:#-]?\s*(?:PHP|PHP\s*₱|₱|P)?\s*"
    r"(?P<amount>\d{1,10}(?:,\d{3})*(?:\.\d{1,2})?|\d{1,10}\.\d{1,2})\b",
    re.IGNORECASE,
)
DATE_PATTERNS = (
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),
    re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{4}\b",
        re.IGNORECASE,
    ),
)
TIME_PATTERN = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\s*(?:AM|PM)?\b", re.IGNORECASE)
DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
    "%m/%d/%y", "%d/%m/%y", "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y",
    "%d %b %Y", "%d %B %Y",
)
TIME_FORMATS = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")


def normalize_transaction_number(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _clean_transaction_candidate(value: str) -> str:
    candidate = value.strip(" -:#")
    # Copy/share icons beside short numeric references are sometimes read as
    # one or two trailing letters (for example, "900629 OD").
    icon_artifact = re.fullmatch(r"(?P<reference>\d{6,64})\s+[A-Z]{1,2}", candidate, re.IGNORECASE)
    return icon_artifact.group("reference") if icon_artifact else candidate


def _parse_date(value: str) -> date | None:
    clean = re.sub(r"\s+", " ", value.replace(",", ", ")).strip()
    clean = re.sub(r",\s+", ", ", clean)
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(clean, date_format).date()
        except ValueError:
            continue
    return None


def _parse_time(value: str) -> time | None:
    clean = re.sub(r"\s+", " ", value.upper()).strip()
    for time_format in TIME_FORMATS:
        try:
            return datetime.strptime(clean, time_format).time()
        except ValueError:
            continue
    return None


def parse_receipt_text(text: str, ocr_confidence: float = 0.0) -> dict[str, Any]:
    normalized_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    transaction_number = ""
    transaction_match = TRANSACTION_PATTERN.search(normalized_text)
    if transaction_match:
        candidate = transaction_match.group(1).splitlines()[0].strip(" -:#")
        candidate = TRANSACTION_BOUNDARY_PATTERN.split(candidate, maxsplit=1)[0]
        candidate = _clean_transaction_candidate(candidate)
        if 6 <= len(normalize_transaction_number(candidate)) <= 64:
            transaction_number = candidate.strip()

    payment_amount = None
    amount_label = ""
    amount_priority = -1
    priorities = {
        "total amount sent": 5,
        "amount sent": 4,
        "money sent": 4,
        "total amount": 3,
        "amount": 2,
        "paid": 1,
    }
    for match in AMOUNT_PATTERN.finditer(normalized_text):
        try:
            candidate_amount = Decimal(match.group("amount").replace(",", "")).quantize(Decimal("0.01"))
        except InvalidOperation:
            continue
        if (
            not candidate_amount.is_finite()
            or candidate_amount <= 0
            or candidate_amount > Decimal("9999999999.99")
        ):
            continue
        label = re.sub(r"\s+", " ", match.group("label").lower()).strip()
        priority = priorities.get(label, 0)
        if payment_amount is None or priority > amount_priority:
            payment_amount = candidate_amount
            amount_label = label
            amount_priority = priority

    payment_date = None
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(normalized_text):
            payment_date = _parse_date(match.group(0))
            if payment_date:
                break
        if payment_date:
            break

    payment_time = None
    for match in TIME_PATTERN.finditer(normalized_text):
        payment_time = _parse_time(match.group(0))
        if payment_time:
            break

    base_confidence = max(0.0, min(float(ocr_confidence), 1.0))
    transaction_confidence = min(1.0, base_confidence + 0.15) if transaction_number else 0.0
    datetime_confidence = min(1.0, base_confidence + 0.1) if payment_date and payment_time else 0.0
    amount_confidence = min(1.0, base_confidence + (0.15 if amount_label == "total amount sent" else 0.1)) if payment_amount else 0.0
    return {
        "transaction_number": transaction_number,
        "payment_date": payment_date.isoformat() if payment_date else "",
        "payment_time": payment_time.strftime("%H:%M") if payment_time else "",
        "payment_amount": f"{payment_amount:.2f}" if payment_amount is not None else "",
        "transaction_confidence": round(transaction_confidence, 4),
        "datetime_confidence": round(datetime_confidence, 4),
        "amount_confidence": round(amount_confidence, 4),
    }


def _run_tesseract(image: Image.Image) -> tuple[str, float, str]:
    data = pytesseract.image_to_data(
        image,
        lang="eng",
        config="--oem 3 --psm 6",
        output_type=Output.DICT,
        timeout=OCR_TIMEOUT_SECONDS,
    )
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    confidences: list[float] = []
    for index, word in enumerate(data.get("text", [])):
        clean_word = str(word).strip()
        if not clean_word:
            continue
        key = (
            int(data["page_num"][index]),
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        lines.setdefault(key, []).append(clean_word)
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1
        if confidence >= 0:
            confidences.append(confidence / 100)
    text = "\n".join(" ".join(words) for words in lines.values())
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return text, average_confidence, f"tesseract-{pytesseract.get_tesseract_version()}"


def _prepare_image(content: bytes) -> Image.Image:
    with Image.open(BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("L")
        if image.width < 1600:
            requested_scale = min(2.5, 1600 / max(1, image.width))
            pixel_safe_scale = math.sqrt(OCR_MAX_PIXELS / max(1, image.width * image.height))
            scale = min(requested_scale, pixel_safe_scale)
            if scale > 1:
                image = image.resize((round(image.width * scale), round(image.height * scale)))
        image = ImageOps.autocontrast(image)
        return image.copy()


def scan_payment_proofs(attachments: list[dict]) -> dict[str, Any]:
    best_transaction: dict[str, Any] | None = None
    best_datetime: dict[str, Any] | None = None
    best_amount: dict[str, Any] | None = None
    engine = "tesseract"
    errors: list[str] = []
    for attachment in attachments:
        try:
            image = _prepare_image(bytes(attachment["content"]))
            text, confidence, engine = _run_tesseract(image)
            parsed = parse_receipt_text(text, confidence)
            parsed["source_filename"] = str(attachment["filename"])
            if parsed["transaction_number"] and (
                best_transaction is None
                or parsed["transaction_confidence"] > best_transaction["transaction_confidence"]
            ):
                best_transaction = parsed
            if parsed["payment_date"] and parsed["payment_time"] and (
                best_datetime is None or parsed["datetime_confidence"] > best_datetime["datetime_confidence"]
            ):
                best_datetime = parsed
            if parsed["payment_amount"] and (
                best_amount is None or parsed["amount_confidence"] > best_amount["amount_confidence"]
            ):
                best_amount = parsed
        except (OSError, RuntimeError, pytesseract.TesseractError, pytesseract.TesseractNotFoundError) as exc:
            errors.append(f"{attachment['filename']}: {exc}")

    transaction_number = best_transaction["transaction_number"] if best_transaction else ""
    payment_date = best_datetime["payment_date"] if best_datetime else ""
    payment_time = best_datetime["payment_time"] if best_datetime else ""
    payment_amount = best_amount["payment_amount"] if best_amount else ""
    confidence_values = [
        best_transaction["transaction_confidence"] if best_transaction else 0.0,
        best_datetime["datetime_confidence"] if best_datetime else 0.0,
        best_amount["amount_confidence"] if best_amount else 0.0,
    ]
    confidence = round(sum(confidence_values) / len(confidence_values), 4)
    warnings = []
    if not transaction_number:
        warnings.append("Transaction number could not be read. Enter it exactly as shown on the receipt.")
    elif best_transaction and best_transaction["transaction_confidence"] < 0.65:
        warnings.append("The transaction number was read with low confidence. Check it carefully against the receipt.")
    if not payment_date or not payment_time:
        warnings.append("Payment date or time could not be read. Enter it exactly as shown on the receipt.")
    elif best_datetime and best_datetime["datetime_confidence"] < 0.65:
        warnings.append("The payment date or time was read with low confidence. Check it carefully against the receipt.")
    if not payment_amount:
        warnings.append("The amount sent could not be read. Enter it exactly as shown on the receipt.")
    elif best_amount and best_amount["amount_confidence"] < 0.65:
        warnings.append("The amount sent was read with low confidence. Check it carefully against the receipt.")
    if errors and not transaction_number and not payment_date:
        warnings.append("Receipt recognition was unavailable. You can enter the receipt details manually.")
    return {
        "transaction_number": transaction_number,
        "payment_date": payment_date,
        "payment_time": payment_time,
        "payment_amount": payment_amount,
        "confidence": confidence,
        "transaction_confidence": best_transaction["transaction_confidence"] if best_transaction else 0.0,
        "datetime_confidence": best_datetime["datetime_confidence"] if best_datetime else 0.0,
        "amount_confidence": best_amount["amount_confidence"] if best_amount else 0.0,
        "source_filename": (
            best_transaction["source_filename"] if best_transaction else
            best_datetime["source_filename"] if best_datetime else
            best_amount["source_filename"] if best_amount else ""
        ),
        "engine": engine,
        "warnings": warnings,
    }


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SESSION_SECRET_KEY, salt=OCR_TOKEN_SALT)


def create_scan_token(account_id: int, attachments: list[dict], scan: dict[str, Any]) -> str:
    return _serializer().dumps({
        "account_id": account_id,
        "checksums": sorted(str(item["checksum_sha256"]) for item in attachments),
        "transaction_number": scan.get("transaction_number", ""),
        "payment_date": scan.get("payment_date", ""),
        "payment_time": scan.get("payment_time", ""),
        "payment_amount": scan.get("payment_amount", ""),
        "confidence": float(scan.get("confidence", 0.0)),
        "engine": str(scan.get("engine", "tesseract")),
    })


def verify_scan_token(account_id: int, attachments: list[dict], token: str) -> dict[str, Any]:
    if not token:
        raise ValueError("Read the payment receipt before placing the order.")
    try:
        payload = _serializer().loads(token, max_age=RECEIPT_SCAN_TOKEN_TTL_SECONDS)
    except SignatureExpired:
        raise ValueError("The receipt scan expired. Read the receipt again before placing the order.")
    except BadSignature:
        raise ValueError("The receipt scan could not be verified. Read the receipt again.")
    expected_checksums = sorted(str(item["checksum_sha256"]) for item in attachments)
    if payload.get("account_id") != account_id or payload.get("checksums") != expected_checksums:
        raise ValueError("The receipt scan does not match this account or the selected files. Read the receipt again.")
    return payload


def confirmed_payment_details(
    account_id: int,
    attachments: list[dict],
    token: str,
    transaction_number: str,
    payment_date: str,
    payment_time: str,
    payment_amount: str,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Confirm that the payment details match the receipt.")
    scan = verify_scan_token(account_id, attachments, token)
    clean_transaction = " ".join(transaction_number.strip().split())
    transaction_key = normalize_transaction_number(clean_transaction)
    if not 6 <= len(transaction_key) <= 64:
        raise ValueError("Enter a valid transaction number from the payment receipt.")
    try:
        paid_date = date.fromisoformat(payment_date)
        paid_time = time.fromisoformat(payment_time)
    except ValueError:
        raise ValueError("Enter a valid payment date and time from the receipt.")
    paid_at = datetime.combine(paid_date, paid_time, tzinfo=BUSINESS_ZONE)
    if paid_at > business_now() + timedelta(minutes=10):
        raise ValueError("Payment date and time cannot be in the future.")
    try:
        clean_amount = Decimal(payment_amount.replace(",", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("Enter a valid amount sent from the payment receipt.")
    if (
        not clean_amount.is_finite()
        or clean_amount <= 0
        or clean_amount > Decimal("9999999999.99")
    ):
        raise ValueError("Enter a valid amount sent from the payment receipt.")
    scanned_at = ""
    if scan.get("payment_date") and scan.get("payment_time"):
        scanned_at = f"{scan['payment_date']}T{scan['payment_time']}"
    extraction_method = "ocr_confirmed"
    if (
        normalize_transaction_number(str(scan.get("transaction_number", ""))) != transaction_key
        or scanned_at != f"{payment_date}T{payment_time}"
        or str(scan.get("payment_amount", "")) != f"{clean_amount:.2f}"
    ):
        extraction_method = "manual_corrected"
    return {
        "transaction_number": clean_transaction,
        "transaction_key": transaction_key,
        "paid_at": paid_at,
        "payment_amount": clean_amount,
        "ocr_confidence": max(0.0, min(float(scan.get("confidence", 0.0)), 1.0)),
        "extraction_method": extraction_method,
        "confirmed_at": business_now(),
        "ocr_engine": str(scan.get("engine", "tesseract"))[:80],
    }
