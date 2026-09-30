"""Workflow activities for data processing (back ``DatasetProcessingWorkflow``/``ArtifactProcessingWorkflow``).

Both activities follow the activity rules: JSON payloads, idempotent, and no database transaction is held
while bytes stream from object storage or to the malware scanner — each DB touch is a short
``tenant_uow`` before or after the IO.

* ``data.process_dataset_version`` — one full streaming pass over a dataset version: re-verifies size and
  SHA-256 and computes exact row counts and per-column statistics (CSV/TSV/JSONL). Dataset versions are
  immutable, so the profile is stored as an immutable JSON artifact (``retention_class="evidence"``)
  named ``dataset-profile:<version id>``; re-running returns the existing profile.
* ``data.process_artifact_version`` — re-verifies size and SHA-256, sniffs the content type from magic
  bytes and scans the bytes; only ``scan_status`` is updated (the one mutable column).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import structlog
from sqlalchemy import select

from aegis_api.lab.core.access import get_owned, load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import PermanentError
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.data.artifacts import MALWARE_DETECTED, create_artifact_version
from aegis_api.lab.data.inference import profile_dataset
from aegis_api.lab.data.scanning import ScanResult, get_scanner
from aegis_api.lab.data.uploads import looks_executable
from aegis_api.lab.models import Artifact, ArtifactVersion, DatasetVersion
from aegis_api.lab.storage import get_storage
from aegis_api.lab.storage.base import stream_reader
from aegis_api.lab.workflows.registry import ActivityContext, activity

log = structlog.get_logger("aegis.lab.data.activities")

PROFILE_KIND_BY_FORMAT = {"csv": "csv", "tsv": "tsv", "jsonl": "jsonl"}
HEARTBEAT_INTERVAL_SECONDS = 10.0

_MAGIC_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PAR1", "application/vnd.apache.parquet"),
    (b"\x93NUMPY", "application/x-npy"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"\x89HDF\r\n\x1a\n", "application/x-hdf5"),
)


def sniff_content_type(head: bytes) -> str:
    """Best-effort content type from leading bytes (used to flag mismatches, never to serve inline)."""
    if looks_executable(head):
        return "application/x-executable"
    for magic, content_type in _MAGIC_TYPES:
        if head.startswith(magic):
            return content_type
    sample = head[:4096]
    if b"\x00" in sample:
        return "application/octet-stream"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(sample) - 4:  # a multi-byte sequence cut at the sample boundary is still text
            return "application/octet-stream"
    stripped = sample.lstrip()
    if stripped.startswith((b"{", b"[")):
        return "application/json"
    return "text/plain"


class _Verifier:
    """Wraps a chunk stream: hashes, counts, keeps the head, heartbeats and honours cancellation."""

    def __init__(self, chunks: Iterator[bytes], ctx: ActivityContext) -> None:
        self._chunks = chunks
        self._ctx = ctx
        self._digest = hashlib.sha256()
        self._last_beat = time.monotonic()
        self.size = 0
        self.head = b""

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            if self._ctx.is_cancelled():
                raise PermanentError("Activity cancelled")
            self._digest.update(chunk)
            self.size += len(chunk)
            if len(self.head) < 4096:
                self.head += chunk[: 4096 - len(self.head)]
            now = time.monotonic()
            if now - self._last_beat >= HEARTBEAT_INTERVAL_SECONDS:
                self._ctx.heartbeat({"bytes": self.size})
                self._last_beat = now
            yield chunk

    def drain(self) -> None:
        for _chunk in self:
            pass

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _profile_name(version_id: uuid.UUID) -> str:
    return f"dataset-profile:{version_id}"


def _existing_profile(db: Any, project_id: uuid.UUID, name: str) -> Artifact | None:
    return db.scalar(
        select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.name == name,
            Artifact.kind == "json",
            Artifact.deleted_at.is_(None),
        )
    )


@activity("data.process_dataset_version", timeout_seconds=3600, heartbeat_seconds=60)
def process_dataset_version(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Verify and fully profile a dataset version; store the profile as an evidence artifact."""
    version_id = uuid.UUID(str(payload["dataset_version_id"]))
    with tenant_uow(ctx.actor) as db:
        version = get_owned(db, DatasetVersion, version_id, ctx.actor, label="Dataset version")
        load_project(db, ctx.actor, version.project_id, "dataset:read")
        project_id = version.project_id
        key, fmt, size, checksum = version.storage_key, version.format, version.size_bytes, version.checksum
        splits = version.splits or {}
        existing = _existing_profile(db, project_id, _profile_name(version_id))
        if existing is not None and existing.current_version_id is not None:
            return {
                "dataset_version_id": str(version_id),
                "profile_artifact_version_id": str(existing.current_version_id),
                "reused": True,
            }

    storage = get_storage()
    is_manifest = key.endswith("/manifest.json")  # one-file-per-split versions: main object is a manifest
    objects: list[tuple[str, str, int | None, str | None]] = [("main", key, None if is_manifest else size, checksum)]
    for name, spec in sorted(splits.items()):
        objects.append((f"split:{name}", spec["storage_key"], spec.get("size_bytes"), spec.get("checksum")))
    profile_kind = PROFILE_KIND_BY_FORMAT.get(fmt)
    integrity: dict[str, Any] = {}
    profiles: dict[str, Any] = {}
    for label, object_key, expected_size, expected_sha in objects:
        verifier = _Verifier(storage.open_stream(object_key), ctx)
        wants_profile = profile_kind is not None and (label == "main") != is_manifest
        if wants_profile and profile_kind is not None:
            with stream_reader(iter(verifier)) as reader:
                profiles[label] = profile_dataset(
                    profile_kind, reader, progress=lambda rows: ctx.heartbeat({"rows": rows})
                )
        verifier.drain()
        ok = verifier.sha256 == expected_sha and (expected_size is None or verifier.size == expected_size)
        integrity[label] = {"sha256_verified": ok, "size": verifier.size}
        if not ok:
            with tenant_uow(ctx.actor) as db:
                append_evidence(
                    db,
                    organization_id=ctx.organization_id,
                    kind="dataset_integrity",
                    title=f"Integrity check failed for dataset version {version_id}",
                    content={
                        "dataset_version_id": str(version_id),
                        "object": label,
                        "observed_sha256": verifier.sha256,
                        "observed_size": verifier.size,
                    },
                )
            raise PermanentError(f"Integrity check failed for dataset version {version_id} ({label})")
    profile: dict[str, Any] | None = profiles.get("main")
    if profile is None and profiles:
        rows = [p.get("row_count") for p in profiles.values()]
        profile = {"format": fmt, "row_count": sum(r for r in rows if isinstance(r, int)), "splits": profiles}

    document = {
        "dataset_version_id": str(version_id),
        "format": fmt,
        "integrity": integrity,
        "profile": profile,
        "generated_by": "data.process_dataset_version",
    }
    body = json.dumps(document, sort_keys=True, default=str).encode("utf-8")
    system = Actor.system(ctx.organization_id, label="system:data-profiler")
    with tenant_uow(system) as db:
        advisory_xact_lock(db, f"dataset-profile:{version_id}")
        existing = _existing_profile(db, project_id, _profile_name(version_id))
        if existing is not None and existing.current_version_id is not None:
            return {
                "dataset_version_id": str(version_id),
                "profile_artifact_version_id": str(existing.current_version_id),
                "reused": True,
            }
        created = create_artifact_version(
            db,
            system,
            project_id=project_id,
            kind="json",
            name=_profile_name(version_id),
            data=body,
            mime_type="application/json",
            filename="profile.json",
            retention_class="evidence",
            metadata={"dataset_version_id": str(version_id), "type": "dataset_profile"},
        )
        profile_version_id = str(created.id)
    return {
        "dataset_version_id": str(version_id),
        "profile_artifact_version_id": profile_version_id,
        "row_count": profile.get("row_count") if profile else None,
        "integrity": integrity,
        "reused": False,
    }


@activity("data.process_artifact_version", timeout_seconds=3600, heartbeat_seconds=60)
def process_artifact_version(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Re-verify size/SHA-256, sniff the content type and scan; updates ``scan_status`` only."""
    version_id = uuid.UUID(str(payload["artifact_version_id"]))
    force = bool(payload.get("force", False))
    with tenant_uow(ctx.actor) as db:
        version = get_owned(db, ArtifactVersion, version_id, ctx.actor, label="Artifact version")
        load_project(db, ctx.actor, version.project_id, "artifact:read")
        key, size, checksum = version.storage_key, version.size_bytes, version.checksum
        declared_type, status = version.mime_type, version.scan_status
        project_id = version.project_id
    if status in ("clean", "infected") and not force:
        return {"artifact_version_id": str(version_id), "scan_status": status, "skipped": True}

    scanner = get_scanner()
    verifier = _Verifier(get_storage().open_stream(key), ctx)
    if scanner.enabled:
        result = scanner.scan_stream(verifier)
        verifier.drain()  # clamd may stop reading early (e.g. on a match); finish the integrity pass
    else:
        verifier.drain()
        result = ScanResult(status="not_scanned", scanner=scanner.name)
    sha_ok = verifier.sha256 == checksum
    size_ok = verifier.size == size
    sniffed = sniff_content_type(verifier.head)
    new_status = result.status if (sha_ok and size_ok) else "error"

    with tenant_uow(ctx.actor) as db:
        version = get_owned(db, ArtifactVersion, version_id, ctx.actor, label="Artifact version")
        if version.scan_status != new_status:
            version.scan_status = new_status
        if not (sha_ok and size_ok):
            append_evidence(
                db,
                organization_id=ctx.organization_id,
                kind="artifact_integrity",
                title=f"Integrity check failed for artifact version {version_id}",
                content={
                    "artifact_version_id": str(version_id),
                    "expected_sha256": checksum,
                    "observed_sha256": verifier.sha256,
                    "expected_size": size,
                    "observed_size": verifier.size,
                },
            )
        if result.infected:
            audit(
                db,
                ctx.actor,
                MALWARE_DETECTED,
                "artifact_version",
                version_id,
                after={"project_id": str(project_id), "scanner": result.scanner, "signature": result.signature},
            )
    return {
        "artifact_version_id": str(version_id),
        "scan_status": new_status,
        "scanner": result.scanner,
        "signature": result.signature,
        "sha256_verified": sha_ok,
        "size_verified": size_ok,
        "declared_content_type": declared_type,
        "sniffed_content_type": sniffed,
        "content_type_mismatch": _mismatch(declared_type, sniffed),
        "skipped": False,
    }


def _mismatch(declared: str, sniffed: str) -> bool:
    generic = {"application/octet-stream", "text/plain"}
    if sniffed in generic or declared in generic:
        return sniffed == "application/x-executable"
    if sniffed == "application/zip" and declared in ("application/x-npz", "application/zip"):
        return False
    return declared != sniffed
