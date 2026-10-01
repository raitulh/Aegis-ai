"""Dataset hub: metadata, immutable versions, files in object storage, previews, protected downloads."""

from __future__ import annotations

import csv
import io
import os
import tempfile
import uuid
from datetime import date, timedelta
from typing import Any, BinaryIO

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, PayloadTooLarge, ValidationFailed
from app.core.markdown import render_markdown
from app.core.pagination import PageParams
from app.core.permissions import can_manage_dataset, can_view_dataset, listable_datasets_clause
from app.core.schemas import org_mini, user_mini
from app.core.security import sha256_hex
from app.core.slugs import unique_slug
from app.core.time import utcnow
from app.core.validators import (
    check_dataset_extension,
    clean_tags,
    extension,
    looks_like_text,
    safe_filename,
    validate_external_url,
    validate_zip_archive,
)
from app.integrations.scanner import get_scanner
from app.models.competition import Competition
from app.models.dataset import (
    LICENSES,
    Dataset,
    DatasetDownloadStat,
    DatasetFile,
    DatasetTermsAcceptance,
    DatasetVersion,
)
from app.models.enums import ContentStatus, ContentVisibility, Lifecycle, VersionStatus
from app.models.org import Organization
from app.models.user import RecentView, User
from app.modules.search.indexer import index_dataset
from app.storage import get_storage, signed_download_url
from app.storage.base import PREFIX_DATASETS, new_key

PREVIEW_ROWS = 20
PREVIEW_COLS = 30
PREVIEW_CELL = 200
CONTENT_TYPES = {".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json",
                 ".jsonl": "application/x-ndjson", ".parquet": "application/vnd.apache.parquet", ".txt": "text/plain",
                 ".md": "text/markdown", ".zip": "application/zip", ".ipynb": "application/x-ipynb+json",
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def get_by_slug(db: Session, slug: str) -> Dataset | None:
    return db.scalar(select(Dataset).where(Dataset.slug == slug))


def load_visible(actor: Actor, slug: str) -> Dataset:
    ds = get_by_slug(actor.db, slug)
    if ds is None or not can_view_dataset(actor, ds):
        raise NotFound("Dataset not found.")
    return ds


def load_managed(actor: Actor, slug: str) -> Dataset:
    ds = load_visible(actor, slug)
    if not can_manage_dataset(actor, ds):
        raise Forbidden("Only the dataset owner can do that.")
    return ds


def card(db: Session, ds: Dataset, versions: dict[uuid.UUID, DatasetVersion], owners: dict[uuid.UUID, Any],
         orgs: dict[uuid.UUID, Organization]) -> dict[str, Any]:
    v = versions.get(ds.latest_version_id) if ds.latest_version_id else None
    return {
        "id": ds.id, "slug": ds.slug, "title": ds.title, "subtitle": ds.subtitle, "license": ds.license,
        "license_name": LICENSES.get(ds.license, ds.license), "tags": list(ds.tags or []), "visibility": ds.visibility,
        "owner": user_mini(owners.get(ds.owner_user_id)) if ds.owner_user_id else None,
        "owner_org": org_mini(orgs.get(ds.owner_org_id)) if ds.owner_org_id else None,
        "latest_version": v.version if v else None, "total_bytes": v.total_bytes if v else 0, "file_count": v.file_count if v else 0,
        "updated_at": ds.updated_at, "download_count": ds.download_count, "status": ds.status, "is_demo": ds.is_demo,
    }


def build_cards(db: Session, items: list[Dataset]) -> list[dict[str, Any]]:
    vids = [d.latest_version_id for d in items if d.latest_version_id]
    versions = {v.id: v for v in db.scalars(select(DatasetVersion).where(DatasetVersion.id.in_(vids)))} if vids else {}
    uids = [d.owner_user_id for d in items if d.owner_user_id]
    owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids)))} if uids else {}
    oids = [d.owner_org_id for d in items if d.owner_org_id]
    orgs = {o.id: o for o in db.scalars(select(Organization).where(Organization.id.in_(oids)))} if oids else {}
    return [card(db, d, versions, owners, orgs) for d in items]


def list_datasets(actor: Actor, params: PageParams, *, q: str | None, tag: str | None, license: str | None,
                  owner: str | None, sort: str) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = select(Dataset).where(listable_datasets_clause(actor), Dataset.latest_version_id.is_not(None))
    if owner == "me" and actor.is_authenticated:
        stmt = select(Dataset).where(or_(Dataset.owner_user_id == actor.id,
                                         Dataset.owner_org_id.in_([o for o, m in actor.memberships().items()
                                                                   if m.role in ("owner", "admin", "manager")])))
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Dataset.title.ilike(like), Dataset.subtitle.ilike(like)))
    if tag:
        stmt = stmt.where(Dataset.tags.any(tag.lower()))
    if license:
        stmt = stmt.where(Dataset.license == license)
    order = {"downloads": Dataset.download_count.desc(), "title": Dataset.title.asc()}.get(sort, Dataset.updated_at.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(order, Dataset.id).limit(params.page_size).offset(params.offset)))
    return build_cards(db, items), total


def _version_out(v: DatasetVersion) -> dict[str, Any]:
    return {"id": v.id, "version": v.version, "status": v.status, "release_notes_html": v.release_notes_html,
            "release_notes_md": v.release_notes_md, "data_dictionary": v.data_dictionary, "file_count": v.file_count,
            "total_bytes": v.total_bytes, "published_at": v.published_at, "archived_at": v.archived_at, "created_at": v.created_at}


def _file_out(f: DatasetFile) -> dict[str, Any]:
    return {"id": f.id, "filename": f.filename, "size_bytes": f.size_bytes, "sha256": f.sha256, "content_type": f.content_type,
            "kind": f.kind, "row_count": f.row_count, "has_preview": bool(f.preview), "scan_status": f.scan_status,
            "created_at": f.created_at}


def detail(actor: Actor, ds: Dataset, version_number: int | None = None) -> dict[str, Any]:
    db = actor.db
    manager = can_manage_dataset(actor, ds)
    vstmt = select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
    if not manager:
        vstmt = vstmt.where(DatasetVersion.status != VersionStatus.draft)
    versions = list(db.scalars(vstmt.order_by(DatasetVersion.version.desc())))
    current = next((v for v in versions if v.version == version_number), None) if version_number else None
    current = current or next((v for v in versions if v.id == ds.latest_version_id), None) or (versions[0] if versions else None)
    files = list(db.scalars(select(DatasetFile).where(DatasetFile.version_id == current.id).order_by(DatasetFile.filename))) if current else []
    used_by = db.scalars(select(Competition).where(Competition.dataset_version_id.in_([v.id for v in versions]),
                                                   Competition.lifecycle != Lifecycle.draft)).all() if versions else []
    from app.core.permissions import can_view_competition

    accepted = False
    if actor.is_authenticated and ds.requires_terms:
        row = db.scalar(select(DatasetTermsAcceptance).where(DatasetTermsAcceptance.dataset_id == ds.id,
                                                             DatasetTermsAcceptance.user_id == actor.id))
        accepted = row is not None and row.terms_hash == sha256_hex(ds.terms_md or "")
    since = date.today() - timedelta(days=30)
    downloads_30d = db.scalar(select(func.coalesce(func.sum(DatasetDownloadStat.count), 0)).where(
        DatasetDownloadStat.dataset_id == ds.id, DatasetDownloadStat.day >= since)) or 0
    if actor.is_authenticated:
        db.execute(insert(RecentView).values(user_id=actor.id, entity_type="dataset", entity_id=ds.id, viewed_at=utcnow())
                   .on_conflict_do_update(index_elements=["user_id", "entity_type", "entity_id"], set_={"viewed_at": utcnow()}))
        db.commit()
    return {
        **build_cards(db, [ds])[0],
        "description_html": ds.description_html, "description_md": ds.description_md if manager else None,
        "citation": ds.citation, "attribution": ds.attribution, "source_url": ds.source_url,
        "requires_terms": ds.requires_terms, "terms_html": ds.terms_html, "terms_accepted": accepted,
        "terms_md": ds.terms_md if manager else None,
        "takedown_reason": ds.takedown_reason if ds.status == ContentStatus.taken_down else None,
        "versions": [_version_out(v) for v in versions], "current_version": _version_out(current) if current else None,
        "files": [_file_out(f) for f in files],
        "used_by": [{"slug": c.slug, "title": c.title} for c in used_by if can_view_competition(actor, c)],
        "downloads_30d": int(downloads_30d), "can_manage": manager,
    }


def create_dataset(actor: Actor, data: dict[str, Any]) -> Dataset:
    db = actor.db
    org_id = data.get("owner_org_id")
    if org_id and not actor.is_org_manager(org_id):
        raise Forbidden("You can only publish datasets for organizations you manage.")
    lic = data.get("license") or "cc-by-4.0"
    if lic not in LICENSES:
        raise ValidationFailed(details={"fields": {"license": "Choose a license from the list."}})
    ds = Dataset(slug=unique_slug(db, Dataset, data["title"]), title=data["title"], subtitle=data.get("subtitle"),
                 owner_user_id=None if org_id else actor.id, owner_org_id=org_id, license=lic,
                 visibility=ContentVisibility(data.get("visibility") or "public").value)
    _apply(ds, data)
    db.add(ds)
    db.flush()
    db.add(DatasetVersion(dataset_id=ds.id, version=1, status=VersionStatus.draft, created_by=actor.id))
    record_audit(db, actor.id, "dataset.create", target_type="dataset", target_id=ds.id, org_id=org_id)
    db.commit()
    return ds


def _apply(ds: Dataset, data: dict[str, Any]) -> None:
    for key in ("title", "subtitle", "citation", "attribution"):
        if key in data and data[key] is not None:
            setattr(ds, key, data[key])
    if "description_md" in data and data["description_md"] is not None:
        ds.description_md = data["description_md"]
        ds.description_html = render_markdown(ds.description_md)
    if "terms_md" in data and data["terms_md"] is not None:
        ds.terms_md = data["terms_md"]
        ds.terms_html = render_markdown(ds.terms_md)
    if "requires_terms" in data and data["requires_terms"] is not None:
        ds.requires_terms = bool(data["requires_terms"])
    if "tags" in data and data["tags"] is not None:
        ds.tags = clean_tags(data["tags"])
    if "source_url" in data:
        ds.source_url = validate_external_url(data["source_url"], "source_url")
    if data.get("license"):
        if data["license"] not in LICENSES:
            raise ValidationFailed(details={"fields": {"license": "Choose a license from the list."}})
        ds.license = data["license"]
    if data.get("visibility"):
        ds.visibility = ContentVisibility(data["visibility"]).value
    if ds.requires_terms and not (ds.terms_md or "").strip():
        raise ValidationFailed(details={"fields": {"terms_md": "Provide the terms participants must accept."}})


def update_dataset(actor: Actor, ds: Dataset, data: dict[str, Any]) -> Dataset:
    _apply(ds, data)
    record_audit(actor.db, actor.id, "dataset.update", target_type="dataset", target_id=ds.id, meta={"fields": sorted(data)})
    index_dataset(actor.db, ds)
    actor.db.commit()
    return ds


def get_version(db: Session, ds: Dataset, version: int) -> DatasetVersion:
    v = db.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.version == version))
    if v is None:
        raise NotFound("Version not found.")
    return v


def create_version(actor: Actor, ds: Dataset, release_notes_md: str | None) -> DatasetVersion:
    db = actor.db
    if db.scalar(select(DatasetVersion.id).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.status == VersionStatus.draft)):
        raise Conflict("Publish or delete the existing draft version first.", code="draft_exists")
    latest = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == ds.id)) or 0
    v = DatasetVersion(dataset_id=ds.id, version=latest + 1, status=VersionStatus.draft, created_by=actor.id,
                       release_notes_md=release_notes_md or "", release_notes_html=render_markdown(release_notes_md or ""))
    db.add(v)
    db.commit()
    return v


def update_version(actor: Actor, ds: Dataset, v: DatasetVersion, release_notes_md: str | None,
                   data_dictionary: list[dict[str, Any]] | None) -> DatasetVersion:
    if v.status != VersionStatus.draft:
        raise Conflict("Published versions are immutable. Create a new version instead.", code="version_immutable")
    if release_notes_md is not None:
        v.release_notes_md = release_notes_md
        v.release_notes_html = render_markdown(release_notes_md)
    if data_dictionary is not None:
        v.data_dictionary = [{"column": str(d.get("column", ""))[:80], "type": str(d.get("type", ""))[:40],
                              "description": str(d.get("description", ""))[:500]} for d in data_dictionary[:200]]
    actor.db.commit()
    return v


def _owner_usage(db: Session, ds: Dataset) -> int:
    owner_clause = Dataset.owner_org_id == ds.owner_org_id if ds.owner_org_id else Dataset.owner_user_id == ds.owner_user_id
    return db.scalar(select(func.coalesce(func.sum(DatasetVersion.total_bytes), 0)).join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                     .where(owner_clause)) or 0


def _preview(path: str, ext: str) -> tuple[dict[str, Any] | None, int | None]:
    delimiter = "\t" if ext == ".tsv" else ","
    with open(path, "rb") as fh:
        head = fh.read(1024 * 1024)
    if not looks_like_text(head):
        return None, None
    text = head.decode("utf-8", errors="ignore").lstrip("﻿")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        rows.append([c[:PREVIEW_CELL] for c in row[:PREVIEW_COLS]])
        if len(rows) > PREVIEW_ROWS:
            break
    if not rows:
        return None, None
    row_count = None
    if os.path.getsize(path) <= 50 * 1024 * 1024:
        with open(path, "rb") as fh:
            row_count = max(0, sum(1 for _ in fh) - 1)
    return {"columns": rows[0], "rows": rows[1:PREVIEW_ROWS + 1], "truncated_columns": len(rows[0]) >= PREVIEW_COLS}, row_count


def upload_file(actor: Actor, ds: Dataset, v: DatasetVersion, *, filename: str, stream: BinaryIO, kind: str,
                idempotency_key: str | None) -> DatasetFile:
    db = actor.db
    if v.status != VersionStatus.draft:
        raise Conflict("Files can only be added to a draft version.", code="version_immutable")
    if idempotency_key:
        existing = db.scalar(select(DatasetFile).where(DatasetFile.version_id == v.id, DatasetFile.idempotency_key == idempotency_key))
        if existing:
            return existing
    name = safe_filename(filename)
    check_dataset_extension(name)
    if db.scalar(select(DatasetFile.id).where(DatasetFile.version_id == v.id, DatasetFile.filename == name)):
        raise Conflict("A file with that name already exists in this version.", code="duplicate_file")
    if kind not in ("data", "sample_submission", "notebook", "documentation"):
        raise ValidationFailed(details={"fields": {"kind": "Invalid file kind."}})
    ext = extension(name)
    remaining_quota = settings.DATASET_QUOTA_MB_PER_OWNER * 1024 * 1024 - _owner_usage(db, ds)
    limit = min(settings.MAX_DATASET_FILE_MB * 1024 * 1024, max(0, remaining_quota))
    if limit <= 0:
        raise PayloadTooLarge("Storage quota reached for this owner.", code="quota_exceeded")
    storage = get_storage()
    key = new_key(PREFIX_DATASETS, ext)
    stored = storage.save_stream(key, stream, max_bytes=limit, content_type=CONTENT_TYPES.get(ext, "application/octet-stream"))
    local = storage.local_path(key)
    tmp: str | None = None
    try:
        if local is None:
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as out, storage.open(key) as src:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
            local = tmp
        if ext == ".zip":
            try:
                validate_zip_archive(path=local)
            except ValidationFailed:
                storage.delete(key)
                raise
        scan = get_scanner().scan(local)
        if scan == "infected":
            storage.delete(key)
            record_audit(db, actor.id, "dataset.upload_blocked", target_type="dataset", target_id=ds.id, meta={"reason": "malware"})
            db.commit()
            raise ValidationFailed("The file failed a malware scan and was rejected.", code="malware_detected")
        preview, rows = _preview(local, ext) if ext in (".csv", ".tsv") else (None, None)
    finally:
        if tmp:
            os.unlink(tmp)
    f = DatasetFile(version_id=v.id, filename=name, storage_key=key, size_bytes=stored.size_bytes, sha256=stored.sha256,
                    content_type=CONTENT_TYPES.get(ext, "application/octet-stream"), kind=kind, preview=preview,
                    row_count=rows, scan_status=scan, idempotency_key=idempotency_key)
    db.add(f)
    v.file_count += 1
    v.total_bytes += stored.size_bytes
    record_audit(db, actor.id, "dataset.file_upload", target_type="dataset", target_id=ds.id,
                 meta={"version": v.version, "file": name, "bytes": stored.size_bytes})
    db.commit()
    return f


def delete_file(actor: Actor, ds: Dataset, v: DatasetVersion, file_id: uuid.UUID) -> None:
    db = actor.db
    if v.status != VersionStatus.draft:
        raise Conflict("Published versions are immutable.", code="version_immutable")
    f = db.get(DatasetFile, file_id)
    if f is None or f.version_id != v.id:
        raise NotFound()
    get_storage().delete(f.storage_key)
    v.file_count -= 1
    v.total_bytes -= f.size_bytes
    db.delete(f)
    db.commit()


def publish_version(actor: Actor, ds: Dataset, v: DatasetVersion) -> DatasetVersion:
    db = actor.db
    if v.status != VersionStatus.draft:
        raise Conflict("Only draft versions can be published.", code="invalid_transition")
    if v.file_count == 0:
        raise Conflict("Add at least one file before publishing.", code="version_empty")
    v.status = VersionStatus.published
    v.published_at = utcnow()
    ds.latest_version_id = v.id
    ds.updated_at = utcnow()
    record_audit(db, actor.id, "dataset.version_publish", target_type="dataset", target_id=ds.id, meta={"version": v.version})
    index_dataset(db, ds)
    db.commit()
    return v


def archive_version(actor: Actor, ds: Dataset, v: DatasetVersion) -> DatasetVersion:
    """Archived versions stay downloadable so competitions that reference them keep working."""
    db = actor.db
    if v.status != VersionStatus.published:
        raise Conflict("Only published versions can be archived.", code="invalid_transition")
    v.status = VersionStatus.archived
    v.archived_at = utcnow()
    if ds.latest_version_id == v.id:
        newest = db.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.status == VersionStatus.published,
                                                        DatasetVersion.id != v.id).order_by(DatasetVersion.version.desc()).limit(1))
        ds.latest_version_id = newest.id if newest else v.id
    record_audit(db, actor.id, "dataset.version_archive", target_type="dataset", target_id=ds.id, meta={"version": v.version})
    db.commit()
    return v


def accept_terms(actor: Actor, ds: Dataset) -> None:
    db = actor.db
    h = sha256_hex(ds.terms_md or "")
    db.execute(insert(DatasetTermsAcceptance).values(dataset_id=ds.id, user_id=actor.id, terms_hash=h)
               .on_conflict_do_update(index_elements=["dataset_id", "user_id"], set_={"terms_hash": h, "accepted_at": utcnow()}))
    db.commit()


def download_url(actor: Actor, ds: Dataset, file_id: uuid.UUID) -> str:
    db = actor.db
    f = db.get(DatasetFile, file_id)
    if f is None:
        raise NotFound()
    v = db.get(DatasetVersion, f.version_id)
    if v is None or v.dataset_id != ds.id:
        raise NotFound()
    manager = can_manage_dataset(actor, ds)
    if v.status == VersionStatus.draft and not manager:
        raise NotFound()
    if ds.requires_terms and not manager:
        if not actor.is_authenticated:
            raise Forbidden("Sign in and accept the dataset terms to download.", code="terms_required")
        row = db.scalar(select(DatasetTermsAcceptance).where(DatasetTermsAcceptance.dataset_id == ds.id,
                                                             DatasetTermsAcceptance.user_id == actor.id))
        if row is None or row.terms_hash != sha256_hex(ds.terms_md or ""):
            raise Forbidden("Accept the dataset terms to download.", code="terms_required")
    if f.scan_status == "infected":
        raise NotFound()
    db.execute(insert(DatasetDownloadStat).values(dataset_id=ds.id, day=date.today(), count=1)
               .on_conflict_do_update(index_elements=["dataset_id", "day"], set_={"count": DatasetDownloadStat.count + 1}))
    ds.download_count += 1
    db.commit()
    return signed_download_url(f.storage_key, f.filename, f.content_type)


def file_preview(actor: Actor, ds: Dataset, file_id: uuid.UUID) -> dict[str, Any]:
    f = actor.db.get(DatasetFile, file_id)
    if f is None:
        raise NotFound()
    v = actor.db.get(DatasetVersion, f.version_id)
    if v is None or v.dataset_id != ds.id or (v.status == VersionStatus.draft and not can_manage_dataset(actor, ds)):
        raise NotFound()
    return {"file_id": f.id, "filename": f.filename, "preview": f.preview, "row_count": f.row_count}


def takedown(actor: Actor, ds: Dataset, reason: str, restore: bool = False) -> Dataset:
    if not actor.is_moderator:
        raise Forbidden()
    ds.status = ContentStatus.active if restore else ContentStatus.taken_down
    ds.takedown_reason = None if restore else reason
    record_audit(actor.db, actor.id, "moderation.dataset_restore" if restore else "moderation.dataset_takedown",
                 target_type="dataset", target_id=ds.id, reason=reason)
    index_dataset(actor.db, ds)
    actor.db.commit()
    return ds
