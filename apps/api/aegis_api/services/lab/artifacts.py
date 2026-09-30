"""Artifact store: content-addressed, versioned objects with checksums, scan status and download auditing.

Bytes live in object storage (S3/MinIO or local); rows record the key, SHA-256, size and type. Downloads are
permission-checked (``artifact:download``), audit-logged, and served either through a short-lived presigned
URL or streamed through the API. Evidence-class artifacts are never purged by retention.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from typing import IO, Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound, PayloadTooLarge, ValidationFailed
from aegis_api.infrastructure.scanning import scan_bytes
from aegis_api.infrastructure.storage import ObjectTooLarge, StoredObject, get_storage, object_key
from aegis_api.models.lab import Artifact, ArtifactVersion
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import usage
from aegis_api.services.lab.access import accessible_project_ids, get_scoped

RETENTION_CLASSES = frozenset({"standard", "evidence", "ephemeral"})
SCAN_INLINE_MAX = 25 * 1024 * 1024
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def safe_name(name: str) -> str:
    cleaned = "".join(c if c in _SAFE_NAME else "_" for c in name.strip().split("/")[-1])[:200]
    return cleaned.lstrip(".") or "artifact"


def put_object(organization_id: uuid.UUID, scope: str, data: bytes, *, content_type: str) -> tuple[StoredObject, str]:
    digest = hashlib.sha256(data).hexdigest()
    key = object_key(organization_id, "artifacts", scope, digest)
    return get_storage().put_bytes(key, data, content_type=content_type), key


def register(
    db: Session,
    *,
    organization_id: uuid.UUID,
    stored: StoredObject,
    key: str,
    name: str,
    kind: str,
    content_type: str,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_run_id: uuid.UUID | None = None,
    execution_job_id: uuid.UUID | None = None,
    retention_class: str = "standard",
    created_by: str | None = None,
    scan_status: str = "not_scanned",
    metadata: dict[str, Any] | None = None,
    artifact: Artifact | None = None,
) -> tuple[Artifact, ArtifactVersion]:
    if retention_class not in RETENTION_CLASSES:
        raise ValidationFailed(f"retention_class must be one of {sorted(RETENTION_CLASSES)}")
    if artifact is None:
        artifact = Artifact(
            organization_id=organization_id,
            project_id=project_id,
            mission_id=mission_id,
            experiment_run_id=experiment_run_id,
            execution_job_id=execution_job_id,
            kind=kind[:32],
            name=safe_name(name),
            retention_class=retention_class,
            created_by=(created_by or "")[:160] or None,
        )
        db.add(artifact)
        db.flush()
    number = 1 + int(
        db.scalar(
            select(func.coalesce(func.max(ArtifactVersion.version), 0)).where(
                ArtifactVersion.artifact_id == artifact.id
            )
        )
        or 0
    )
    version = ArtifactVersion(
        organization_id=organization_id,
        artifact_id=artifact.id,
        version=number,
        storage_key=key,
        sha256=stored.sha256,
        size_bytes=stored.size,
        content_type=content_type[:120],
        scan_status=scan_status,
        av_metadata=metadata or {},
    )
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    usage.record_storage(
        db,
        organization_id=organization_id,
        object_kind="artifact",
        object_id=version.id,
        size_bytes=stored.size,
        project_id=artifact.project_id,
        mission_id=artifact.mission_id,
    )
    return artifact, version


def store(
    organization_id: uuid.UUID,
    *,
    data: bytes,
    name: str,
    kind: str,
    content_type: str = "application/octet-stream",
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_run_id: uuid.UUID | None = None,
    execution_job_id: uuid.UUID | None = None,
    retention_class: str = "standard",
    created_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Activity helper: object write first (no transaction), then a short transaction for the rows.
    Returns (artifact_id, version_id, sha256)."""
    scope = str(project_id or mission_id or "org")
    stored, key = put_object(organization_id, scope, data, content_type=content_type)
    with session_scope(organization_id) as db:
        artifact, version = register(
            db,
            organization_id=organization_id,
            stored=stored,
            key=key,
            name=name,
            kind=kind,
            content_type=content_type,
            project_id=project_id,
            mission_id=mission_id,
            experiment_run_id=experiment_run_id,
            execution_job_id=execution_job_id,
            retention_class=retention_class,
            created_by=created_by,
            scan_status="platform_generated",
            metadata=metadata,
        )
        return artifact.id, version.id, version.sha256


def upload(
    db: Session,
    principal: Principal,
    *,
    project_id: uuid.UUID,
    mission_id: uuid.UUID | None,
    name: str,
    kind: str,
    content_type: str,
    stream: IO[bytes],
) -> tuple[Artifact, ArtifactVersion]:
    """User upload. The caller commits its transaction before calling (no transaction across the upload)."""
    principal.require("artifact:upload")
    settings = get_settings()
    scope = str(project_id)
    tmp_key = object_key(principal.organization_id, "uploads", scope, uuid.uuid4().hex)
    storage = get_storage()
    try:
        stored = storage.put_stream(
            tmp_key, stream, content_type=content_type, max_bytes=settings.max_artifact_upload_bytes
        )
    except ObjectTooLarge as exc:
        raise PayloadTooLarge(f"Artifact exceeds {settings.max_artifact_upload_bytes} bytes") from exc
    scan = "not_scanned"
    if stored.size <= SCAN_INLINE_MAX:
        result = scan_bytes(storage.get_bytes(tmp_key, max_bytes=SCAN_INLINE_MAX))
        scan = result.status
        if result.status == "infected":
            storage.delete(tmp_key)
            audit_log.record(
                db,
                organization_id=principal.organization_id,
                action="lab.artifact.upload_rejected",
                resource_type="artifact",
                principal=principal,
                after={"name": name, "reason": result.detail},
            )
            raise ValidationFailed("Upload rejected by the malware scanner")
    return register(
        db,
        organization_id=principal.organization_id,
        stored=stored,
        key=tmp_key,
        name=name,
        kind=kind,
        content_type=content_type,
        project_id=project_id,
        mission_id=mission_id,
        created_by=principal.actor_label,
        scan_status=scan,
    )


def list_artifacts(
    db: Session,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_run_id: uuid.UUID | None = None,
    kind: str | None = None,
) -> Select[Artifact]:
    stmt = select(Artifact).where(Artifact.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Artifact.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(Artifact.project_id == project_id)
    if mission_id:
        stmt = stmt.where(Artifact.mission_id == mission_id)
    if experiment_run_id:
        stmt = stmt.where(Artifact.experiment_run_id == experiment_run_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    return stmt.order_by(Artifact.created_at.desc())


def versions(db: Session, artifact: Artifact) -> list[ArtifactVersion]:
    return list(
        db.scalars(
            select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id).order_by(ArtifactVersion.version)
        ).all()
    )


def resolve_version(db: Session, artifact: Artifact, version: int | None) -> ArtifactVersion:
    if version is None:
        row = db.get(ArtifactVersion, artifact.current_version_id) if artifact.current_version_id else None
    else:
        row = db.scalar(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.version == version
            )
        )
    if row is None:
        raise NotFound("Artifact version not found")
    if row.purged_at is not None:
        raise NotFound("Artifact content was purged by the retention policy")
    return row


def download(
    db: Session, principal: Principal, artifact_id: uuid.UUID | str, *, version: int | None = None
) -> tuple[Artifact, ArtifactVersion, str | None, Iterator[bytes] | None]:
    """Returns (artifact, version, presigned_url | None, stream | None) and audit-logs the access."""
    principal.require("artifact:download")
    artifact = get_scoped(db, principal, Artifact, artifact_id, label="Artifact")
    row = resolve_version(db, artifact, version)
    if row.scan_status == "infected":
        raise ValidationFailed("Artifact is quarantined (malware detected)")
    audit_log.record(
        db,
        organization_id=artifact.organization_id,
        action="lab.artifact.downloaded",
        resource_type="artifact",
        resource_id=artifact.id,
        principal=principal,
        after={"version": row.version, "sha256": row.sha256},
    )
    storage = get_storage()
    url = storage.presign_get(
        row.storage_key, ttl_seconds=get_settings().object_storage_presign_ttl_seconds, filename=artifact.name
    )
    if url:
        return artifact, row, url, None
    return artifact, row, None, storage.open_stream(row.storage_key)


def verify_checksum(version: ArtifactVersion) -> tuple[str, str | None]:
    """Recompute the stored object's SHA-256 (tamper/corruption detection)."""
    digest = hashlib.sha256()
    try:
        for chunk in get_storage().open_stream(version.storage_key):
            digest.update(chunk)
    except Exception:
        return version.sha256, None
    return version.sha256, digest.hexdigest()


def purge_version(db: Session, version: ArtifactVersion) -> None:
    get_storage().delete(version.storage_key)
    version.purged_at = utcnow()
    usage.record_storage(
        db,
        organization_id=version.organization_id,
        object_kind="artifact",
        object_id=version.id,
        size_bytes=version.size_bytes,
        operation="delete",
    )
