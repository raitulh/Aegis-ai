"""Cryptographic digital signing and verification for evidence chain heads (Ed25519).

Pure Python module: provides court-admissible, third-party verifiable seals over audit hash chains.
Does not import database or FastAPI layers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


@dataclass(frozen=True)
class EvidenceSeal:
    audit_id: str
    head_hash: str
    algorithm: str
    public_key_hex: str
    signature_hex: str
    signed_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "audit_id": self.audit_id,
            "head_hash": self.head_hash,
            "algorithm": self.algorithm,
            "public_key_hex": self.public_key_hex,
            "signature_hex": self.signature_hex,
            "signed_at": self.signed_at,
        }


def generate_signing_keypair() -> tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Generate a new Ed25519 keypair for evidence signing."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def export_private_key_raw(private_key: ed25519.Ed25519PrivateKey) -> str:
    """Export the 32-byte private key as a hex string."""
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return raw.hex()


def import_private_key_raw(key_hex: str) -> ed25519.Ed25519PrivateKey:
    """Import a 32-byte Ed25519 private key from hex."""
    return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))


def export_public_key_raw(public_key: ed25519.Ed25519PublicKey) -> str:
    """Export the 32-byte public key as a hex string."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw.hex()


def import_public_key_raw(key_hex: str) -> ed25519.Ed25519PublicKey:
    """Import a 32-byte Ed25519 public key from hex."""
    return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))


def _canonical_payload(audit_id: str, head_hash: str, signed_at: str) -> bytes:
    canonical = json.dumps(
        {"audit_id": audit_id, "head_hash": head_hash, "signed_at": signed_at},
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical.encode("utf-8")


def sign_evidence_head(
    audit_id: str,
    head_hash: str,
    signed_at: str,
    private_key: ed25519.Ed25519PrivateKey,
) -> EvidenceSeal:
    """Cryptographically sign an audit evidence chain head using Ed25519."""
    payload = _canonical_payload(audit_id, head_hash, signed_at)
    signature = private_key.sign(payload)
    public_key = private_key.public_key()

    return EvidenceSeal(
        audit_id=audit_id,
        head_hash=head_hash,
        algorithm="Ed25519",
        public_key_hex=export_public_key_raw(public_key),
        signature_hex=signature.hex(),
        signed_at=signed_at,
    )


def verify_evidence_seal(seal_data: dict[str, Any] | EvidenceSeal) -> bool:
    """Verify an Ed25519 evidence seal against its embedded public key and canonical payload."""
    data = seal_data.to_dict() if isinstance(seal_data, EvidenceSeal) else seal_data

    audit_id = str(data.get("audit_id", ""))
    head_hash = str(data.get("head_hash", ""))
    signed_at = str(data.get("signed_at", ""))
    pub_hex = str(data.get("public_key_hex", ""))
    sig_hex = str(data.get("signature_hex", ""))

    if not (audit_id and head_hash and signed_at and pub_hex and sig_hex):
        return False

    try:
        public_key = import_public_key_raw(pub_hex)
        signature = bytes.fromhex(sig_hex)
        payload = _canonical_payload(audit_id, head_hash, signed_at)
        public_key.verify(signature, payload)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
