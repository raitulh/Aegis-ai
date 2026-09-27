"""Webhook payload signing: ``Aegis-Signature: t=<unix>,v1=<hex hmac-sha256(secret, f"{t}.{body}")>``."""

from __future__ import annotations

import hashlib
import hmac
import time

DEFAULT_TOLERANCE_SECONDS = 300


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(
    secret: str, body: bytes, header: str, tolerance: int = DEFAULT_TOLERANCE_SECONDS, now: int | None = None
) -> bool:
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        ts = int(parts["t"])
        received = parts["v1"]
    except (ValueError, KeyError):
        return False
    current = int(now if now is not None else time.time())
    if abs(current - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
