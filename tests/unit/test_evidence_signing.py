"""Unit tests for Ed25519 digital signing of evidence chain heads."""

from __future__ import annotations

import uuid

from engines.evidence.signing import (
    EvidenceSeal,
    export_private_key_raw,
    export_public_key_raw,
    generate_signing_keypair,
    import_private_key_raw,
    import_public_key_raw,
    sign_evidence_head,
    verify_evidence_seal,
)


def test_ed25519_keypair_export_import():
    priv, pub = generate_signing_keypair()
    priv_hex = export_private_key_raw(priv)
    pub_hex = export_public_key_raw(pub)

    assert len(priv_hex) == 64  # 32 bytes in hex
    assert len(pub_hex) == 64

    imported_priv = import_private_key_raw(priv_hex)
    imported_pub = import_public_key_raw(pub_hex)

    assert export_private_key_raw(imported_priv) == priv_hex
    assert export_public_key_raw(imported_pub) == pub_hex


def test_sign_and_verify_evidence_seal():
    priv, _ = generate_signing_keypair()
    audit_id = str(uuid.uuid4())
    head_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    timestamp = "2026-10-21T12:00:00Z"

    seal = sign_evidence_head(audit_id, head_hash, timestamp, priv)
    assert isinstance(seal, EvidenceSeal)
    assert seal.audit_id == audit_id
    assert seal.head_hash == head_hash
    assert seal.algorithm == "Ed25519"

    # Verify with valid seal
    assert verify_evidence_seal(seal) is True
    assert verify_evidence_seal(seal.to_dict()) is True


def test_tampered_seal_rejected():
    priv, _ = generate_signing_keypair()
    audit_id = str(uuid.uuid4())
    head_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    timestamp = "2026-10-21T12:00:00Z"

    seal = sign_evidence_head(audit_id, head_hash, timestamp, priv)
    seal_dict = seal.to_dict()

    # Tamper with head_hash
    tampered_head = dict(seal_dict)
    tampered_head["head_hash"] = "1111110123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    assert verify_evidence_seal(tampered_head) is False

    # Tamper with timestamp
    tampered_time = dict(seal_dict)
    tampered_time["signed_at"] = "2026-10-21T12:00:01Z"
    assert verify_evidence_seal(tampered_time) is False

    # Tamper with signature
    tampered_sig = dict(seal_dict)
    tampered_sig["signature_hex"] = "00" * 64
    assert verify_evidence_seal(tampered_sig) is False
