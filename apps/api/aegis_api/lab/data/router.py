"""HTTP API for datasets, artifacts and signed object downloads (tag "Data").

Uploads are multipart: Starlette spools each file part to a temporary file (``UploadFile``), and every
byte after that is streamed — validated in place, scanned, and streamed to object storage. The request
body is never read into memory, which is also why uploads compute their ``Idempotency-Key`` fingerprint
from the validated file digests and form fields instead of the raw body.

Downloads authorize and audit in a short tenant transaction, then hand out a short-lived signed URL
(``307`` redirect, or JSON with ``mode=url``) or stream through the API (``mode=stream``) without holding a
database connection while bytes flow. Request-scoped dependencies are only torn down after a streamed body
has been sent, so the download endpoints also close the authentication layer's session (the same cached
per-request instance ``get_current_principal`` used) before streaming. ``GET /storage/objects/{token}`` serves signed tokens without other
authentication: possession of an unexpired token minted after an authorization check is the grant.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.deps import _raw_session, get_db
from aegis_api.errors import Forbidden, ValidationFailed
from aegis_api.lab.core.access import load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, lab_rate_limit, tenant_uow
from aegis_api.lab.core.idempotency import HEADER as IDEMPOTENCY_HEADER
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.data import artifacts as artifact_service
from aegis_api.lab.data import datasets as dataset_service
from aegis_api.lab.data.artifacts import ArtifactIntegrityError, DownloadTarget, record_rejected_upload
from aegis_api.lab.data.inference import SPLIT_NAME_RE
from aegis_api.lab.data.scanning import MalwareDetected, ScanResult, enforce_scan, get_scanner, scan_file
from aegis_api.lab.data.schemas import (
    ArtifactOut,
    ArtifactVersionOut,
    DatasetCreate,
    DatasetLineageOut,
    DatasetOut,
    DatasetVersionOut,
    DownloadMode,
    ErrorEnvelope,
    RetentionClass,
    SignedUrlOut,
)
from aegis_api.lab.data.uploads import DATASET_KINDS, ValidatedLabUpload, detect_extension, validate_lab_upload
from aegis_api.lab.models import Artifact, ArtifactVersion, IdempotencyKey
from aegis_api.lab.storage import get_storage
from aegis_api.lab.storage.signing import content_disposition, verify_download
from aegis_api.ratelimit import get_limiter
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.uploads import sanitize_filename

router = APIRouter(prefix="/api/v1", tags=["Data"])


def _err(description: str) -> dict[str, Any]:
    return {"model": ErrorEnvelope, "description": description}


READ_ERRORS: dict[int | str, dict[str, Any]] = {
    401: _err("Authentication required"),
    403: _err("Missing permission"),
    404: _err("Not found (or owned by another organization)"),
}
UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    **READ_ERRORS,
    409: _err("Conflict"),
    413: _err("Upload exceeds the size limit"),
    422: _err("Invalid file (type, content, archive safety) or rejected by the malware scanner (malware_detected)"),
    503: _err("Object storage or malware scanner unavailable"),
}
DOWNLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    **READ_ERRORS,
    429: _err("Download rate limit exceeded"),
    503: _err("Object storage unavailable"),
}
NO_STORE = "private, no-store"


# -- mapping helpers -------------------------------------------------------------------------------
def _artifact_out(artifact: Artifact, version: ArtifactVersion | None) -> ArtifactOut:
    out = ArtifactOut.model_validate(artifact)
    if version is not None:
        out.current_version = ArtifactVersionOut.model_validate(version)
    return out


def _json_form(value: str | None, label: str) -> Any:
    if value is None or not value.strip():
        return None
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ValidationFailed(f"'{label}' must be valid JSON") from exc


def _uuid_form(value: str | None, label: str) -> uuid.UUID | None:
    if value is None or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError as exc:
        raise ValidationFailed(f"'{label}' must be a UUID") from exc


def _close_all(uploads: list[ValidatedLabUpload]) -> None:
    for upload in uploads:
        upload.close()


def _scan_or_reject(actor: Actor, project_id: uuid.UUID, upload: ValidatedLabUpload) -> ScanResult:
    """User uploads fail closed: infected → 422 (audited); scanner down → 503."""
    result = scan_file(upload.open(), get_scanner())
    try:
        enforce_scan(result, fail_closed=True)
    except MalwareDetected:
        record_rejected_upload(actor, project_id=project_id, filename=upload.filename, result=result)
        raise
    return result


def _upload_idempotency(
    request: Request, db: Session, actor: Actor, uploads: list[ValidatedLabUpload], fields: dict[str, Any]
) -> Idempotency:
    """``Idempotency-Key`` for multipart uploads, fingerprinted from file digests + form fields.

    Mirrors :func:`aegis_api.lab.core.idempotency.idempotency` (same table, scope and semantics) without
    reading the request body into memory.
    """
    key = request.headers.get(IDEMPOTENCY_HEADER)
    if not key:
        return Idempotency(db, None, replayed=False)
    if not (8 <= len(key) <= 255) or not all(c.isalnum() or c in "_-:." for c in key) or not key.isascii():
        raise ValidationFailed("Idempotency-Key must be 8-255 characters of [A-Za-z0-9_-:.]")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "m": request.method,
                "p": request.url.path,
                "files": [[u.filename, u.sha256, u.size] for u in uploads],
                "fields": jsonable_encoder(fields),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    principal_key = f"key:{actor.api_key_id}" if actor.api_key_id else f"user:{actor.user_id}"
    now = utcnow()
    ttl = timedelta(hours=get_settings().idempotency_ttl_hours)
    inserted = db.execute(
        insert(IdempotencyKey)
        .values(
            organization_id=actor.organization_id,
            principal_key=principal_key,
            key=key,
            method=request.method,
            path=request.url.path[:500],
            request_hash=fingerprint,
            status="in_progress",
            response_status=0,
            response_body={},
            created_at=now,
            expires_at=now + ttl,
        )
        .on_conflict_do_nothing(constraint="uq_idempotency_keys_scope")
        .returning(IdempotencyKey.id)
    ).scalar()
    row = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.organization_id == actor.organization_id,
            IdempotencyKey.principal_key == principal_key,
            IdempotencyKey.key == key,
        )
    )
    if row is None:  # pragma: no cover - defensive
        return Idempotency(db, None, replayed=False)
    if inserted is not None:
        return Idempotency(db, row, replayed=False)
    if row.request_hash != fingerprint:
        raise ValidationFailed(
            "Idempotency-Key was already used with a different request", code="idempotency_key_reused"
        )
    if row.expires_at < now:
        row.request_hash = fingerprint
        row.status = "in_progress"
        row.expires_at = now + ttl
        return Idempotency(db, row, replayed=False)
    return Idempotency(db, row, replayed=True)


def _deliver(target: DownloadTarget, mode: str) -> Response:
    """Hand an authorized object to the client: signed-URL redirect, signed-URL JSON, or a stream."""
    storage = get_storage()
    ttl = get_settings().signed_url_ttl_seconds
    if mode == "stream":
        stream = storage.open_stream(target.key)
        return StreamingResponse(
            stream,
            media_type=target.content_type,
            headers={
                "Content-Disposition": content_disposition(target.filename),
                "Content-Length": str(target.size_bytes),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": NO_STORE,
                "X-Checksum-SHA256": target.checksum,
            },
        )
    url = storage.presign_get(target.key, ttl, target.filename, target.content_type)
    if mode == "url":
        body = SignedUrlOut(
            url=url,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            filename=target.filename,
            size_bytes=target.size_bytes,
            checksum=target.checksum,
        )
        return JSONResponse(jsonable_encoder(body), headers={"Cache-Control": NO_STORE})
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": NO_STORE, "Referrer-Policy": "no-referrer"})


DOWNLOAD_DESCRIPTION = (
    "`mode=redirect` (default) answers **307** to a short-lived signed URL; `mode=url` returns that URL as "
    "JSON; `mode=stream` streams the bytes through the API. Responses force `Content-Disposition: "
    "attachment` and `X-Content-Type-Options: nosniff`."
)


# -- datasets --------------------------------------------------------------------------------------
@router.post(
    "/datasets",
    response_model=DatasetOut,
    status_code=201,
    summary="Create a dataset",
    description="Create a named dataset in a project (`dataset:create`). Names are unique per project.",
    responses={**READ_ERRORS, 409: _err("A dataset with this name already exists"), 422: _err("Invalid input")},
)
def create_dataset(
    body: DatasetCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
    idem: Idempotency = Depends(idempotency),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    dataset = dataset_service.create_dataset(db, actor, body)
    return idem.remember(201, DatasetOut.model_validate(dataset))


@router.get(
    "/datasets",
    response_model=Page[DatasetOut],
    summary="List datasets",
    description="Datasets in projects visible to the caller, newest first. Filter by `project_id`; `q` "
    "searches name and description.",
    responses=READ_ERRORS,
)
def list_datasets(
    project_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=100),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return dataset_service.list_datasets(
        db, actor, params, project_id=project_id, q=q, mapper=DatasetOut.model_validate
    )


@router.get(
    "/datasets/{dataset_id}",
    response_model=DatasetOut,
    summary="Get a dataset",
    description="Dataset metadata including the current version id (`dataset:read`).",
    responses=READ_ERRORS,
)
def get_dataset(dataset_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return DatasetOut.model_validate(dataset_service.get_dataset(db, actor, dataset_id))


def _assign_split_files(uploads: list[ValidatedLabUpload], splits: Any) -> dict[str, ValidatedLabUpload]:
    specs = dataset_service.normalize_splits(splits)
    assigned: dict[str, ValidatedLabUpload] = {}
    used: set[int] = set()

    def stem(upload: ValidatedLabUpload) -> str:
        return upload.filename[: len(upload.filename) - len(detect_extension(upload.filename))]

    for name, spec in specs.items():
        wanted = sanitize_filename(spec.file) if spec.file else None
        matches = [
            i for i, u in enumerate(uploads) if i not in used and (u.filename == wanted if wanted else stem(u) == name)
        ]
        if len(matches) != 1:
            raise ValidationFailed(f"Could not match exactly one uploaded file to split '{name}'")
        assigned[name] = uploads[matches[0]]
        used.add(matches[0])
    for index, upload in enumerate(uploads):
        if index in used:
            continue
        name = stem(upload)
        if not SPLIT_NAME_RE.match(name) or name in assigned:
            raise ValidationFailed(
                f"Cannot derive a split name from '{upload.filename}'; name it in 'splits' with a 'file' entry"
            )
        assigned[name] = upload
    return assigned


@router.post(
    "/datasets/{dataset_id}/versions",
    response_model=DatasetVersionOut,
    status_code=201,
    summary="Upload a new dataset version",
    description=(
        "Multipart upload of one file (`file`) or one file per split (`files`, repeated). Optional form "
        'fields: `format`, `splits` (JSON: split name → `{"visibility": "experiment"|"evaluator_only", '
        '"file": <filename>}`), `split_column` (partition one CSV/TSV/JSONL file by a column), `schema` '
        "(JSON object; otherwise inferred), `parent_version_id`, `transformations` (JSON array), `license`, "
        "`source`, `metadata` (JSON object). Versions are immutable; the schema, row count and SHA-256 are "
        "recorded and anchored in the evidence chain. Supports `Idempotency-Key`."
    ),
    responses=UPLOAD_ERRORS,
)
def create_dataset_version(
    request: Request,
    dataset_id: uuid.UUID,
    file: UploadFile | None = File(None, description="The dataset file"),
    files: list[UploadFile] | None = File(None, description="One file per split"),
    format: str | None = Form(None, max_length=24),
    splits: str | None = Form(None),
    split_column: str | None = Form(None, max_length=200),
    data_schema: str | None = Form(None, alias="schema", description="JSON object; inferred when omitted"),
    parent_version_id: str | None = Form(None),
    transformations: str | None = Form(None),
    license: str | None = Form(None, max_length=120),
    source: str | None = Form(None, max_length=1000),
    metadata: str | None = Form(None),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    parts = ([file] if file is not None else []) + list(files or [])
    if not parts:
        raise ValidationFailed("Upload a file ('file') or one file per split ('files')")
    if len(parts) > dataset_service.MAX_SPLITS:
        raise ValidationFailed(f"At most {dataset_service.MAX_SPLITS} files can be uploaded per version")
    dataset = dataset_service.get_dataset(db, actor, dataset_id)
    load_project(db, actor, dataset.project_id, "dataset:create")
    fields = {
        "format": format,
        "splits": _json_form(splits, "splits"),
        "split_column": split_column,
        "schema": _json_form(data_schema, "schema"),
        "parent_version_id": _uuid_form(parent_version_id, "parent_version_id"),
        "transformations": _json_form(transformations, "transformations"),
        "license": license,
        "source": source,
        "metadata": _json_form(metadata, "metadata"),
    }
    uploads: list[ValidatedLabUpload] = []
    try:
        for part in parts:
            uploads.append(
                validate_lab_upload(part.filename, part.content_type, part.file, allowed_kinds=DATASET_KINDS)
            )
        scans = [_scan_or_reject(actor, dataset.project_id, upload) for upload in uploads]
        idem = _upload_idempotency(request, db, actor, uploads, {"dataset_id": dataset_id, **fields})
        if (replay := idem.replay()) is not None:
            return replay
        common: dict[str, Any] = {**fields, "scan_result": dataset_service.worst_scan(scans)}
        if len(uploads) > 1:
            if split_column:
                raise ValidationFailed("split_column cannot be combined with one file per split")
            split_files = _assign_split_files(uploads, fields["splits"])
            version = dataset_service.create_dataset_version(db, actor, dataset.id, split_files=split_files, **common)
        else:
            version = dataset_service.create_dataset_version(db, actor, dataset.id, upload=uploads[0], **common)
        return idem.remember(201, DatasetVersionOut.model_validate(version))
    finally:
        _close_all(uploads)


@router.get(
    "/datasets/{dataset_id}/versions",
    response_model=Page[DatasetVersionOut],
    summary="List dataset versions",
    description="Immutable versions of a dataset, newest first (`dataset:read`).",
    responses=READ_ERRORS,
)
def list_dataset_versions(
    dataset_id: uuid.UUID,
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return dataset_service.list_dataset_versions(db, actor, dataset_id, params, mapper=DatasetVersionOut.model_validate)


@router.get(
    "/dataset-versions/{version_id}",
    response_model=DatasetVersionOut,
    summary="Get a dataset version",
    description="Checksum, size, row count, schema, splits (without storage locations) and provenance.",
    responses=READ_ERRORS,
)
def get_dataset_version(version_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return DatasetVersionOut.model_validate(dataset_service.get_dataset_version(db, actor, version_id))


@router.get(
    "/dataset-versions/{version_id}/lineage",
    response_model=DatasetLineageOut,
    summary="Dataset version lineage",
    description="The version followed by its parent chain (nearest first, at most 100 ancestors).",
    responses=READ_ERRORS,
)
def get_dataset_lineage(version_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    chain, truncated = dataset_service.dataset_lineage(db, actor, version_id)
    return DatasetLineageOut(
        version_id=str(version_id),
        depth=len(chain) - 1,
        items=[DatasetVersionOut.model_validate(v) for v in chain],
        truncated=truncated,
    )


@router.get(
    "/dataset-versions/{version_id}/download",
    status_code=307,
    response_class=Response,
    summary="Download a dataset version or split",
    description=DOWNLOAD_DESCRIPTION
    + " Without `split`, the version's main object is returned (a JSON manifest for one-file-per-split "
    "versions). Evaluator-only splits require `for_evaluator=true`, a signed-in human and `evaluation:run`.",
    responses={
        **DOWNLOAD_ERRORS,
        200: {"description": "Stream (mode=stream) or signed URL (mode=url)", "model": SignedUrlOut},
        307: {"description": "Redirect to a short-lived signed URL"},
    },
)
def download_dataset_version(
    version_id: uuid.UUID,
    split: str | None = Query(None, max_length=64),
    mode: DownloadMode = Query("redirect"),
    for_evaluator: bool = Query(False),
    actor: Actor = Depends(get_actor),
    _rate: None = Depends(lab_rate_limit("download")),
    auth_session: Session = Depends(_raw_session),
) -> Response:
    if for_evaluator and not actor.is_human:
        raise Forbidden("Evaluator-only data can only be downloaded by signed-in users")
    with tenant_uow(actor) as db:
        target = dataset_service.resolve_dataset_object(db, actor, version_id, split, for_evaluator=for_evaluator)
        dataset_service.audit_dataset_download(db, actor, version_id, split)
    auth_session.close()
    return _deliver(target, mode)


# -- artifacts -------------------------------------------------------------------------------------
@router.post(
    "/artifacts",
    response_model=ArtifactOut,
    status_code=201,
    summary="Upload an artifact",
    description=(
        "Multipart upload creating a new artifact with version 1 (`artifact:upload`). Form fields: "
        "`project_id`, `file`, optional `name` (defaults to the filename), `kind` (inferred from the "
        "extension), `description`, `mission_id`, `experiment_run_id`, `retention_class` "
        "(`evidence`|`standard`|`ephemeral`), `metadata` (JSON object). Supports `Idempotency-Key`."
    ),
    responses=UPLOAD_ERRORS,
)
def upload_artifact(
    request: Request,
    project_id: uuid.UUID = Form(...),
    file: UploadFile = File(..., description="The artifact file"),
    name: str | None = Form(None, max_length=300),
    kind: str | None = Form(None, max_length=32),
    description: str | None = Form(None, max_length=10_000),
    mission_id: str | None = Form(None),
    experiment_run_id: str | None = Form(None),
    retention_class: RetentionClass = Form("standard"),
    metadata: str | None = Form(None),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    fields = {
        "project_id": project_id,
        "name": name,
        "kind": kind,
        "description": description,
        "mission_id": _uuid_form(mission_id, "mission_id"),
        "experiment_run_id": _uuid_form(experiment_run_id, "experiment_run_id"),
        "retention_class": retention_class,
        "metadata": _json_form(metadata, "metadata"),
    }
    load_project(db, actor, project_id, "artifact:upload")
    upload = validate_lab_upload(file.filename, file.content_type, file.file)
    try:
        scan = _scan_or_reject(actor, project_id, upload)
        idem = _upload_idempotency(request, db, actor, [upload], fields)
        if (replay := idem.replay()) is not None:
            return replay
        version = artifact_service.create_artifact_version(
            db,
            actor,
            project_id=project_id,
            kind=kind,
            name=name or upload.filename,
            fileobj=upload.open(),
            mime_type=upload.content_type,
            filename=upload.filename,
            mission_id=fields["mission_id"],
            experiment_run_id=fields["experiment_run_id"],
            retention_class=retention_class,
            metadata=fields["metadata"],
            description=description,
            scan_result=scan,
            fail_closed_scan=True,
        )
        if version.checksum != upload.sha256:
            raise ArtifactIntegrityError("Upload changed while it was being stored")
        artifact = artifact_service.get_artifact(db, actor, version.artifact_id)
        return idem.remember(201, _artifact_out(artifact, version))
    finally:
        upload.close()


@router.post(
    "/artifacts/{artifact_id}/versions",
    response_model=ArtifactVersionOut,
    status_code=201,
    summary="Upload a new artifact version",
    description="Multipart upload (`file`, optional `metadata` JSON) of the next immutable version of an "
    "artifact (`artifact:upload`). Supports `Idempotency-Key`.",
    responses=UPLOAD_ERRORS,
)
def upload_artifact_version(
    request: Request,
    artifact_id: uuid.UUID,
    file: UploadFile = File(..., description="The artifact file"),
    metadata: str | None = Form(None),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    artifact = artifact_service.get_artifact(db, actor, artifact_id, permission="artifact:upload")
    fields = {"artifact_id": artifact_id, "metadata": _json_form(metadata, "metadata")}
    upload = validate_lab_upload(file.filename, file.content_type, file.file)
    try:
        scan = _scan_or_reject(actor, artifact.project_id, upload)
        idem = _upload_idempotency(request, db, actor, [upload], fields)
        if (replay := idem.replay()) is not None:
            return replay
        version = artifact_service.create_artifact_version(
            db,
            actor,
            project_id=artifact.project_id,
            artifact_id=artifact.id,
            fileobj=upload.open(),
            mime_type=upload.content_type,
            filename=upload.filename,
            metadata=fields["metadata"],
            scan_result=scan,
            fail_closed_scan=True,
        )
        if version.checksum != upload.sha256:
            raise ArtifactIntegrityError("Upload changed while it was being stored")
        return idem.remember(201, ArtifactVersionOut.model_validate(version))
    finally:
        upload.close()


@router.get(
    "/artifacts",
    response_model=CursorPage[ArtifactOut],
    summary="List artifacts",
    description="Artifacts visible to the caller, newest first (cursor pagination). Filters: `project_id`, "
    "`mission_id`, `experiment_run_id`, `kind`. Soft-deleted artifacts are excluded.",
    responses=READ_ERRORS,
)
def list_artifacts(
    project_id: uuid.UUID | None = Query(None),
    mission_id: uuid.UUID | None = Query(None),
    experiment_run_id: uuid.UUID | None = Query(None),
    kind: str | None = Query(None, max_length=32),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    page = artifact_service.list_artifacts(
        db,
        actor,
        params,
        project_id=project_id,
        mission_id=mission_id,
        experiment_run_id=experiment_run_id,
        kind=kind,
    )
    versions = artifact_service.current_versions(db, page.items)
    items = [_artifact_out(a, versions.get(a.current_version_id) if a.current_version_id else None) for a in page.items]
    return CursorPage[ArtifactOut](items=items, next_cursor=page.next_cursor, limit=page.limit)


@router.get(
    "/artifacts/{artifact_id}",
    response_model=ArtifactOut,
    summary="Get an artifact",
    description="Artifact metadata with its current version (`artifact:read`).",
    responses=READ_ERRORS,
)
def get_artifact(artifact_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    artifact = artifact_service.get_artifact(db, actor, artifact_id)
    version = db.get(ArtifactVersion, artifact.current_version_id) if artifact.current_version_id else None
    return _artifact_out(artifact, version)


@router.get(
    "/artifacts/{artifact_id}/versions",
    response_model=Page[ArtifactVersionOut],
    summary="List artifact versions",
    description="Immutable versions of an artifact, newest first (`artifact:read`).",
    responses=READ_ERRORS,
)
def list_artifact_versions(
    artifact_id: uuid.UUID,
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return artifact_service.list_artifact_versions(
        db, actor, artifact_id, params, mapper=ArtifactVersionOut.model_validate
    )


@router.get(
    "/artifact-versions/{version_id}",
    response_model=ArtifactVersionOut,
    summary="Get an artifact version",
    description="Size, SHA-256, media type, scan status and metadata of one immutable version.",
    responses=READ_ERRORS,
)
def get_artifact_version(
    version_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> Any:
    return ArtifactVersionOut.model_validate(artifact_service.get_artifact_version(db, actor, version_id))


@router.get(
    "/artifact-versions/{version_id}/download",
    status_code=307,
    response_class=Response,
    summary="Download an artifact version",
    description=DOWNLOAD_DESCRIPTION + " Requires `artifact:download`; every download is audited. Versions "
    "flagged by the malware scanner are refused (403 `artifact_quarantined`).",
    responses={
        **DOWNLOAD_ERRORS,
        200: {"description": "Stream (mode=stream) or signed URL (mode=url)", "model": SignedUrlOut},
        307: {"description": "Redirect to a short-lived signed URL"},
    },
)
def download_artifact_version(
    version_id: uuid.UUID,
    mode: DownloadMode = Query("redirect"),
    actor: Actor = Depends(get_actor),
    _rate: None = Depends(lab_rate_limit("download")),
    auth_session: Session = Depends(_raw_session),
) -> Response:
    with tenant_uow(actor) as db:
        target = artifact_service.resolve_artifact_download(db, actor, version_id)
    auth_session.close()
    return _deliver(target, mode)


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=204,
    response_class=Response,
    summary="Delete an artifact",
    description="Soft delete (`artifact:upload`). Evidence-class and legal-hold artifacts cannot be deleted "
    "(409); stored versions remain for lineage until retention purges them.",
    responses={**READ_ERRORS, 409: _err("Artifact is retained (evidence class or legal hold)")},
)
def delete_artifact(
    artifact_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> Response:
    artifact_service.delete_artifact(db, actor, artifact_id)
    return Response(status_code=204)


# -- signed objects --------------------------------------------------------------------------------
@router.get(
    "/storage/objects/{token}",
    response_class=StreamingResponse,
    summary="Fetch an object with a signed token",
    description=(
        "Streams the object named by an HMAC-signed, expiring token minted by a download endpoint. No other "
        "authentication: possession of an unexpired token is the grant. Invalid, tampered or expired tokens "
        "→ 403."
    ),
    responses={
        200: {"description": "The object bytes (attachment)"},
        403: _err("Invalid or expired token"),
        404: _err("Object not found"),
        429: _err("Rate limited"),
    },
)
def get_signed_object(token: str) -> StreamingResponse:
    signed = verify_download(token)
    # Keys are tenant-prefixed ("org/<id>/…"): limit per organization, not per (possibly shared) client IP.
    segments = signed.key.split("/", 2)
    tenant = segments[1] if len(segments) > 2 and segments[0] == "org" else "unscoped"
    get_limiter().check_custom("lab-signed-object", f"org:{tenant}", get_settings().rate_limit_download_per_min)
    storage = get_storage()
    info = storage.stat(signed.key)
    stream = storage.open_stream(signed.key)
    return StreamingResponse(
        stream,
        media_type=signed.content_type,
        headers={
            "Content-Disposition": content_disposition(signed.filename, fallback=signed.key.rsplit("/", 1)[-1]),
            "Content-Length": str(info.size),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": NO_STORE,
            "Referrer-Policy": "no-referrer",
        },
    )
