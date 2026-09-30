"""Signed download tokens for objects served through the API (local backend, or streaming fallback).

Token format::

    base64url(json({"k": key, "e": expiry_unix, "f": filename, "c": content_type})) + "." +
    base64url(HMAC-SHA256(settings.signed_url_key, <payload part>))

Possession of an unexpired token authorizes exactly one object download; tokens are only minted after an
authorization check (artifact/dataset download permission). The HMAC key is derived solely for signed
URLs, verification is constant-time, and the embedded key is re-validated after verification. Any
tampering (key, expiry, filename, content type or signature) yields 403.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

from aegis_api.config import get_settings
from aegis_api.errors import Forbidden
from aegis_api.lab.storage.base import DEFAULT_CONTENT_TYPE, InvalidObjectKey
from aegis_api.lab.storage.keys import validate_key
from aegis_api.security.uploads import sanitize_filename

MAX_TOKEN_LENGTH = 4096
MAX_TTL_SECONDS = 7 * 24 * 3600
_CONTENT_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")


class InvalidSignedToken(Forbidden):
    """The download link is invalid or has expired."""

    code = "invalid_signed_url"


@dataclass(frozen=True)
class SignedDownload:
    key: str
    expires_at: int
    filename: str | None
    content_type: str


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def safe_content_type(value: str | None) -> str:
    """Normalize a media type for response headers (parameters dropped; unknown/unsafe → octet-stream)."""
    candidate = (value or "").split(";")[0].strip().lower()
    return candidate if _CONTENT_TYPE_RE.match(candidate) else DEFAULT_CONTENT_TYPE


def safe_download_filename(value: str | None) -> str | None:
    if not value:
        return None
    return sanitize_filename(value)[:200] or None


def _mac(key: bytes, payload_part: str) -> bytes:
    return hmac.new(key, payload_part.encode("ascii"), hashlib.sha256).digest()


def sign_download(
    key: str,
    *,
    ttl_seconds: int | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    now: float | None = None,
    signing_key: bytes | None = None,
) -> str:
    """Mint a signed token for ``key``. Call only after the caller's download permission was checked."""
    validate_key(key)
    settings = get_settings()
    ttl = settings.signed_url_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0 or ttl > MAX_TTL_SECONDS:
        raise ValueError("ttl_seconds must be between 1 second and 7 days")
    issued = time.time() if now is None else now
    payload = {
        "k": key,
        "e": int(issued) + ttl,
        "f": safe_download_filename(filename),
        "c": safe_content_type(content_type),
    }
    payload_part = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _mac(signing_key or settings.signed_url_key, payload_part)
    return f"{payload_part}.{_b64encode(signature)}"


def verify_download(token: str, *, now: float | None = None, signing_key: bytes | None = None) -> SignedDownload:
    """Verify signature (constant time) and expiry; return the authorized object. Raises 403 otherwise."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 1:
        raise InvalidSignedToken()
    payload_part, signature_part = token.split(".", 1)
    try:
        signature = _b64decode(signature_part)
    except (ValueError, binascii.Error) as exc:
        raise InvalidSignedToken() from exc
    expected = _mac(signing_key or get_settings().signed_url_key, payload_part)
    if not hmac.compare_digest(signature, expected):
        raise InvalidSignedToken()
    try:
        payload = json.loads(_b64decode(payload_part).decode("utf-8"))
        key = payload["k"]
        expires_at = int(payload["e"])
        filename = payload.get("f")
        content_type = payload.get("c")
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidSignedToken() from exc
    current = time.time() if now is None else now
    if expires_at < current:
        raise InvalidSignedToken("The download link has expired")
    try:
        validate_key(key)
    except InvalidObjectKey as exc:
        raise InvalidSignedToken() from exc
    return SignedDownload(
        key=key,
        expires_at=expires_at,
        filename=safe_download_filename(filename if isinstance(filename, str) else None),
        content_type=safe_content_type(content_type if isinstance(content_type, str) else None),
    )


def content_disposition(filename: str | None, fallback: str = "download") -> str:
    """An ``attachment`` Content-Disposition with a sanitized ASCII filename (header-injection safe)."""
    name = safe_download_filename(filename) or fallback
    return f'attachment; filename="{name}"'
