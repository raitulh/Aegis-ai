"""Dataset versioning.

A dataset version is an immutable, checksummed set of files, each tagged with a split role
(``train`` | ``validation`` | ``test`` | ``harness_only``). ``harness_only`` files (e.g. hidden labels) are
only ever staged into the platform-owned evaluation harness container — never into candidate code — which is
how measurement integrity and leakage prevention are enforced at the execution layer.

Upload flow: the API writes file bytes to object storage and starts the DatasetProcessingWorkflow, which
profiles the files (schema, row counts, PII/injection flags) and registers the immutable version.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from typing import IO, Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import session_scope
from aegis_api.errors import Conflict, NotFound, PayloadTooLarge, ValidationFailed
from aegis_api.infrastructure.execution.base import ExecutionPolicyError, safe_relative_path
from aegis_api.infrastructure.storage import ObjectTooLarge, get_storage, object_key
from aegis_api.models.lab import Dataset, DatasetVersion
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import usage
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from engines.lab.security.prompt_injection import detect

SPLIT_ROLES = frozenset({"train", "validation", "test", "harness_only"})
PROFILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 64


def create_dataset(
    db: Session,
    principal: Principal,
    *,
    project_id: uuid.UUID | str,
    name: str,
    description: str | None,
    license: str | None,
    source: str | None,
) -> Dataset:
    principal.require("dataset:write")
    project = get_project(db, principal, project_id)
    dataset = Dataset(
        organization_id=principal.organization_id,
        project_id=project.id,
        name=name[:200],
        description=description,
        license=license,
        source=source,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(dataset)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("A dataset with this name already exists in the project") from exc
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.dataset.created",
        resource_type="dataset",
        resource_id=dataset.id,
        principal=principal,
    )
    return dataset


def stage_files(
    principal: Principal, dataset: Dataset, files: list[tuple[str, str, str, IO[bytes]]]
) -> list[dict[str, Any]]:
    """Write uploaded files to object storage. ``files``: (path, role, content_type, stream). No DB access."""
    if not files:
        raise ValidationFailed("At least one file is required")
    if len(files) > MAX_FILES:
        raise ValidationFailed(f"At most {MAX_FILES} files per version")
    settings = get_settings()
    staged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, role, content_type, stream in files:
        try:
            rel = safe_relative_path(path, roots=None)
        except ExecutionPolicyError as exc:
            raise ValidationFailed(str(exc)) from exc
        if role not in SPLIT_ROLES:
            raise ValidationFailed(f"file role must be one of {sorted(SPLIT_ROLES)}")
        if rel in seen:
            raise ValidationFailed(f"duplicate file path '{rel}'")
        seen.add(rel)
        key = object_key(principal.organization_id, "datasets", str(dataset.id), uuid.uuid4().hex)
        try:
            stored = get_storage().put_stream(
                key,
                stream,
                content_type=content_type or "application/octet-stream",
                max_bytes=settings.max_artifact_upload_bytes,
            )
        except ObjectTooLarge as exc:
            raise PayloadTooLarge(f"'{rel}' exceeds {settings.max_artifact_upload_bytes} bytes") from exc
        staged.append(
            {
                "path": rel,
                "role": role,
                "key": key,
                "sha256": stored.sha256,
                "size": stored.size,
                "content_type": content_type or "application/octet-stream",
            }
        )
    return staged


def _profile_csv(data: bytes) -> dict[str, Any]:
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    rows = 0
    numeric = [True] * len(header)
    empty = [0] * len(header)
    for row in reader:
        rows += 1
        for i, value in enumerate(row[: len(header)]):
            if value == "":
                empty[i] += 1
                continue
            if numeric[i]:
                try:
                    float(value)
                except ValueError:
                    numeric[i] = False
    return {
        "format": "csv",
        "columns": [
            {"name": h, "type": "number" if numeric[i] else "string", "empty": empty[i]} for i, h in enumerate(header)
        ],
        "rows_profiled": rows,
        "profile_truncated": len(data) >= PROFILE_BYTES,
    }


def profile_files(organization_id: uuid.UUID, files: list[dict[str, Any]]) -> dict[str, Any]:
    """Activity: read (a bounded prefix of) each file and derive schema + flags. Deterministic."""
    storage = get_storage()
    schema: dict[str, Any] = {}
    flags: list[str] = []
    for f in files:
        chunks = bytearray()
        for chunk in storage.open_stream(f["key"]):
            chunks.extend(chunk)
            if len(chunks) >= PROFILE_BYTES:
                break
        data = bytes(chunks[:PROFILE_BYTES])
        if f["path"].lower().endswith(".csv"):
            prof = _profile_csv(data)
        elif f["path"].lower().endswith((".json", ".jsonl")):
            prof = {"format": "json", "bytes_profiled": len(data)}
            try:
                if f["path"].lower().endswith(".json") and len(data) < PROFILE_BYTES:
                    value = json.loads(data)
                    prof["top_level"] = type(value).__name__
            except ValueError:
                flags.append(f"{f['path']}: invalid JSON")
        else:
            prof = {"format": "binary" if b"\x00" in data[:4096] else "text", "bytes_profiled": len(data)}
        if prof.get("format") in ("csv", "json", "text"):
            injection = detect(data[:200_000].decode("utf-8", errors="replace"))
            if injection.suspicious:
                flags.append(f"{f['path']}: contains instruction-like text (injection score {injection.score:.2f})")
        schema[f["path"]] = {**prof, "role": f["role"], "size": f["size"]}
    return {"schema": schema, "flags": flags}


def version_checksum(files: list[dict[str, Any]]) -> str:
    canonical = json.dumps(sorted((f["path"], f["role"], f["sha256"]) for f in files), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def register_version(
    organization_id: uuid.UUID,
    *,
    dataset_id: uuid.UUID,
    files: list[dict[str, Any]],
    profile: dict[str, Any],
    parent_version_id: uuid.UUID | None,
    transformations: list[dict[str, Any]],
    license: str | None,
    source: str | None,
    created_by_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Activity: create the immutable version row (idempotent on the dataset+checksum pair)."""
    checksum = version_checksum(files)
    with session_scope(organization_id) as db:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None:
            raise NotFound("Dataset not found")
        existing = db.scalar(
            select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id, DatasetVersion.checksum == checksum)
        )
        if existing is not None:
            return {"dataset_version_id": str(existing.id), "version": existing.version, "created": False}
        number = 1 + int(
            db.scalar(
                select(func.coalesce(func.max(DatasetVersion.version), 0)).where(
                    DatasetVersion.dataset_id == dataset_id
                )
            )
            or 0
        )
        total = sum(int(f["size"]) for f in files)
        version = DatasetVersion(
            organization_id=organization_id,
            dataset_id=dataset_id,
            version=number,
            parent_version_id=parent_version_id,
            checksum=checksum,
            size_bytes=total,
            schema_info=profile.get("schema", {}),
            ds_metadata={"flags": profile.get("flags", []), "file_count": len(files)},
            license=license or dataset.license,
            source=source or dataset.source,
            transformations=transformations,
            files=files,
            storage_prefix=object_key(organization_id, "datasets", str(dataset_id)),
            status="ready",
            created_by_id=created_by_id,
        )
        db.add(version)
        db.flush()
        dataset.current_version_id = version.id
        usage.record_storage(
            db,
            organization_id=organization_id,
            object_kind="dataset",
            object_id=version.id,
            size_bytes=total,
            project_id=dataset.project_id,
        )
        return {"dataset_version_id": str(version.id), "version": number, "created": True, "checksum": checksum}


def list_datasets(db: Session, principal: Principal, *, project_id: uuid.UUID | None = None) -> Select[Dataset]:
    stmt = select(Dataset).where(Dataset.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Dataset.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(Dataset.project_id == project_id)
    return stmt.order_by(Dataset.created_at.desc())


def versions(db: Session, dataset: Dataset) -> list[DatasetVersion]:
    return list(
        db.scalars(
            select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version)
        ).all()
    )


def get_version(db: Session, principal: Principal, version_id: uuid.UUID | str) -> DatasetVersion:
    version = get_scoped(db, principal, DatasetVersion, version_id, label="Dataset version")
    get_scoped(db, principal, Dataset, version.dataset_id, label="Dataset")  # project access
    return version


def lineage(db: Session, version: DatasetVersion) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current: DatasetVersion | None = version
    guard = 0
    while current is not None and guard < 100:
        out.append(
            {
                "id": str(current.id),
                "version": current.version,
                "checksum": current.checksum,
                "transformations": current.transformations,
                "created_at": current.created_at.isoformat() if current.created_at else None,
            }
        )
        current = db.get(DatasetVersion, current.parent_version_id) if current.parent_version_id else None
        guard += 1
    return out


def load_files(
    organization_id: uuid.UUID, dataset_version_id: uuid.UUID, *, roles: frozenset[str], max_total_bytes: int
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Read the files of the given split roles for sandbox staging (verifying each file's checksum)."""
    with session_scope(organization_id) as db:
        version = db.get(DatasetVersion, dataset_version_id)
        if version is None or version.organization_id != organization_id:
            raise NotFound("Dataset version not found")
        files = [dict(f) for f in version.files or []]
        info = {"id": str(version.id), "checksum": version.checksum, "version": version.version}
    out: dict[str, bytes] = {}
    total = 0
    storage = get_storage()
    for f in files:
        if f["role"] not in roles:
            continue
        data = storage.get_bytes(f["key"], max_bytes=max_total_bytes)
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise ValidationFailed(f"dataset file '{f['path']}' failed checksum verification")
        total += len(data)
        if total > max_total_bytes:
            raise PayloadTooLarge("dataset exceeds the sandbox input limit")
        out[f["path"]] = data
    return out, info
