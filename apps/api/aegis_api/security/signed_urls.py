"""Short-lived, HMAC-signed download tokens for API-proxied artifact downloads (local storage backend, or when
presigned object-store URLs are disabled). Tokens bind the organization, object and expiry; they are verified in
constant time and never embed storage credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from aegis_api.config import get_settings
from aegis_api.errors import Forbidden


def _key() -> bytes:
    return hashlib.sha256(get_settings().effective_api_key_pepper + b":download-urls").digest()


def sign(organization_id: str, object_id: str, *, ttl_seconds: int) -> str:
    payload = {"o": organization_id, "i": object_id, "e": int(time.time()) + ttl_seconds}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(_key(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def verify(token: str, *, object_id: str) -> str:
    """Return the organization id bound to a valid token for ``object_id``; raise ``Forbidden`` otherwise."""
    try:
        raw_b64, mac_b64 = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
        mac = base64.urlsafe_b64decode(mac_b64 + "=" * (-len(mac_b64) % 4))
    except (ValueError, TypeError) as exc:
        raise Forbidden("Invalid download token") from exc
    expected = hmac.new(_key(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise Forbidden("Invalid download token")
    payload = json.loads(raw)
    if payload.get("i") != object_id or int(payload.get("e", 0)) < time.time():
        raise Forbidden("Download token expired or not valid for this object")
    return str(payload["o"])
