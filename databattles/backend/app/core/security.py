"""Cryptographic helpers: password hashing, opaque tokens, HMAC signatures, encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_hasher = PasswordHasher()  # argon2id with library defaults (OWASP-aligned)

# A deliberately small deny-list; length requirement does most of the work.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123",
    "iloveyou12", "welcome123", "admin12345", "passw0rd12", "abc1234567", "databattles",
}

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Burn comparable time so missing accounts are not distinguishable by timing.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def password_problems(password: str, email: str | None = None) -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 256:
        problems.append("Use at most 256 characters.")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("This password is too common.")
    if email and password.lower() == email.lower():
        problems.append("Password must not match your email address.")
    return problems


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Tokens are high-entropy, so a fast keyed hash is sufficient for lookup."""
    return hmac.new(settings.SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def hash_identifier(value: str) -> str:
    """Pseudonymize identifiers (IPs, emails) for security logs."""
    return hmac.new(settings.SECRET_KEY.encode(), ("id:" + value).encode(), hashlib.sha256).hexdigest()[:32]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(value: str, purpose: str) -> str:
    mac = hmac.new(settings.SECRET_KEY.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).digest()
    return _b64(mac)


def verify_sign(value: str, signature: str, purpose: str) -> bool:
    return hmac.compare_digest(sign(value, purpose), signature)


def make_signed_payload(payload: dict[str, Any], purpose: str, ttl_seconds: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    return f"{raw}.{sign(raw, purpose)}"


def read_signed_payload(token: str, purpose: str) -> dict[str, Any] | None:
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not verify_sign(raw, sig, purpose):
        return None
    try:
        body = json.loads(_unb64(raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    return body


def encrypt_secret(plaintext: str) -> str:
    return Fernet(settings.fernet_key).encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str | None:
    try:
        return Fernet(settings.fernet_key).decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
