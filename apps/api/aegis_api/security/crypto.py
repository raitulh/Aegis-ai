"""Symmetric encryption for stored secrets (Fernet / AES-128-CBC + HMAC-SHA256).

Key management: ``SECRETS_ENCRYPTION_KEY`` must be a urlsafe base64 32-byte key in production
(generate with ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``).
Rotating the key requires re-encrypting the ``secrets`` table (see docs/security.md).
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from aegis_api.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - indicates key mismatch
        raise ValueError("Secret could not be decrypted with the configured key") from exc


def encrypt_json(value: Any) -> str:
    return encrypt_str(json.dumps(value, separators=(",", ":"), sort_keys=True))


def decrypt_json(token: str) -> Any:
    return json.loads(decrypt_str(token))


def last4(value: str) -> str:
    return value[-4:] if len(value) >= 8 else "••••"
