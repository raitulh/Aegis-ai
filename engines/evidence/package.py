#!/usr/bin/env python3
"""Aegis evidence package verifier (``aegis.evidence-package.v1``).

This module is self-contained on purpose: it depends only on the Python standard library (plus the optional
``cryptography`` package for signature checks), so the exact same file is shipped inside every exported
evidence package as ``verify.py``. Anyone can verify a package independently of Aegis::

    python verify.py aegis-evidence-<audit>.zip [--public-key <base64>]

Checks performed:

1. **File integrity** — every file listed in ``manifest.json`` is present and its SHA-256 matches.
2. **Content hashes** — each artifact's ``content_hash`` is recomputed from its canonical JSON content
   (skipped, and reported, for artifacts purged under a retention policy).
3. **Hash chain** — ``chain_hash = sha256(prev_chain_hash | "GENESIS" + ":" + content_hash)`` links every artifact
   to its predecessor; the final link must equal the recorded chain head.
4. **Root hash** — recomputed over the chain head and the file digests.
5. **Signature** — the canonical manifest bytes are verified against the Ed25519 signature in
   ``manifest.sig.json`` (and, if provided, against an independently obtained public key).

Statuses: ``VERIFIED`` (all checks pass), ``TAMPERED`` (a hash, link or signature does not match),
``INCOMPLETE`` (a listed file is missing), ``UNSIGNED`` (integrity holds but no verifiable signature).
These results are cryptographic integrity checks; they make no legal or regulatory claim.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Any

FORMAT = "aegis.evidence-package.v1"


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(payload: Any) -> str:
    return sha256_hex(canonical(payload))


def chain_hash(prev_hash: str | None, this_content_hash: str) -> str:
    return hashlib.sha256(f"{prev_hash or 'GENESIS'}:{this_content_hash}".encode()).hexdigest()


def artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    """The exact payload whose canonical JSON is the artifact's content hash."""
    return {
        "kind": artifact["kind"],
        "title": artifact["title"],
        "content": artifact["content"],
        "seq": artifact["seq"],
    }


def root_hash(chain_head: str | None, files: dict[str, str]) -> str:
    return sha256_hex(canonical({"chain_head": chain_head, "files": dict(sorted(files.items()))}))


@dataclass
class ChainReport:
    status: str = "VERIFIED"
    records: int = 0
    content_checked: int = 0
    purged: int = 0
    head: str | None = None
    problems: list[dict[str, Any]] = field(default_factory=list)

    def fail(self, index: int, check: str, detail: str) -> None:
        self.status = "TAMPERED"
        self.problems.append({"index": index, "check": check, "detail": detail})

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": self.records,
            "content_checked": self.content_checked,
            "purged": self.purged,
            "head": self.head,
            "problems": self.problems,
        }


def verify_artifacts(artifacts: list[dict[str, Any]], expected_head: str | None = None) -> ChainReport:
    """Verify ordered artifacts ({kind,title,content,seq,content_hash,prev_hash,chain_hash,purged?})."""
    report = ChainReport(records=len(artifacts))
    prev: str | None = None
    for i, art in enumerate(artifacts):
        if art.get("purged"):
            report.purged += 1
        else:
            recomputed = content_hash(artifact_payload(art))
            report.content_checked += 1
            if recomputed != art.get("content_hash"):
                report.fail(i, "content_hash", f"artifact seq {art.get('seq')} content does not match its hash")
        if art.get("prev_hash") != prev:
            report.fail(i, "prev_hash", f"artifact seq {art.get('seq')} does not link to its predecessor")
        expected = chain_hash(prev, art.get("content_hash") or "")
        if expected != art.get("chain_hash"):
            report.fail(i, "chain_hash", f"artifact seq {art.get('seq')} chain hash mismatch")
        prev = art.get("chain_hash")
    report.head = prev
    if expected_head is not None and prev != expected_head:
        report.fail(len(artifacts), "head", "chain head does not match the recorded head")
    return report


def verify_signature(message: bytes, signature_b64: str, public_key_b64: str) -> bool | None:
    """True/False for a checked Ed25519 signature; None when ``cryptography`` is unavailable."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # pragma: no cover - optional dependency for standalone use
        return None
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(public_key_b64))
        key.verify(base64.urlsafe_b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def verify_package(data: bytes, trusted_public_key: str | None = None) -> dict[str, Any]:
    """Verify a zipped evidence package. Never raises for malformed input; reports it instead."""
    result: dict[str, Any] = {"format": FORMAT, "status": "VERIFIED", "checks": [], "problems": []}

    def problem(status: str, check: str, detail: str) -> None:
        severity = {"TAMPERED": 3, "INCOMPLETE": 2, "UNSIGNED": 1, "VERIFIED": 0}
        if severity[status] > severity[result["status"]]:
            result["status"] = status
        result["problems"].append({"check": check, "detail": detail})

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        problem("INCOMPLETE", "archive", "not a valid zip archive")
        return result
    names = {n.split("/", 1)[1] if "/" in n else n: n for n in archive.namelist()}
    if "manifest.json" not in names:
        problem("INCOMPLETE", "manifest", "manifest.json is missing")
        return result
    manifest_bytes = archive.read(names["manifest.json"])
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError:
        problem("TAMPERED", "manifest", "manifest.json is not valid JSON")
        return result
    if manifest.get("format") != FORMAT:
        problem("INCOMPLETE", "format", f"unsupported package format {manifest.get('format')!r}")
    result["audit"] = manifest.get("audit")
    result["generated_at"] = manifest.get("generated_at")

    # 1. files
    files: dict[str, str] = manifest.get("files", {})
    for path, expected in sorted(files.items()):
        if path not in names:
            problem("INCOMPLETE", "file", f"{path} is missing")
            continue
        if sha256_hex(archive.read(names[path])) != expected:
            problem("TAMPERED", "file", f"{path} does not match its recorded SHA-256")
    result["checks"].append({"check": "files", "count": len(files)})

    # 2-3. artifacts and chain
    artifacts: list[dict[str, Any]] = []
    for entry in manifest.get("artifacts", []):
        path = entry.get("file")
        if path not in names:
            continue
        try:
            artifacts.append(json.loads(archive.read(names[path])))
        except ValueError:
            problem("TAMPERED", "artifact", f"{path} is not valid JSON")
    artifacts.sort(key=lambda a: a.get("seq", 0))
    chain = verify_artifacts(artifacts, manifest.get("chain_head"))
    if len(artifacts) != len(manifest.get("artifacts", [])):
        problem("INCOMPLETE", "artifacts", "some listed artifacts could not be read")
    for p in chain.problems:
        problem("TAMPERED", p["check"], p["detail"])
    result["chain"] = chain.as_dict()
    result["checks"].append({"check": "chain", "records": chain.records, "purged": chain.purged})

    # 4. root hash
    recomputed_root = root_hash(manifest.get("chain_head"), files)
    if recomputed_root != manifest.get("root_hash"):
        problem("TAMPERED", "root_hash", "root hash does not match the package contents")
    result["root_hash"] = manifest.get("root_hash")

    # 5. signature
    if "manifest.sig.json" not in names:
        problem("UNSIGNED", "signature", "package is not signed")
    else:
        try:
            sig = json.loads(archive.read(names["manifest.sig.json"]))
        except ValueError:
            sig = {}
        public_key = trusted_public_key or sig.get("public_key", "")
        verified = verify_signature(manifest_bytes, sig.get("signature", ""), public_key) if public_key else False
        result["signature"] = {
            "algorithm": sig.get("algorithm"),
            "key_id": sig.get("key_id"),
            "trusted_key_supplied": bool(trusted_public_key),
            "valid": verified,
        }
        if verified is None:
            problem("UNSIGNED", "signature", "install the 'cryptography' package to check the signature")
        elif not verified:
            problem("TAMPERED", "signature", "signature does not match the manifest")
        if trusted_public_key and sig.get("public_key") and sig.get("public_key") != trusted_public_key:
            problem("TAMPERED", "signature", "package was signed by a different key than the trusted key")
    return result


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wrapper
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python verify.py <package.zip> [--public-key <base64>]")  # noqa: T201 - CLI output
        return 2
    key = None
    if "--public-key" in args:
        i = args.index("--public-key")
        key = args[i + 1]
        del args[i : i + 2]
    from pathlib import Path

    report = verify_package(Path(args[0]).read_bytes(), key)
    print(json.dumps(report, indent=2))  # noqa: T201 - CLI output
    return 0 if report["status"] == "VERIFIED" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
