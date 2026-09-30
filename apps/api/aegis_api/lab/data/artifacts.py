"""Artifacts with immutable versions (logs, models, checkpoints, plots, notebooks, reports, outputs…).

An ``Artifact`` is the logical, mutable container (name, links, retention, soft delete); each
``ArtifactVersion`` is an immutable reference to bytes in object storage with their size and SHA-256
(only ``scan_status`` may change afterwards — enforced by a database trigger).

Versions are numbered ``max+1`` under a transaction-scoped advisory lock per artifact. Objects are stored
under ``object_key(org, project, "artifacts", artifact_id, "v<n>-<version id prefix>", filename)``; the
version-row suffix makes every key unique so an object written by a transaction that later rolls back
can be deleted safely (see :mod:`aegis_api.lab.storage.tx`).

Permissions: ``artifact:upload`` to create versions or delete, ``artifact:read`` for metadata,
``artifact:download`` for bytes. Every id is loaded through the tenant session and re-checked against
the project, so foreign ids surface as 404.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, PayloadTooLarge, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate, paginate_keyset
from aegis_api.lab.data.flows import launch_artifact_processing
from aegis_api.lab.data.scanning import MalwareDetected, ScanResult, enforce_scan, get_scanner, scan_bytes, scan_file
from aegis_api.lab.models import Artifact, ArtifactVersion, ComputeJob, ExperimentRun, Mission, Project
from aegis_api.lab.storage import get_storage
from aegis_api.lab.storage.base import StoredObject
from aegis_api.lab.storage.keys import object_key, validate_key
from aegis_api.lab.storage.signing import safe_content_type
from aegis_api.lab.storage.tx import delete_on_rollback
from aegis_api.lab.usage.recorder import record_storage_usage
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.uploads import sanitize_filename

log = structlog.get_logger("aegis.lab.artifacts")

ARTIFACT_KINDS: tuple[str, ...] = (
    "log",
    "model",
    "checkpoint",
    "csv",
    "json",
    "plot",
    "notebook",
    "code",
    "report",
    "environment_manifest",
    "execution_snapshot",
    "dataset_file",
    "upload",
    "output",
    "other",
)
RETENTION_CLASSES: tuple[str, ...] = ("evidence", "standard", "ephemeral")
MAX_METADATA_BYTES = 64 * 1024
MAX_NAME_LENGTH = 300

_EXTENSION_KINDS: dict[str, str] = {
    ".log": "log",
    ".out": "log",
    ".err": "log",
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".png": "plot",
    ".jpg": "plot",
    ".jpeg": "plot",
    ".svg": "plot",
    ".ipynb": "notebook",
    ".py": "code",
    ".r": "code",
    ".jl": "code",
    ".ts": "code",
    ".js": "code",
    ".pdf": "report",
    ".md": "report",
    ".html": "report",
    ".pt": "model",
    ".pth": "model",
    ".onnx": "model",
    ".safetensors": "model",
    ".joblib": "model",
    ".h5": "model",
    ".ckpt": "checkpoint",
    ".parquet": "dataset_file",
    ".npy": "dataset_file",
    ".npz": "dataset_file",
}

ARTIFACT_DELETED = "ARTIFACT_DELETED"
MALWARE_DETECTED = "MALWARE_DETECTED"


class ArtifactIntegrityError(ServiceUnavailable):
    """Stored bytes do not match the recorded checksum."""

    code = "integrity_check_failed"


class ArtifactQuarantined(Forbidden):
    """The artifact was flagged by the malware scanner and cannot be downloaded."""

    code = "artifact_quarantined"


@dataclass(frozen=True)
class DownloadTarget:
    """What to hand to a client: an authorized object with its presentation metadata."""

    key: str
    filename: str
    content_type: str
    size_bytes: int
    checksum: str


def infer_artifact_kind(filename: str | None, mime_type: str | None = None) -> str:
    name = (filename or "").lower()
    for ext, kind in _EXTENSION_KINDS.items():
        if name.endswith(ext):
            return kind
    if mime_type and mime_type.startswith("image/"):
        return "plot"
    return "upload" if filename else "other"


def guess_content_type(filename: str | None) -> str:
    guessed, _encoding = mimetypes.guess_type(filename or "", strict=False)
    return safe_content_type(guessed)


def _validate_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValidationFailed("metadata must be a JSON object")
    try:
        encoded = json.dumps(metadata, default=str)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("metadata must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValidationFailed(f"metadata exceeds {MAX_METADATA_BYTES} bytes")
    loaded: dict[str, Any] = json.loads(encoded)
    return loaded


def _linked[T](db: Session, actor: Actor, model: type[T], object_id: Any, project: Project, label: str) -> T | None:
    if object_id is None:
        return None
    row = get_owned(db, model, object_id, actor, label=label)
    if getattr(row, "project_id", None) != project.id:
        raise ValidationFailed(f"{label} belongs to a different project")
    return row


def _is_seekable(fileobj: Any) -> bool:
    try:
        return bool(fileobj.seekable())
    except (AttributeError, ValueError, OSError):
        return False


def record_rejected_upload(actor: Actor, *, project_id: uuid.UUID, filename: str, result: ScanResult) -> None:
    """Audit a malware rejection in its own short transaction (the request transaction rolls back)."""
    try:
        with tenant_uow(actor) as session:
            audit(
                session,
                actor,
                MALWARE_DETECTED,
                "upload",
                None,
                after={
                    "project_id": str(project_id),
                    "filename": filename,
                    "scanner": result.scanner,
                    "signature": result.signature,
                },
            )
    except Exception:
        log.error("malware_audit_failed", exc_info=True)


def _scan_before_store(
    actor: Actor,
    project: Project,
    filename: str,
    *,
    data: bytes | None,
    fileobj: BinaryIO | None,
    scan_result: ScanResult | None,
    fail_closed: bool,
) -> ScanResult:
    """Scan in-memory data and seekable files now; other sources are left for the processing activity."""
    scanner = get_scanner()
    if scan_result is not None:
        result = scan_result
    elif not scanner.enabled:
        result = ScanResult(status="not_scanned", scanner=scanner.name)
    elif data is not None:
        result = scan_bytes(data, scanner)
    elif fileobj is not None and _is_seekable(fileobj):
        result = scan_file(fileobj, scanner)
    else:
        result = ScanResult(status="not_scanned", scanner=scanner.name, detail="deferred")
    try:
        enforce_scan(result, fail_closed=fail_closed)
    except MalwareDetected:
        record_rejected_upload(actor, project_id=project.id, filename=filename, result=result)
        raise
    return result


def create_artifact_version(
    db: Session,
    actor: Actor,
    *,
    project_id: uuid.UUID | str,
    kind: str | None = None,
    name: str | None = None,
    data: bytes | None = None,
    fileobj: BinaryIO | None = None,
    stored: StoredObject | None = None,
    mime_type: str | None = None,
    filename: str | None = None,
    artifact_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    experiment_run_id: uuid.UUID | str | None = None,
    compute_job_id: uuid.UUID | str | None = None,
    retention_class: str = "standard",
    metadata: dict[str, Any] | None = None,
    description: str | None = None,
    scan_result: ScanResult | None = None,
    fail_closed_scan: bool = False,
) -> ArtifactVersion:
    """Create a new artifact (or a new version of ``artifact_id``) from exactly one byte source.

    * ``data`` — bytes held by the caller; ``fileobj`` — streamed to storage (never fully read into memory);
    * ``stored`` — an object the platform already wrote under this organization/project prefix.

    User uploads pass a ``scan_result`` from the router (fail-closed); internal callers get a best-effort
    scan (``scan_status="error"`` when the scanner is down, re-scanned asynchronously).
    """
    if sum(source is not None for source in (data, fileobj, stored)) != 1:
        raise ValidationFailed("Provide exactly one of data, fileobj or stored")
    settings = get_settings()
    project = load_project(db, actor, project_id, "artifact:upload")
    metadata_clean = _validate_metadata(metadata)
    if retention_class not in RETENTION_CLASSES:
        raise ValidationFailed(f"retention_class must be one of {', '.join(RETENTION_CLASSES)}")

    artifact: Artifact | None = None
    if artifact_id is not None:
        artifact = get_owned(db, Artifact, artifact_id, actor, label="Artifact")
        if artifact.project_id != project.id or artifact.deleted_at is not None:
            raise NotFound("Artifact not found")
        if kind is not None and kind != artifact.kind:
            raise ValidationFailed(f"Artifact kind is '{artifact.kind}'; a version cannot change it")
    else:
        display_name = (name or filename or "").strip()
        if not display_name or len(display_name) > MAX_NAME_LENGTH:
            raise ValidationFailed(f"name is required (1-{MAX_NAME_LENGTH} characters)")
        _linked(db, actor, Mission, mission_id, project, "Mission")
        _linked(db, actor, ExperimentRun, experiment_run_id, project, "Experiment run")
        _linked(db, actor, ComputeJob, compute_job_id, project, "Compute job")

    safe_name = sanitize_filename(filename or name or (artifact.name if artifact else "artifact"))
    resolved_kind = artifact.kind if artifact else (kind or infer_artifact_kind(safe_name, mime_type))
    if resolved_kind not in ARTIFACT_KINDS:
        raise ValidationFailed(f"kind must be one of {', '.join(ARTIFACT_KINDS)}")
    content_type = safe_content_type(mime_type) if mime_type else guess_content_type(safe_name)
    if data is not None and len(data) > settings.max_artifact_bytes:
        raise PayloadTooLarge(f"Artifact exceeds the {settings.max_artifact_bytes} byte limit")

    scan = _scan_before_store(
        actor,
        project,
        safe_name,
        data=data,
        fileobj=fileobj,
        scan_result=scan_result,
        fail_closed=fail_closed_scan,
    )

    if artifact is None:
        artifact = Artifact(
            id=uuid.uuid4(),
            organization_id=actor.organization_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            mission_id=uuid.UUID(str(mission_id)) if mission_id else None,
            experiment_run_id=uuid.UUID(str(experiment_run_id)) if experiment_run_id else None,
            compute_job_id=uuid.UUID(str(compute_job_id)) if compute_job_id else None,
            kind=resolved_kind,
            name=(name or filename or safe_name).strip()[:MAX_NAME_LENGTH],
            description=description,
            retention_class=retention_class,
            created_by_id=actor.user_id,
            created_by_agent_run_id=actor.agent_run_id,
        )
        db.add(artifact)
        db.flush()

    advisory_xact_lock(db, f"artifact-version:{artifact.id}")
    current_max = db.scalar(select(func.max(ArtifactVersion.version)).where(ArtifactVersion.artifact_id == artifact.id))
    number = int(current_max or 0) + 1
    version_id = uuid.uuid4()
    storage = get_storage()

    if stored is not None:
        key = validate_key(stored.key, organization_id=actor.organization_id, project_id=project.id)
        info = storage.stat(key)
        if info.size != stored.size:
            raise ValidationFailed("Stored object size does not match the declared size")
        written = stored
        if not mime_type and stored.content_type:
            content_type = safe_content_type(stored.content_type)
    else:
        key = object_key(
            actor.organization_id, project.id, "artifacts", artifact.id, f"v{number}-{version_id.hex[:12]}", safe_name
        )
        if data is not None:
            written = storage.put_bytes(key, data, content_type)
        else:
            assert fileobj is not None
            written = storage.put_stream(key, fileobj, content_type, max_bytes=settings.max_artifact_bytes)
        delete_on_rollback(db, key, storage)

    if scan.status != "not_scanned" or scan.detail:
        metadata_clean.setdefault("scan", {"scanner": scan.scanner, "status": scan.status, "at": utcnow().isoformat()})
    version = ArtifactVersion(
        id=version_id,
        organization_id=actor.organization_id,
        artifact_id=artifact.id,
        project_id=project.id,
        version=number,
        storage_key=key,
        size_bytes=written.size,
        checksum=written.sha256,
        mime_type=content_type,
        original_filename=safe_name,
        scan_status=scan.status,
        artifact_metadata=metadata_clean,
        created_by_id=actor.user_id,
    )
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    artifact.updated_at = utcnow()
    record_storage_usage(
        db,
        organization_id=actor.organization_id,
        bytes_delta=written.size,
        reason="execution_output" if artifact.compute_job_id else "artifact_upload",
        project_id=project.id,
        artifact_version_id=version.id,
    )
    if artifact.retention_class == "evidence":
        append_evidence(
            db,
            organization_id=actor.organization_id,
            kind="artifact_version",
            title=f"Artifact {artifact.name[:200]} v{number}",
            content={
                "artifact_id": str(artifact.id),
                "artifact_version_id": str(version.id),
                "version": number,
                "kind": artifact.kind,
                "sha256": written.sha256,
                "size_bytes": written.size,
                "filename": safe_name,
            },
            storage_key=key,
        )
    audit(
        db,
        actor,
        AuditAction.FILE_UPLOADED,
        "artifact_version",
        version.id,
        after={
            "artifact_id": str(artifact.id),
            "version": number,
            "kind": artifact.kind,
            "size_bytes": written.size,
            "sha256": written.sha256,
            "filename": safe_name,
            "scan_status": scan.status,
        },
    )
    if scan.status == "error" or scan.detail == "deferred":
        # The scan could not run inline (scanner down, or a non-seekable stream): finish it asynchronously.
        launch_artifact_processing(db, actor, version)
    db.flush()
    return version


# -- reads -----------------------------------------------------------------------------------------
def get_artifact(
    db: Session, actor: Actor, artifact_id: uuid.UUID | str, *, permission: str = "artifact:read"
) -> Artifact:
    artifact = get_owned(db, Artifact, artifact_id, actor, label="Artifact")
    load_project(db, actor, artifact.project_id, permission)
    if artifact.deleted_at is not None:
        raise NotFound("Artifact not found")
    return artifact


def get_artifact_version(
    db: Session, actor: Actor, version_id: uuid.UUID | str, *, permission: str = "artifact:read"
) -> ArtifactVersion:
    version = get_owned(db, ArtifactVersion, version_id, actor, label="Artifact version")
    load_project(db, actor, version.project_id, permission)
    artifact = db.get(Artifact, version.artifact_id)
    if artifact is None or artifact.deleted_at is not None:
        raise NotFound("Artifact version not found")
    return version


def list_artifacts(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_run_id: uuid.UUID | None = None,
    kind: str | None = None,
    mapper: Any = None,
) -> CursorPage[Any]:
    stmt = select(Artifact).where(Artifact.organization_id == actor.organization_id, Artifact.deleted_at.is_(None))
    if project_id is not None:
        project = load_project(db, actor, project_id, "artifact:read")
        stmt = stmt.where(Artifact.project_id == project.id)
    else:
        actor.require("artifact:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(Artifact.project_id.in_(visible))
    if mission_id is not None:
        stmt = stmt.where(Artifact.mission_id == mission_id)
    if experiment_run_id is not None:
        stmt = stmt.where(Artifact.experiment_run_id == experiment_run_id)
    if kind is not None:
        if kind not in ARTIFACT_KINDS:
            raise ValidationFailed(f"kind must be one of {', '.join(ARTIFACT_KINDS)}")
        stmt = stmt.where(Artifact.kind == kind)
    return paginate_keyset(
        db, stmt, params, time_col=Artifact.created_at, id_col=Artifact.id, mapper=mapper or (lambda a: a)
    )


def list_artifact_versions(
    db: Session, actor: Actor, artifact_id: uuid.UUID | str, params: PageParams, *, mapper: Any = None
) -> Page[Any]:
    artifact = get_artifact(db, actor, artifact_id)
    stmt = (
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version.desc())
    )
    return paginate(db, stmt, params, mapper or (lambda v: v))


def current_versions(db: Session, artifacts: list[Artifact]) -> dict[uuid.UUID, ArtifactVersion]:
    ids = [a.current_version_id for a in artifacts if a.current_version_id is not None]
    if not ids:
        return {}
    rows = db.scalars(select(ArtifactVersion).where(ArtifactVersion.id.in_(ids))).all()
    return {row.id: row for row in rows}


# -- bytes -----------------------------------------------------------------------------------------
def resolve_artifact_download(db: Session, actor: Actor, version_id: uuid.UUID | str) -> DownloadTarget:
    """Authorize a download (``artifact:download``), refuse quarantined bytes and audit the access."""
    version = get_artifact_version(db, actor, version_id, permission="artifact:download")
    if version.scan_status == "infected":
        raise ArtifactQuarantined()
    audit(
        db,
        actor,
        AuditAction.ARTIFACT_DOWNLOADED,
        "artifact_version",
        version.id,
        after={"artifact_id": str(version.artifact_id), "version": version.version, "size_bytes": version.size_bytes},
    )
    return DownloadTarget(
        key=version.storage_key,
        filename=version.original_filename or f"artifact-v{version.version}",
        content_type=version.mime_type,
        size_bytes=version.size_bytes,
        checksum=version.checksum,
    )


def open_artifact_stream(
    db: Session, actor: Actor, version_id: uuid.UUID | str, chunk_size: int = 1024 * 1024
) -> Iterator[bytes]:
    """Stream an artifact version's bytes (requires ``artifact:download``; quarantined bytes refused)."""
    version = get_artifact_version(db, actor, version_id, permission="artifact:download")
    if version.scan_status == "infected":
        raise ArtifactQuarantined()
    return get_storage().open_stream(version.storage_key, chunk_size)


def read_artifact_bytes(db: Session, actor: Actor, version_id: uuid.UUID | str, max_bytes: int) -> bytes:
    """Read a (small) artifact version fully, verifying its checksum. Larger objects → 413."""
    version = get_artifact_version(db, actor, version_id, permission="artifact:download")
    if version.scan_status == "infected":
        raise ArtifactQuarantined()
    if version.size_bytes > max_bytes:
        raise PayloadTooLarge(f"Artifact version is {version.size_bytes} bytes, above the {max_bytes} byte limit")
    data = get_storage().get_bytes(version.storage_key, max_bytes)
    if len(data) != version.size_bytes or hashlib.sha256(data).hexdigest() != version.checksum:
        log.error("artifact_integrity_mismatch", artifact_version_id=str(version.id))
        raise ArtifactIntegrityError("Artifact bytes failed the integrity check")
    return data


# -- lifecycle -------------------------------------------------------------------------------------
def delete_artifact(db: Session, actor: Actor, artifact_id: uuid.UUID | str) -> Artifact:
    """Soft-delete an artifact. Evidence-class and legal-hold artifacts cannot be deleted (409)."""
    artifact = get_artifact(db, actor, artifact_id, permission="artifact:upload")
    if artifact.retention_class == "evidence":
        raise Conflict("Evidence artifacts are retained for reproducibility and cannot be deleted", code="retained")
    if artifact.legal_hold:
        raise Conflict("This artifact is under legal hold and cannot be deleted", code="legal_hold")
    artifact.deleted_at = utcnow()
    audit(
        db,
        actor,
        ARTIFACT_DELETED,
        "artifact",
        artifact.id,
        before={"name": artifact.name, "kind": artifact.kind, "retention_class": artifact.retention_class},
    )
    db.flush()
    return artifact
