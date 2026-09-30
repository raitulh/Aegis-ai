"""Turning collected sandbox outputs into immutable artifacts, and parsing self-reported metrics.

Output files were already extracted by the safe extractor (regular files only, size/count budgets enforced).
Each file is written to object storage *outside* any database transaction, its checksum is verified against
the hash computed during extraction, and then an immutable artifact version is recorded in a short
transaction (retention class ``evidence`` — outputs back reproducibility claims).

``/workspace/output/metrics.json`` is parsed into a bounded list of finite numeric metrics. These values are
produced by the experiment code itself and are labelled ``self_reported``: they are useful signals but never
sufficient evidence on their own (platform evaluators re-measure).
"""

from __future__ import annotations

import json
import math
import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import structlog

from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import PermanentError
from aegis_api.lab.execution import integrations
from aegis_api.lab.execution.archive import ExtractedFile, ExtractionReport
from engines.lab.sandbox import METRICS_FILE

log = structlog.get_logger("aegis.lab.execution.outputs")

MAX_METRICS = 1000
MAX_METRICS_FILE_BYTES = 1024 * 1024
MAX_METRIC_ERRORS = 20
_METRIC_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:/\-]{0,119}$")

KIND_BY_EXTENSION: dict[str, str] = {
    ".log": "log",
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".png": "plot",
    ".jpg": "plot",
    ".jpeg": "plot",
    ".svg": "plot",
    ".gif": "plot",
    ".ipynb": "notebook",
    ".py": "code",
    ".r": "code",
    ".jl": "code",
    ".sh": "code",
    ".ckpt": "checkpoint",
    ".pt": "checkpoint",
    ".pth": "checkpoint",
    ".safetensors": "checkpoint",
    ".pkl": "model",
    ".joblib": "model",
    ".onnx": "model",
    ".h5": "model",
    ".md": "report",
    ".html": "report",
    ".pdf": "report",
    ".parquet": "dataset_file",
    ".npz": "dataset_file",
    ".npy": "dataset_file",
    ".arrow": "dataset_file",
    ".feather": "dataset_file",
}
_TEXT_TYPES = {
    ".log": "text/plain",
    ".jsonl": "application/x-ndjson",
    ".md": "text/markdown",
    ".ipynb": "application/json",
}


def artifact_kind_for(path: str) -> str:
    return KIND_BY_EXTENSION.get(PurePosixPath(path).suffix.lower(), "output")


def mime_type_for(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _TEXT_TYPES:
        return _TEXT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(PurePosixPath(path).name, strict=False)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------------------------
# metrics.json
# ---------------------------------------------------------------------------------------------
def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_metrics(data: bytes, *, max_metrics: int = MAX_METRICS) -> dict[str, Any]:
    """Parse ``metrics.json``: either ``{"name": number, …}`` or ``[{"name", "value", "step"?}, …]``.

    Returns ``{"source": "self_reported", "items": [...], "values": {name: last value}, "errors": [...]}``.
    Non-finite values, booleans, bad names and entries beyond ``max_metrics`` are rejected (reported in
    ``errors``), never silently coerced.
    """
    errors: list[str] = []
    items: list[dict[str, Any]] = []

    def reject(message: str) -> None:
        if len(errors) < MAX_METRIC_ERRORS:
            errors.append(message)

    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {"source": "self_reported", "items": [], "values": {}, "errors": [f"invalid JSON: {exc}"[:200]]}

    candidates: list[tuple[Any, Any, Any]] = []
    if isinstance(doc, dict):
        candidates = [(name, value, None) for name, value in doc.items()]
    elif isinstance(doc, list):
        for i, entry in enumerate(doc):
            if not isinstance(entry, dict):
                reject(f"entry {i}: expected an object with name and value")
                continue
            candidates.append((entry.get("name"), entry.get("value"), entry.get("step")))
    else:
        reject("metrics.json must be an object or a list")

    for name, value, step in candidates:
        if len(items) >= max_metrics:
            reject(f"more than {max_metrics} metrics; the rest were ignored")
            break
        if not isinstance(name, str) or not _METRIC_NAME_RE.match(name):
            reject(f"invalid metric name {str(name)[:60]!r}")
            continue
        number = _finite_number(value)
        if number is None:
            reject(f"metric {name!r}: value must be a finite number")
            continue
        if step is not None and (isinstance(step, bool) or not isinstance(step, int) or step < 0):
            reject(f"metric {name!r}: step must be a non-negative integer")
            continue
        items.append({"name": name, "value": number, "step": step})
    values: dict[str, float] = {}
    for item in items:
        values[item["name"]] = item["value"]
    return {"source": "self_reported", "items": items, "values": values, "errors": errors}


def read_metrics(report: ExtractionReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    entry = report.by_path().get(METRICS_FILE)
    if entry is None:
        return None
    if entry.size > MAX_METRICS_FILE_BYTES:
        return {
            "source": "self_reported",
            "items": [],
            "values": {},
            "errors": [f"metrics.json exceeds {MAX_METRICS_FILE_BYTES} bytes"],
        }
    return parse_metrics(entry.local_path.read_bytes())


# ---------------------------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class JobRef:
    organization_id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID
    mission_id: uuid.UUID | None = None
    experiment_run_id: uuid.UUID | None = None


def _store_file(
    actor: Actor,
    ref: JobRef,
    *,
    key: str,
    local: Any,
    size: int,
    sha256: str,
    name: str,
    filename: str,
    kind: str,
    mime_type: str,
    retention_class: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    storage = integrations.get_storage()
    with local.open("rb") as fh:
        stored = storage.put_stream(key, fh, mime_type, max_bytes=size)
    if stored.sha256 != sha256 or stored.size != size:
        raise PermanentError(f"Stored object for {name!r} does not match the collected file (checksum mismatch)")
    with tenant_uow(actor) as db:
        version = integrations.create_artifact_version(
            db,
            actor,
            project_id=ref.project_id,
            kind=kind,
            name=name[:300],
            stored=stored,
            mime_type=mime_type,
            filename=filename[:300],
            mission_id=ref.mission_id,
            experiment_run_id=ref.experiment_run_id,
            compute_job_id=ref.job_id,
            retention_class=retention_class,
            metadata=metadata,
        )
        info = {
            "artifact_id": str(version.artifact_id),
            "artifact_version_id": str(version.id),
            "sha256": sha256,
            "size": size,
            "mime_type": mime_type,
            "kind": kind,
        }
    return info


def persist_outputs(actor: Actor, ref: JobRef, files: list[ExtractedFile]) -> dict[str, dict[str, Any]]:
    """Store every collected output file as an immutable artifact version → ``{path: info}``."""
    out: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(sorted(files, key=lambda f: f.path)):
        basename = PurePosixPath(item.path).name
        key = integrations.object_key(
            ref.organization_id, ref.project_id, "compute-jobs", str(ref.job_id), "outputs", f"{index:04d}-{basename}"
        )
        mime = mime_type_for(item.path)
        out[item.path] = _store_file(
            actor,
            ref,
            key=key,
            local=item.local_path,
            size=item.size,
            sha256=item.sha256,
            name=item.path,
            filename=basename,
            kind=artifact_kind_for(item.path),
            mime_type=mime,
            retention_class="evidence",
            metadata={
                "compute_job_id": str(ref.job_id),
                "output_path": item.path,
                "sha256": item.sha256,
                "self_reported": item.path == METRICS_FILE,
            },
        )
    return out


def persist_log(actor: Actor, ref: JobRef, local: Any, *, size: int, sha256: str, truncated: bool) -> dict[str, Any]:
    key = integrations.object_key(ref.organization_id, ref.project_id, "compute-jobs", str(ref.job_id), "job.log")
    info = _store_file(
        actor,
        ref,
        key=key,
        local=local,
        size=size,
        sha256=sha256,
        name=f"compute-job-{ref.job_id}.log",
        filename=f"compute-job-{ref.job_id}.log",
        kind="log",
        mime_type="text/plain",
        retention_class="standard",
        metadata={"compute_job_id": str(ref.job_id), "truncated": truncated},
    )
    info["truncated"] = truncated
    return info
