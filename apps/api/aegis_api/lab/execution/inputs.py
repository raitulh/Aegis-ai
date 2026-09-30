"""Staging job inputs into a single tar that the backend places at ``/workspace`` before the job starts.

Layout inside the sandbox::

    /workspace/input/…          dataset / artifact / inline inputs + params.json   (root-owned, read-only)
    /workspace/code/…           code snapshot files (+ inline source files)        (root-owned, read-only)
    /workspace/output/          the only writable directory (owned by the sandbox user)

Worker-side only. Database reads happen in short ``tenant_uow`` transactions; object bytes are streamed to
local scratch files *outside* any transaction, verified against the checksums recorded at upload time, and
bounded by the job's disk budget. Code snapshots are extracted with the strict safe extractor (no links,
devices or traversal) and checked against the snapshot's file manifest.
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import PermanentError
from aegis_api.lab.execution import integrations
from aegis_api.lab.execution.archive import (
    ChunkReader,
    ExtractLimits,
    TarBuilder,
    copy_to_file,
    safe_extract,
    stream_budget,
)
from aegis_api.lab.models import ArtifactVersion, CodeSnapshot, DatasetVersion
from engines.lab.sandbox import PARAMS_PATH

MAX_CODE_FILES = 5000


class InputStagingError(PermanentError):
    """An input could not be staged (missing, refused, corrupted or over budget)."""


@dataclass(frozen=True)
class StagedInputs:
    path: Path
    size: int
    manifest: list[dict[str, Any]] = field(default_factory=list)


def stage_inputs(
    actor: Actor,
    inputs: list[dict[str, Any]],
    workdir: Path,
    *,
    max_bytes: int,
    uid: int,
    gid: int,
) -> StagedInputs:
    """Build ``workdir/inputs.tar`` from the stored job inputs (see ``service._serialize_inputs``)."""
    workdir.mkdir(parents=True, exist_ok=True)
    scratch = workdir / "scratch"
    scratch.mkdir(exist_ok=True)
    tar_path = workdir / "inputs.tar"
    manifest: list[dict[str, Any]] = []
    with tar_path.open("wb") as fh, TarBuilder(fh, max_total_bytes=max_bytes) as tar:
        tar.add_dir("input", mode=0o555)
        tar.add_dir("code", mode=0o555)
        tar.add_dir("output", mode=0o755, uid=uid, gid=gid)
        for index, item in enumerate(inputs):
            kind = str(item.get("kind"))
            path = str(item.get("path") or "")
            remaining = max(max_bytes - tar.total_bytes, 0)
            if kind == "inline":
                data = _inline_bytes(item)
                tar.add_bytes(path, data)
                manifest.append(_entry(item, size=len(data)))
            elif kind == "parameters":
                data = json.dumps(item.get("content") or {}, sort_keys=True, separators=(",", ":")).encode()
                tar.add_bytes(PARAMS_PATH, data)
                manifest.append({"kind": "parameters", "path": PARAMS_PATH, "size": len(data)})
            elif kind in ("dataset_version", "artifact_version"):
                local = scratch / f"{index:04d}.bin"
                size, sha = _download(actor, item, local, remaining)
                tar.add_file(path, local)
                local.unlink(missing_ok=True)
                manifest.append(_entry(item, size=size, sha256=sha))
            elif kind == "code_snapshot":
                target = scratch / f"code-{index:04d}"
                files, total = _extract_code_snapshot(actor, item, target, remaining)
                tar.add_tree(path, target)
                manifest.append(_entry(item, size=total, files=files))
            else:
                raise InputStagingError(f"Unsupported input kind {kind!r}")
        size = tar.total_bytes
    return StagedInputs(path=tar_path, size=size, manifest=manifest)


def _entry(item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = {k: item.get(k) for k in ("kind", "ref_id", "path", "split") if item.get(k) is not None}
    out.update(extra)
    return out


def _inline_bytes(item: dict[str, Any]) -> bytes:
    content = item.get("content")
    if not isinstance(content, str):
        raise InputStagingError("Inline input has no content")
    if item.get("encoding") == "base64":
        return base64.b64decode(content, validate=True)
    return content.encode("utf-8")


def _download(actor: Actor, item: dict[str, Any], local: Path, max_bytes: int) -> tuple[int, str]:
    kind = str(item["kind"])
    ref_id = uuid.UUID(str(item["ref_id"]))
    split = item.get("split")
    with tenant_uow(actor) as db:
        if kind == "dataset_version":
            version = db.get(DatasetVersion, ref_id)
            if version is None or version.organization_id != actor.organization_id:
                raise InputStagingError(f"Dataset version {ref_id} no longer exists")
            expected: str | None = version.checksum
            if split:
                info = (version.splits or {}).get(split)
                if not isinstance(info, dict):
                    raise InputStagingError(f"Dataset version {ref_id} has no split {split!r}")
                if info.get("visibility") == "evaluator_only":
                    raise InputStagingError("Evaluator-only splits are never mounted into sandboxes")
                expected = info.get("checksum") or None
            stream: Iterator[bytes] = iter(integrations.open_dataset_split(db, actor, ref_id, split))
        else:
            artifact = db.get(ArtifactVersion, ref_id)
            if artifact is None or artifact.organization_id != actor.organization_id:
                raise InputStagingError(f"Artifact version {ref_id} no longer exists")
            if artifact.scan_status == "infected":
                raise InputStagingError(f"Artifact version {ref_id} failed malware scanning")
            expected = artifact.checksum
            stream = iter(integrations.open_artifact_stream(db, actor, ref_id))
        # Pull the first chunk while the transaction is open (authorization + object lookup happen lazily in
        # some streaming implementations); the remaining bytes stream outside the transaction.
        first = next(stream, b"")
    try:
        size, sha = copy_to_file(itertools.chain([first], stream), local, max_bytes)
    except Exception as exc:
        raise InputStagingError(f"Input {item.get('path')} exceeds the job's disk budget or failed: {exc}") from exc
    if expected and sha != expected:
        raise InputStagingError(f"Integrity check failed for input {item.get('path')} (checksum mismatch)")
    return size, sha


def _extract_code_snapshot(actor: Actor, item: dict[str, Any], target: Path, max_bytes: int) -> tuple[int, int]:
    ref_id = uuid.UUID(str(item["ref_id"]))
    with tenant_uow(actor) as db:
        snapshot = db.get(CodeSnapshot, ref_id)
        if snapshot is None or snapshot.organization_id != actor.organization_id:
            raise InputStagingError(f"Code snapshot {ref_id} no longer exists")
        if not snapshot.storage_key:
            raise InputStagingError(f"Code snapshot {ref_id} has no stored files")
        key = snapshot.storage_key
        expected: dict[str, Any] = dict(snapshot.files_manifest or {})
    limits = ExtractLimits(max_total_bytes=max_bytes, max_files=MAX_CODE_FILES)
    chunks = integrations.get_storage().open_stream(key)
    try:
        reader = io.BufferedReader(ChunkReader(chunks, stream_budget(limits)))
        report = safe_extract(reader, target, limits, strict=True)
    except Exception as exc:
        raise InputStagingError(f"Code snapshot {ref_id} could not be extracted safely: {exc}") from exc
    if expected:
        got = {f.path: f.sha256 for f in report.files}
        for path, meta in expected.items():
            sha = meta.get("sha256") if isinstance(meta, dict) else None
            if path not in got or (sha and got[path] != sha):
                raise InputStagingError(f"Code snapshot {ref_id} failed its integrity check at {path!r}")
        extra = set(got) - set(expected)
        if extra:
            raise InputStagingError(f"Code snapshot {ref_id} contains unexpected files: {sorted(extra)[:5]}")
    return len(report.files), report.total_bytes
