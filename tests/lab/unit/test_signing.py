"""Signed download tokens: round trip, tampering, expiry, header safety."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from aegis_api.lab.storage.keys import object_key
from aegis_api.lab.storage.signing import (
    InvalidSignedToken,
    content_disposition,
    safe_content_type,
    sign_download,
    verify_download,
)

KEY = object_key(uuid.uuid4(), uuid.uuid4(), "artifacts", "a1", "v1", "data.csv")
NOW = 1_800_000_000.0


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _payload(token: str) -> dict:
    part = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_round_trip():
    token = sign_download(KEY, ttl_seconds=60, filename="data.csv", content_type="text/csv", now=NOW)
    signed = verify_download(token, now=NOW + 30)
    assert signed.key == KEY
    assert signed.filename == "data.csv"
    assert signed.content_type == "text/csv"
    assert signed.expires_at == int(NOW) + 60


def test_payload_fields_match_contract():
    token = sign_download(KEY, ttl_seconds=60, filename="d.csv", content_type="text/csv", now=NOW)
    assert set(_payload(token)) == {"k", "e", "f", "c"}


def test_expired_token_is_rejected():
    token = sign_download(KEY, ttl_seconds=60, now=NOW)
    verify_download(token, now=NOW + 60)
    with pytest.raises(InvalidSignedToken) as info:
        verify_download(token, now=NOW + 61)
    assert info.value.status_code == 403


def test_tampered_payload_is_rejected():
    token = sign_download(KEY, ttl_seconds=60, now=NOW)
    _payload_part, signature = token.split(".")
    payload = _payload(token)
    for field, value in (
        ("k", object_key(uuid.uuid4(), uuid.uuid4(), "x")),
        ("e", payload["e"] + 3600),
        ("f", "evil.html"),
        ("c", "text/html"),
    ):
        forged = _b64(json.dumps({**payload, field: value}, separators=(",", ":"), sort_keys=True).encode())
        with pytest.raises(InvalidSignedToken):
            verify_download(f"{forged}.{signature}", now=NOW)


def test_tampered_signature_is_rejected():
    token = sign_download(KEY, ttl_seconds=60, now=NOW)
    payload_part, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(InvalidSignedToken):
        verify_download(f"{payload_part}.{flipped}", now=NOW)


def test_signature_from_another_key_is_rejected():
    token = sign_download(KEY, ttl_seconds=60, now=NOW, signing_key=b"k" * 32)
    with pytest.raises(InvalidSignedToken):
        verify_download(token, now=NOW)
    assert verify_download(token, now=NOW, signing_key=b"k" * 32).key == KEY


@pytest.mark.parametrize(
    "token", ["", "abc", "a.b.c", "!!!.???", "x" * 5000, "eyJ9.", ".sig", "e30.AAAA", "bm90anNvbg.AAAA"]
)
def test_malformed_tokens_are_rejected(token):
    with pytest.raises(InvalidSignedToken):
        verify_download(token, now=NOW)


def test_signing_refuses_invalid_keys_and_ttls():
    from aegis_api.lab.storage.base import InvalidObjectKey

    with pytest.raises(InvalidObjectKey):
        sign_download("../../etc/passwd")
    with pytest.raises(ValueError):
        sign_download(KEY, ttl_seconds=0)
    with pytest.raises(ValueError):
        sign_download(KEY, ttl_seconds=8 * 24 * 3600)


def test_header_values_are_sanitized():
    token = sign_download(
        KEY, ttl_seconds=60, filename='evil"\r\nSet-Cookie: x=1.html', content_type="text/html\r\nX: y", now=NOW
    )
    signed = verify_download(token, now=NOW)
    assert "\r" not in (signed.filename or "") and '"' not in (signed.filename or "")
    assert signed.content_type == "application/octet-stream"
    assert content_disposition('a"b\r\n.txt') == 'attachment; filename="a_b_.txt"'
    assert content_disposition(None) == 'attachment; filename="download"'
    assert safe_content_type("Text/CSV; charset=utf-8") == "text/csv"
    assert safe_content_type("nonsense") == "application/octet-stream"
