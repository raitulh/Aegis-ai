"""Datasets with immutable versions, splits with visibility, schema inference and lineage.

A ``Dataset`` is a named, mutable container inside a project; every ``DatasetVersion`` is append-only
(UPDATE/DELETE rejected by a trigger) and pins the exact bytes (SHA-256 + size) experiments ran on.

Version sources (exactly one):

* one file (``upload`` from the router, or ``data``/``fileobj`` from internal callers) — optionally
  partitioned by a ``split_column`` into per-split objects;
* one file per split (``split_files``) — the version's main object is a canonical JSON manifest whose
  SHA-256 covers every split checksum;
* an existing artifact version (``artifact_version_id``) — copied server-side into the dataset namespace.

Splits carry a visibility: ``experiment`` (may be mounted into experiment sandboxes) or ``evaluator_only``
(held-out labels: only evaluator code paths may read them, never agents or experiments). When a version
contains evaluator-only data, the full main object is also restricted to evaluators.

Version numbers are ``max+1`` under ``advisory_xact_lock("dataset-version:<id>")``; each version records
storage usage, an evidence record (checksums) and a ``DATASET_VERSION_CREATED`` audit entry.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, cast

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import Conflict, Forbidden, NotFound, PayloadTooLarge, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.data.artifacts import (
    ArtifactIntegrityError,
    ArtifactQuarantined,
    DownloadTarget,
    record_rejected_upload,
)
from aegis_api.lab.data.flows import launch_dataset_processing
from aegis_api.lab.data.inference import (
    KIND_TO_FORMAT,
    SPLIT_NAME_RE,
    InferredSchema,
    infer_delimited,
    infer_jsonl,
    infer_npy,
    infer_schema,
    partition_by_column,
)
from aegis_api.lab.data.scanning import MalwareDetected, ScanResult, get_scanner, scan_file
from aegis_api.lab.data.schemas import DatasetCreate, SplitSpec
from aegis_api.lab.data.uploads import DATASET_KINDS, ValidatedLabUpload, file_type_for, validate_lab_upload
from aegis_api.lab.models import Artifact, ArtifactVersion, Dataset, DatasetVersion, Project
from aegis_api.lab.storage import get_storage
from aegis_api.lab.storage.base import StoredObject, stream_reader
from aegis_api.lab.storage.keys import object_key
from aegis_api.lab.storage.tx import delete_on_rollback
from aegis_api.lab.usage.recorder import record_storage_usage
from aegis_api.schemas.common import Page, PageParams

MAX_SPLITS = 32
MAX_LINEAGE_DEPTH = 100
MAX_JSON_FIELD_BYTES = 64 * 1024
INLINE_PROFILE_LIMIT = 256 * 1024 * 1024
DATASET_CREATED = "DATASET_CREATED"
DATASET_DOWNLOADED = "DATASET_DOWNLOADED"
_SCAN_SEVERITY = {"clean": 0, "not_scanned": 1, "error": 2, "infected": 3}


class EvaluatorOnlyData(Forbidden):
    """Held-out evaluator data is only available to evaluators."""

    code = "evaluator_only_split"


@dataclass
class _StoredSplit:
    name: str
    stored: StoredObject
    upload_filename: str
    rows: int | None
    visibility: str
    schema: dict[str, Any]


# -- helpers ---------------------------------------------------------------------------------------
def _json_field(value: Any, label: str, expected: type) -> Any:
    if value is None:
        return None
    if not isinstance(value, expected):
        raise ValidationFailed(f"{label} must be a JSON {'object' if expected is dict else 'array'}")
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{label} must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_JSON_FIELD_BYTES:
        raise ValidationFailed(f"{label} exceeds {MAX_JSON_FIELD_BYTES} bytes")
    return json.loads(encoded)


def normalize_splits(splits: Mapping[str, Any] | None) -> dict[str, SplitSpec]:
    """Accept ``{"test": {"visibility": "evaluator_only", "file": "test.csv"}}`` or ``{"test": "evaluator_only"}``."""
    if splits is None:
        return {}
    if not isinstance(splits, Mapping):
        raise ValidationFailed("splits must be an object mapping split name to its specification")
    if len(splits) > MAX_SPLITS:
        raise ValidationFailed(f"At most {MAX_SPLITS} splits are supported")
    normalized: dict[str, SplitSpec] = {}
    for name, spec in splits.items():
        if not isinstance(name, str) or not SPLIT_NAME_RE.match(name):
            raise ValidationFailed("Split names must be 1-64 characters of [A-Za-z0-9_-]")
        if isinstance(spec, SplitSpec):
            normalized[name] = spec
            continue
        raw = {"visibility": spec} if isinstance(spec, str) else (spec or {})
        try:
            normalized[name] = SplitSpec.model_validate(raw)
        except PydanticValidationError as exc:
            raise ValidationFailed(
                f"Invalid specification for split '{name}'",
                details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
            ) from exc
    return normalized


_FORMAT_EXTENSIONS = {
    "csv": ".csv",
    "tsv": ".tsv",
    "json": ".json",
    "jsonl": ".jsonl",
    "parquet": ".parquet",
    "npy": ".npy",
    "npz": ".npz",
    "zip": ".zip",
    "tar.gz": ".tar.gz",
    "tar_gz": ".tar.gz",
    "text": ".txt",
}


def _default_filename(fmt: str | None) -> str:
    extension = _FORMAT_EXTENSIONS.get((fmt or "").lower().lstrip("."))
    if extension is None:
        raise ValidationFailed("Provide a filename (or a supported format) for the dataset file")
    return f"data{extension}"


def worst_scan(results: list[ScanResult]) -> ScanResult:
    """The most severe of several scan results (infected > error > not_scanned > clean)."""
    if not results:
        scanner = get_scanner()
        return ScanResult(status="not_scanned", scanner=scanner.name)
    return max(results, key=lambda r: _SCAN_SEVERITY.get(r.status, 1))


def _scan_uploads(actor: Actor, project: Project, uploads: list[ValidatedLabUpload]) -> ScanResult:
    """Best-effort scan for internal callers; infected files are always rejected (and audited)."""
    scanner = get_scanner()
    if not scanner.enabled:
        return ScanResult(status="not_scanned", scanner=scanner.name)
    results = []
    for upload in uploads:
        result = scan_file(upload.open(), scanner)
        if result.infected:
            record_rejected_upload(actor, project_id=project.id, filename=upload.filename, result=result)
            raise MalwareDetected(
                "The file was rejected by the malware scanner",
                details={"scanner": result.scanner, "signature": result.signature},
            )
        results.append(result)
    return worst_scan(results)


def _store_upload(db: Session, key: str, upload: ValidatedLabUpload) -> StoredObject:
    storage = get_storage()
    stored = storage.put_stream(key, upload.open(), upload.content_type, max_bytes=upload.size)
    delete_on_rollback(db, key, storage)
    if stored.sha256 != upload.sha256 or stored.size != upload.size:
        raise ArtifactIntegrityError("Upload changed while it was being stored")
    return stored


# -- datasets --------------------------------------------------------------------------------------
def create_dataset(db: Session, actor: Actor, data: DatasetCreate | Mapping[str, Any]) -> Dataset:
    if not isinstance(data, DatasetCreate):
        try:
            data = DatasetCreate.model_validate(dict(data))
        except PydanticValidationError as exc:
            raise ValidationFailed("Invalid dataset", details=exc.errors(include_url=False)) from exc
    project = load_project(db, actor, data.project_id, "dataset:create")
    exists = db.scalar(select(Dataset.id).where(Dataset.project_id == project.id, Dataset.name == data.name))
    if exists is not None:
        raise Conflict("A dataset with this name already exists in the project", code="dataset_exists")
    dataset = Dataset(
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        name=data.name,
        description=data.description,
        license=data.license,
        source=data.source,
        tags=list(data.tags),
        created_by_id=actor.user_id,
    )
    try:
        with db.begin_nested():
            db.add(dataset)
            db.flush()
    except IntegrityError as exc:
        raise Conflict("A dataset with this name already exists in the project", code="dataset_exists") from exc
    audit(db, actor, DATASET_CREATED, "dataset", dataset.id, after={"name": dataset.name, "project_id": project.id})
    return dataset


def get_dataset(db: Session, actor: Actor, dataset_id: uuid.UUID | str) -> Dataset:
    dataset = get_owned(db, Dataset, dataset_id, actor, label="Dataset")
    load_project(db, actor, dataset.project_id, "dataset:read")
    return dataset


def list_datasets(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    project_id: uuid.UUID | str | None = None,
    q: str | None = None,
    mapper: Any = None,
) -> Page[Any]:
    stmt = select(Dataset).where(Dataset.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "dataset:read")
        stmt = stmt.where(Dataset.project_id == project.id)
    else:
        actor.require("dataset:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(Dataset.project_id.in_(visible))
    if q:
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:100] + "%"
        stmt = stmt.where(
            or_(Dataset.name.ilike(pattern, escape="\\"), Dataset.description.ilike(pattern, escape="\\"))
        )
    stmt = stmt.order_by(Dataset.created_at.desc(), Dataset.id.desc())
    return paginate(db, stmt, params, mapper or (lambda d: d))


# -- versions --------------------------------------------------------------------------------------
def create_dataset_version(
    db: Session,
    actor: Actor,
    dataset_id: uuid.UUID | str,
    *,
    data: bytes | None = None,
    fileobj: BinaryIO | None = None,
    filename: str | None = None,
    upload: ValidatedLabUpload | None = None,
    split_files: Mapping[str, ValidatedLabUpload] | None = None,
    artifact_version_id: uuid.UUID | str | None = None,
    format: str | None = None,
    splits: Mapping[str, Any] | None = None,
    split_column: str | None = None,
    schema: dict[str, Any] | None = None,
    parent_version_id: uuid.UUID | str | None = None,
    transformations: list[Any] | None = None,
    license: str | None = None,
    source: str | None = None,
    metadata: dict[str, Any] | None = None,
    scan_result: ScanResult | None = None,
) -> DatasetVersion:
    """Create the next immutable version of a dataset (see module docstring for the source modes).

    ``upload``/``split_files`` are already validated (and scanned — pass ``scan_result``) by the router;
    ``data``/``fileobj`` + ``filename`` are validated and scanned here. The caller keeps ownership of
    ``upload``/``split_files`` (close them after the request).
    """
    provided = [data is not None or fileobj is not None, upload is not None, bool(split_files)]
    provided.append(artifact_version_id is not None)
    if sum(provided) != 1 or (data is not None and fileobj is not None):
        raise ValidationFailed("Provide exactly one dataset source: a file, one file per split, or an artifact")
    dataset = get_owned(db, Dataset, dataset_id, actor, label="Dataset")
    project = load_project(db, actor, dataset.project_id, "dataset:create")
    parent: DatasetVersion | None = None
    if parent_version_id is not None:
        parent = get_owned(db, DatasetVersion, parent_version_id, actor, label="Parent version")
        if parent.dataset_id != dataset.id:
            raise ValidationFailed("parent_version_id must be a version of the same dataset")
    if license is not None and len(license) > 120:
        raise ValidationFailed("license must be at most 120 characters")
    if source is not None and len(source) > 1000:
        raise ValidationFailed("source must be at most 1000 characters")
    split_specs = normalize_splits(splits)
    clean_transformations = _json_field(transformations, "transformations", list) or []
    declared_schema = _json_field(schema, "schema", dict)
    clean_metadata = _json_field(metadata, "metadata", dict) or {}
    if split_column is not None and not split_column.strip():
        raise ValidationFailed("split_column must not be blank")

    context = _VersionContext(
        db=db,
        actor=actor,
        dataset=dataset,
        project=project,
        parent=parent,
        split_specs=split_specs,
        transformations=clean_transformations,
        declared_schema=declared_schema,
        metadata=clean_metadata,
        license=license,
        source=source,
        requested_format=format,
    )
    if artifact_version_id is not None:
        if split_specs or split_column:
            raise ValidationFailed("Splits are not supported for versions created from an artifact")
        return context.from_artifact(artifact_version_id)

    owned: list[ValidatedLabUpload] = []
    try:
        if split_files:
            if split_column:
                raise ValidationFailed("split_column cannot be combined with one file per split")
            files = dict(split_files)
            unknown = set(split_specs) - set(files)
            if unknown:
                raise ValidationFailed(f"No file was uploaded for split(s): {', '.join(sorted(unknown))}")
            for name in files:
                if not SPLIT_NAME_RE.match(name):
                    raise ValidationFailed("Split names must be 1-64 characters of [A-Za-z0-9_-]")
            scan = scan_result or _scan_uploads(actor, project, list(files.values()))
            return context.from_split_files(files, scan)
        if upload is None:
            limit = get_settings().max_artifact_bytes
            raw: BinaryIO = io.BytesIO(data) if data is not None else fileobj  # type: ignore[assignment]
            name = filename or _default_filename(format)
            upload = validate_lab_upload(name, None, raw, max_bytes=limit, allowed_kinds=DATASET_KINDS)
            owned.append(upload)
            scan = scan_result or _scan_uploads(actor, project, [upload])
        else:
            if upload.kind not in DATASET_KINDS:
                raise ValidationFailed(f"Files of type '{upload.extension}' cannot be used as a dataset")
            scan = scan_result or _scan_uploads(actor, project, [upload])
        if split_column:
            if not split_specs:
                raise ValidationFailed("Declare the splits (name → visibility) when using split_column")
            return context.from_split_column(upload, split_column.strip(), scan)
        if split_specs:
            if len(split_specs) != 1:
                raise ValidationFailed("Upload one file per split, or name a split_column to partition one file")
            return context.from_split_files({next(iter(split_specs)): upload}, scan)
        return context.from_single(upload, scan)
    finally:
        for item in owned:
            item.close()


@dataclass
class _VersionContext:
    db: Session
    actor: Actor
    dataset: Dataset
    project: Project
    parent: DatasetVersion | None
    split_specs: dict[str, SplitSpec]
    transformations: list[Any]
    declared_schema: dict[str, Any] | None
    metadata: dict[str, Any]
    license: str | None
    source: str | None
    requested_format: str | None

    # -- shared steps ----------------------------------------------------------------------------
    def _format_for(self, kind: str) -> str:
        fmt = KIND_TO_FORMAT.get(kind, "binary")
        if self.requested_format and self.requested_format.lower().lstrip(".") not in (fmt, kind):
            raise ValidationFailed(f"format '{self.requested_format}' does not match the uploaded {fmt} file")
        return fmt

    def _allocate(self) -> tuple[int, uuid.UUID, tuple[str, ...]]:
        advisory_xact_lock(self.db, f"dataset-version:{self.dataset.id}")
        current = self.db.scalar(
            select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == self.dataset.id)
        )
        number = int(current or 0) + 1
        version_id = uuid.uuid4()
        return number, version_id, ("datasets", str(self.dataset.id), f"v{number}-{version_id.hex[:12]}")

    def _key(self, prefix: tuple[str, ...], *parts: str) -> str:
        return object_key(self.actor.organization_id, self.project.id, *prefix, *parts)

    def _finish(
        self,
        *,
        version_id: uuid.UUID,
        number: int,
        fmt: str,
        main_key: str,
        checksum: str,
        size_bytes: int,
        row_count: int | None,
        inferred: dict[str, Any],
        object_info: dict[str, Any],
        splits: list[_StoredSplit],
        stored_objects: list[StoredObject],
        scan: ScanResult,
    ) -> DatasetVersion:
        db, actor = self.db, self.actor
        metadata = dict(self.metadata)
        metadata["object"] = object_info
        metadata["scan"] = {"status": scan.status, "scanner": scan.scanner}
        if self.declared_schema is not None:
            metadata["inferred_schema"] = inferred
        split_rows = {
            s.name: {
                "storage_key": s.stored.key,
                "checksum": s.stored.sha256,
                "size_bytes": s.stored.size,
                "rows": s.rows,
                "visibility": s.visibility,
                "filename": s.upload_filename,
                "format": fmt,
            }
            for s in splits
        }
        version = DatasetVersion(
            id=version_id,
            organization_id=actor.organization_id,
            dataset_id=self.dataset.id,
            project_id=self.project.id,
            version=number,
            checksum=checksum,
            size_bytes=size_bytes,
            row_count=row_count,
            format=fmt,
            schema=self.declared_schema if self.declared_schema is not None else inferred,
            dataset_metadata=metadata,
            source=self.source if self.source is not None else self.dataset.source,
            license=self.license if self.license is not None else self.dataset.license,
            transformations=self.transformations,
            splits=split_rows,
            parent_version_id=self.parent.id if self.parent else None,
            storage_key=main_key,
            created_by_id=actor.user_id,
        )
        db.add(version)
        db.flush()
        self.dataset.current_version_id = version.id
        for stored in stored_objects:
            record_storage_usage(
                db,
                organization_id=actor.organization_id,
                bytes_delta=stored.size,
                reason="dataset_upload",
                project_id=self.project.id,
                dataset_version_id=version.id,
            )
        append_evidence(
            db,
            organization_id=actor.organization_id,
            kind="dataset_version",
            title=f"Dataset {self.dataset.name[:200]} v{number}",
            content={
                "dataset_id": str(self.dataset.id),
                "dataset_version_id": str(version.id),
                "version": number,
                "format": fmt,
                "sha256": checksum,
                "size_bytes": size_bytes,
                "row_count": row_count,
                "parent_version_id": str(self.parent.id) if self.parent else None,
                "splits": {
                    name: {"sha256": s["checksum"], "rows": s["rows"], "visibility": s["visibility"]}
                    for name, s in split_rows.items()
                },
            },
            storage_key=main_key,
        )
        audit(
            db,
            actor,
            AuditAction.DATASET_VERSION_CREATED,
            "dataset_version",
            version.id,
            after={
                "dataset_id": str(self.dataset.id),
                "version": number,
                "sha256": checksum,
                "size_bytes": size_bytes,
                "row_count": row_count,
                "format": fmt,
                "splits": sorted(split_rows),
            },
        )
        # Full streaming verification + exact profile (the upload path only samples).
        launch_dataset_processing(db, actor, version)
        db.flush()
        return version

    # -- modes -----------------------------------------------------------------------------------
    def from_single(self, upload: ValidatedLabUpload, scan: ScanResult) -> DatasetVersion:
        fmt = self._format_for(upload.kind)
        inferred = infer_schema(upload.kind, upload.open(), upload.size)
        number, version_id, prefix = self._allocate()
        key = self._key(prefix, upload.filename)
        stored = _store_upload(self.db, key, upload)
        return self._finish(
            version_id=version_id,
            number=number,
            fmt=fmt,
            main_key=key,
            checksum=stored.sha256,
            size_bytes=stored.size,
            row_count=inferred.row_count,
            inferred=inferred.schema,
            object_info={"filename": upload.filename, "content_type": upload.content_type, "size_bytes": stored.size},
            splits=[],
            stored_objects=[stored],
            scan=scan,
        )

    def from_split_column(self, upload: ValidatedLabUpload, column: str, scan: ScanResult) -> DatasetVersion:
        fmt = self._format_for(upload.kind)
        inferred = infer_schema(upload.kind, upload.open(), upload.size)
        partitions = partition_by_column(upload.open(), upload.kind, column, self.split_specs)
        try:
            number, version_id, prefix = self._allocate()
            key = self._key(prefix, upload.filename)
            stored_main = _store_upload(self.db, key, upload)
            stored_objects = [stored_main]
            splits: list[_StoredSplit] = []
            storage = get_storage()
            for name in sorted(partitions):
                partition = partitions[name]
                split_filename = f"{name}{upload.extension}"
                split_key = self._key(prefix, "splits", name, split_filename)
                stored = storage.put_stream(split_key, partition.file, upload.content_type)
                delete_on_rollback(self.db, split_key, storage)
                stored_objects.append(stored)
                splits.append(
                    _StoredSplit(name, stored, split_filename, partition.rows, self.split_specs[name].visibility, {})
                )
        finally:
            for partition in partitions.values():
                partition.file.close()
        return self._finish(
            version_id=version_id,
            number=number,
            fmt=fmt,
            main_key=key,
            checksum=stored_main.sha256,
            size_bytes=stored_main.size,
            row_count=inferred.row_count,
            inferred={**inferred.schema, "split_column": column},
            object_info={
                "filename": upload.filename,
                "content_type": upload.content_type,
                "size_bytes": stored_main.size,
                "split_column": column,
            },
            splits=splits,
            stored_objects=stored_objects,
            scan=scan,
        )

    def from_split_files(self, files: Mapping[str, ValidatedLabUpload], scan: ScanResult) -> DatasetVersion:
        kinds = {u.kind for u in files.values()}
        if len(kinds) != 1:
            raise ValidationFailed("All split files must have the same format")
        kind = next(iter(kinds))
        if kind not in DATASET_KINDS:
            raise ValidationFailed("These files cannot be used as a dataset")
        fmt = self._format_for(kind)
        inferred_by_split: dict[str, InferredSchema] = {
            name: infer_schema(kind, upload.open(), upload.size) for name, upload in files.items()
        }
        number, version_id, prefix = self._allocate()
        splits: list[_StoredSplit] = []
        for name in sorted(files):
            upload = files[name]
            key = self._key(prefix, "splits", name, upload.filename)
            stored = _store_upload(self.db, key, upload)
            spec = self.split_specs.get(name) or SplitSpec()
            inferred = inferred_by_split[name]
            splits.append(
                _StoredSplit(name, stored, upload.filename, inferred.row_count, spec.visibility, inferred.schema)
            )
        manifest = {
            "dataset_id": str(self.dataset.id),
            "version": number,
            "format": fmt,
            "splits": {
                s.name: {
                    "sha256": s.stored.sha256,
                    "size_bytes": s.stored.size,
                    "rows": s.rows,
                    "filename": s.upload_filename,
                    "visibility": s.visibility,
                }
                for s in splits
            },
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        storage = get_storage()
        manifest_key = self._key(prefix, "manifest.json")
        stored_manifest = storage.put_bytes(manifest_key, manifest_bytes, "application/json")
        delete_on_rollback(self.db, manifest_key, storage)
        first = splits[0]
        columns = [c.get("name") for c in first.schema.get("columns", [])]
        consistent = all([c.get("name") for c in s.schema.get("columns", [])] == columns for s in splits)
        rows = [s.rows for s in splits]
        return self._finish(
            version_id=version_id,
            number=number,
            fmt=fmt,
            main_key=manifest_key,
            checksum=hashlib.sha256(manifest_bytes).hexdigest(),
            size_bytes=sum(s.stored.size for s in splits),
            row_count=sum(r for r in rows if r is not None) if all(r is not None for r in rows) else None,
            inferred={**first.schema, "splits_consistent": consistent},
            object_info={
                "filename": "manifest.json",
                "content_type": "application/json",
                "size_bytes": len(manifest_bytes),
            },
            splits=splits,
            stored_objects=[stored_manifest, *(s.stored for s in splits)],
            scan=scan,
        )

    def from_artifact(self, artifact_version_id: uuid.UUID | str) -> DatasetVersion:
        db, actor = self.db, self.actor
        source_version = get_owned(db, ArtifactVersion, artifact_version_id, actor, label="Artifact version")
        load_project(db, actor, source_version.project_id, "artifact:download")
        if source_version.project_id != self.project.id:
            raise ValidationFailed("The artifact must belong to the dataset's project")
        artifact = db.get(Artifact, source_version.artifact_id)
        if artifact is None or artifact.deleted_at is not None:
            raise NotFound("Artifact version not found")
        if source_version.scan_status == "infected":
            raise ArtifactQuarantined()
        ftype = file_type_for(source_version.original_filename or "")
        if ftype is None or ftype.kind not in DATASET_KINDS:
            raise ValidationFailed("This artifact's file type cannot be used as a dataset")
        fmt = self._format_for(ftype.kind)
        filename = source_version.original_filename or "dataset"
        number, version_id, prefix = self._allocate()
        key = self._key(prefix, filename)
        storage = get_storage()
        copied = storage.copy(source_version.storage_key, key)
        delete_on_rollback(db, key, storage)
        if copied.size != source_version.size_bytes:
            raise ArtifactIntegrityError("Copied object size does not match the artifact version")
        inferred = self._infer_stored(key, ftype.kind, source_version.size_bytes, source_version.checksum)
        stored = StoredObject(
            key=key, size=copied.size, sha256=source_version.checksum, content_type=ftype.canonical_type
        )
        metadata_source = {"artifact_id": str(artifact.id), "artifact_version_id": str(source_version.id)}
        self.metadata.setdefault("derived_from", metadata_source)
        return self._finish(
            version_id=version_id,
            number=number,
            fmt=fmt,
            main_key=key,
            checksum=source_version.checksum,
            size_bytes=copied.size,
            row_count=inferred.row_count,
            inferred=inferred.schema,
            object_info={"filename": filename, "content_type": ftype.canonical_type, "size_bytes": copied.size},
            splits=[],
            stored_objects=[stored],
            scan=ScanResult(status=_scan_status_of(source_version.scan_status), scanner="artifact"),
        )

    def _infer_stored(self, key: str, kind: str, size: int, checksum: str) -> InferredSchema:
        """Infer from a stored object: spool + verify when small enough, else sample the head only."""
        storage = get_storage()
        if size <= INLINE_PROFILE_LIMIT:
            digest = hashlib.sha256()
            with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b") as spool:
                for chunk in storage.open_stream(key):
                    digest.update(chunk)
                    spool.write(chunk)
                if digest.hexdigest() != checksum:
                    raise ArtifactIntegrityError("Artifact bytes failed the integrity check")
                spool.seek(0)
                return infer_schema(kind, cast(BinaryIO, spool), size)
        reader = stream_reader(storage.open_stream(key))
        try:
            if kind in ("csv", "tsv"):
                return infer_delimited(reader, delimiter="," if kind == "csv" else "\t", count_rows=False)
            if kind == "jsonl":
                return infer_jsonl(reader, count_rows=False)
            if kind == "npy":
                return infer_npy(reader)
        finally:
            reader.close()
        fmt = KIND_TO_FORMAT.get(kind, "binary")
        return InferredSchema(fmt, {"format": fmt, "note": "computed asynchronously for large versions"}, None)


def _scan_status_of(status: str) -> Any:
    return status if status in _SCAN_SEVERITY else "not_scanned"


def get_dataset_version(db: Session, actor: Actor, version_id: uuid.UUID | str) -> DatasetVersion:
    version = get_owned(db, DatasetVersion, version_id, actor, label="Dataset version")
    load_project(db, actor, version.project_id, "dataset:read")
    return version


def list_dataset_versions(
    db: Session, actor: Actor, dataset_id: uuid.UUID | str, params: PageParams, *, mapper: Any = None
) -> Page[Any]:
    dataset = get_dataset(db, actor, dataset_id)
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version.desc())
    return paginate(db, stmt, params, mapper or (lambda v: v))


def dataset_lineage(
    db: Session, actor: Actor, version_id: uuid.UUID | str, *, max_depth: int = MAX_LINEAGE_DEPTH
) -> tuple[list[DatasetVersion], bool]:
    """The version and its ancestors (nearest first); ``truncated`` when the chain exceeds ``max_depth``."""
    version = get_dataset_version(db, actor, version_id)
    chain = [version]
    seen = {version.id}
    current = version
    while current.parent_version_id is not None and len(chain) < max_depth:
        parent = db.get(DatasetVersion, current.parent_version_id)
        if parent is None or parent.id in seen or parent.organization_id != actor.organization_id:
            break
        chain.append(parent)
        seen.add(parent.id)
        current = parent
    truncated = current.parent_version_id is not None and len(chain) >= max_depth
    return chain, truncated


# -- reading data ----------------------------------------------------------------------------------
def resolve_dataset_object(
    db: Session,
    actor: Actor,
    version_id: uuid.UUID | str,
    split: str | None = None,
    *,
    for_evaluator: bool = False,
) -> DownloadTarget:
    """Authorize access to a version's main object or one split and describe it.

    ``evaluator_only`` splits (and the full object of a version that contains them) require
    ``for_evaluator=True``, which in turn requires ``evaluation:run`` and is never granted to agents.
    """
    version = get_owned(db, DatasetVersion, version_id, actor, label="Dataset version")
    permissions = ("dataset:read", "evaluation:run") if for_evaluator else ("dataset:read",)
    load_project(db, actor, version.project_id, *permissions)
    if for_evaluator and actor.kind == "agent":
        raise EvaluatorOnlyData("Held-out evaluator data is never exposed to agents")
    splits: dict[str, Any] = version.splits or {}
    if split is None:
        if not for_evaluator and any(s.get("visibility") == "evaluator_only" for s in splits.values()):
            raise EvaluatorOnlyData(
                "This dataset version contains evaluator-only splits; request an experiment split instead"
            )
        info = (version.dataset_metadata or {}).get("object", {})
        return DownloadTarget(
            key=version.storage_key,
            filename=str(info.get("filename") or f"dataset-v{version.version}"),
            content_type=str(info.get("content_type") or "application/octet-stream"),
            size_bytes=int(info.get("size_bytes") or version.size_bytes),
            checksum=version.checksum,
        )
    spec = splits.get(split)
    if not isinstance(spec, dict):
        raise NotFound("Split not found")
    if spec.get("visibility") == "evaluator_only" and not for_evaluator:
        raise EvaluatorOnlyData("This split is reserved for evaluators")
    ftype = file_type_for(str(spec.get("filename") or ""))
    return DownloadTarget(
        key=str(spec["storage_key"]),
        filename=str(spec.get("filename") or f"{split}"),
        content_type=ftype.canonical_type if ftype else "application/octet-stream",
        size_bytes=int(spec.get("size_bytes") or 0),
        checksum=str(spec.get("checksum") or ""),
    )


def open_dataset_split(
    db: Session,
    actor: Actor,
    version_id: uuid.UUID | str,
    split: str | None = None,
    *,
    for_evaluator: bool = False,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    """Stream a dataset version (or one split). Evaluator-only data is refused unless ``for_evaluator``."""
    target = resolve_dataset_object(db, actor, version_id, split, for_evaluator=for_evaluator)
    return get_storage().open_stream(target.key, chunk_size)


def read_dataset_bytes(
    db: Session,
    actor: Actor,
    version_id: uuid.UUID | str,
    max_bytes: int,
    split: str | None = None,
    *,
    for_evaluator: bool = False,
) -> bytes:
    target = resolve_dataset_object(db, actor, version_id, split, for_evaluator=for_evaluator)
    if target.size_bytes > max_bytes:
        raise PayloadTooLarge(f"Dataset object is {target.size_bytes} bytes, above the {max_bytes} byte limit")
    return get_storage().get_bytes(target.key, max_bytes)


def audit_dataset_download(db: Session, actor: Actor, version_id: uuid.UUID | str, split: str | None) -> None:
    audit(db, actor, DATASET_DOWNLOADED, "dataset_version", version_id, after={"split": split})
