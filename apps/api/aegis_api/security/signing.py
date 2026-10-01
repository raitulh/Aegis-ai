"""Ed25519 signing for evidence export manifests.

The private key is ``EVIDENCE_SIGNING_KEY`` (urlsafe base64 of a 32-byte seed; required in production). In
development a deterministic key is derived from the development-only seed and flagged as such. The public
key and its id are published at ``GET /api/v1/evidence/signing-key`` so third parties can verify packages
without trusting the key embedded in the package itself.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aegis_api.config import _DEV_ONLY_SEED, get_settings


@lru_cache(maxsize=4)
def _private_key(seed_b64: str | None) -> Ed25519PrivateKey:
    if seed_b64:
        seed = base64.urlsafe_b64decode(seed_b64 + "=" * (-len(seed_b64) % 4))
        if len(seed) != 32:
            raise ValueError("EVIDENCE_SIGNING_KEY must decode to exactly 32 bytes")
    else:
        seed = hashlib.sha256(f"{_DEV_ONLY_SEED}:evidence-signing".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def private_key() -> Ed25519PrivateKey:
    return _private_key(get_settings().evidence_signing_key)


def public_key_b64() -> str:
    raw = private_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode()


def key_id() -> str:
    return hashlib.sha256(public_key_b64().encode()).hexdigest()[:16]


def is_development_key() -> bool:
    return not get_settings().evidence_signing_key


def sign(message: bytes) -> str:
    return base64.urlsafe_b64encode(private_key().sign(message)).decode()
