"""Random token generation and keyed hashing (sessions, API keys, magic links, invitations)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from aegis_api.config import get_settings

SESSION_PREFIX = "aegs_"
API_KEY_PREFIX = "aeg_live_"
API_KEY_TEST_PREFIX = "aeg_test_"


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def keyed_hash(value: str) -> str:
    """HMAC-SHA256 with the server-side pepper. Stored instead of the plaintext token."""
    return hmac.new(get_settings().effective_api_key_pepper, value.encode(), hashlib.sha256).hexdigest()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def new_session_token() -> str:
    return SESSION_PREFIX + random_token(32)


def new_api_key(test: bool = False) -> tuple[str, str]:
    """Return (plaintext_key, display_prefix). The plaintext is shown to the user exactly once."""
    key = (API_KEY_TEST_PREFIX if test else API_KEY_PREFIX) + random_token(30).replace("-", "x").replace("_", "y")
    return key, key[:16]


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
