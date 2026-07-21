from __future__ import annotations

import hashlib
import hmac
import re
import secrets


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
PASSWORD_POLICY_MESSAGE = (
    "New password must be at least 8 characters and include uppercase, "
    "lowercase, number, and special character."
)
PASSWORD_POLICY_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$"
)


def validate_password_policy(password: str) -> str:
    cleaned_password = password.strip()
    if not PASSWORD_POLICY_PATTERN.match(cleaned_password):
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    return cleaned_password


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False

    parts = stored_hash.split("$")
    if len(parts) != 4 or parts[0] != PASSWORD_ALGORITHM:
        return hmac.compare_digest(password, stored_hash)

    _, iterations_text, salt, expected_digest = parts
    try:
        iterations = int(iterations_text)
    except ValueError:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual_digest, expected_digest)


def password_needs_rehash(stored_hash: str) -> bool:
    parts = stored_hash.split("$")
    if len(parts) != 4 or parts[0] != PASSWORD_ALGORITHM:
        return True
    try:
        return int(parts[1]) < PASSWORD_ITERATIONS
    except ValueError:
        return True
