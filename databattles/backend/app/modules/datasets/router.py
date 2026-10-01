from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from pydantic import Field

from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.pagination import Page, PageParams, make_page
from app.core.schemas import Message, OrgMini, Schema, UserMini
from app.models.dataset import LICENSES
from app.modules.datasets import service

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetCard(Schema):
    id: uuid.UUID
    slug: str
    title: str
    subtitle: str | None
    license: str
    license_name: str
    tags: list[str]
    visibility: str
    owner: UserMini | None
    owner_org: OrgMini | None
    latest_version: int | None
    total_bytes: int
    file_count: int
    updated_at: datetime
    download_count: int
    status: str
    is_demo: bool


class DatasetVersionOut(Schema):
    id: uuid.UUID
    version: int
    status: str
    release_notes_html: str
    release_notes_md: str
    data_dictionary: list[dict[str, Any]]
    file_count: int
    total_bytes: int
    published_at: datetime | None
    archived_at: datetime | None
    created_at: datetime


class DatasetFileOut(Schema):
    id: uuid.UUID
    filename: str
    size_bytes: int
    sha256: str
    content_type: str
    kind: str
    row_count: int | None
    has_preview: bool
    scan_status: str
    created_at: datetime


class CompetitionLink(Schema):
    slug: str
    title: str


class DatasetDetail(DatasetCard):
    description_html: str
    description_md: str | None
    citation: str | None
    attribution: str | None
    source_url: str | None
    requires_terms: bool
    terms_html: str | None
    terms_md: str | None = None  # managers only
    terms_accepted: bool
    takedown_reason: str | None
    versions: list[DatasetVersionOut]
    current_version: DatasetVersionOut | None
    files: list[DatasetFileOut]
    used_by: list[CompetitionLink]
    downloads_30d: int
    can_manage: bool


class DatasetWrite(Schema):
    title: str | None = Field(default=None, min_length=3, max_length=140)
    subtitle: str | None = Field(default=None, max_length=200)
    description_md: str | None = Field(default=None, max_length=100_000)
    license: str | None = None
    citation: str | None = Field(default=None, max_length=2000)
    attribution: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None
    visibility: str | None = Field(default=None, pattern="^(public|org|private)$")
    requires_terms: bool | None = None
    terms_md: str | None = Field(default=None, max_length=20_000)
    owner_org_id: uuid.UUID | None = None


class VersionIn(Schema):
    release_notes_md: str | None = Field(default=None, max_length=20_000)
    data_dictionary: list[dict[str, Any]] | None = None


class PreviewOut(Schema):
    file_id: uuid.UUID
    filename: str
    preview: dict[str, Any] | None
    row_count: int | None


class DownloadOut(Schema):
    url: str
    expires_in_seconds: int


class ReasonIn(Schema):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/licenses")
def licenses() -> dict[str, str]:
    return LICENSES


@router.get("", response_model=Page[DatasetCard])
def list_datasets(params: PageParams = Depends(), q: str | None = Query(None, max_length=100), tag: str | None = Query(None, max_length=48),
                  license: str | None = Query(None, max_length=32), owner: str | None = Query(None, pattern="^me$"),
                  sort: str = Query("updated", pattern="^(updated|downloads|title)$"), actor: Actor = Depends(get_actor)) -> dict:
    items, total = service.list_datasets(actor, params, q=q, tag=tag, license=license, owner=owner, sort=sort)
    return make_page(items, total, params)


@router.post("", response_model=DatasetDetail, status_code=201)
def create(data: DatasetWrite, actor: Actor = Depends(require_verified_user)) -> dict:
    if not data.title:
        from app.core.errors import ValidationFailed

        raise ValidationFailed(details={"fields": {"title": "Enter a title."}})
    ds = service.create_dataset(actor, data.model_dump(exclude_unset=True))
    return service.detail(actor, ds)


@router.get("/{slug}", response_model=DatasetDetail)
def detail(slug: str, version: int | None = Query(None, ge=1), actor: Actor = Depends(get_actor)) -> dict:
    return service.detail(actor, service.load_visible(actor, slug), version)


@router.patch("/{slug}", response_model=DatasetDetail)
def update(slug: str, data: DatasetWrite, actor: Actor = Depends(require_user)) -> dict:
    ds = service.load_managed(actor, slug)
    service.update_dataset(actor, ds, data.model_dump(exclude_unset=True, exclude={"owner_org_id"}))
    return service.detail(actor, ds)


@router.post("/{slug}/versions", response_model=DatasetVersionOut, status_code=201)
def new_version(slug: str, data: VersionIn, actor: Actor = Depends(require_user)) -> dict:
    v = service.create_version(actor, service.load_managed(actor, slug), data.release_notes_md)
    return service._version_out(v)


@router.patch("/{slug}/versions/{version}", response_model=DatasetVersionOut)
def update_version(slug: str, version: int, data: VersionIn, actor: Actor = Depends(require_user)) -> dict:
    ds = service.load_managed(actor, slug)
    v = service.update_version(actor, ds, service.get_version(actor.db, ds, version), data.release_notes_md, data.data_dictionary)
    return service._version_out(v)


@router.post("/{slug}/versions/{version}/files", response_model=DatasetFileOut, status_code=201)
def upload(slug: str, version: int, file: UploadFile = File(...), kind: str = Form("data"),
           idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=80),
           actor: Actor = Depends(require_user)) -> dict:
    ds = service.load_managed(actor, slug)
    f = service.upload_file(actor, ds, service.get_version(actor.db, ds, version), filename=file.filename or "", stream=file.file,
                            kind=kind, idempotency_key=idempotency_key)
    return service._file_out(f)


@router.delete("/{slug}/versions/{version}/files/{file_id}", response_model=Message)
def delete_file(slug: str, version: int, file_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    ds = service.load_managed(actor, slug)
    service.delete_file(actor, ds, service.get_version(actor.db, ds, version), file_id)
    return Message(message="File removed.")


@router.post("/{slug}/versions/{version}/publish", response_model=DatasetVersionOut)
def publish_version(slug: str, version: int, actor: Actor = Depends(require_user)) -> dict:
    ds = service.load_managed(actor, slug)
    return service._version_out(service.publish_version(actor, ds, service.get_version(actor.db, ds, version)))


@router.post("/{slug}/versions/{version}/archive", response_model=DatasetVersionOut)
def archive_version(slug: str, version: int, actor: Actor = Depends(require_user)) -> dict:
    ds = service.load_managed(actor, slug)
    return service._version_out(service.archive_version(actor, ds, service.get_version(actor.db, ds, version)))


@router.post("/{slug}/accept-terms", response_model=Message)
def accept_terms(slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.accept_terms(actor, service.load_visible(actor, slug))
    return Message(message="Terms accepted.")


@router.post("/{slug}/files/{file_id}/download", response_model=DownloadOut)
def download(slug: str, file_id: uuid.UUID, actor: Actor = Depends(get_actor)) -> DownloadOut:
    from app.core.config import settings

    url = service.download_url(actor, service.load_visible(actor, slug), file_id)
    return DownloadOut(url=url, expires_in_seconds=settings.SIGNED_URL_TTL_SECONDS)


@router.get("/{slug}/files/{file_id}/preview", response_model=PreviewOut)
def preview(slug: str, file_id: uuid.UUID, actor: Actor = Depends(get_actor)) -> dict:
    return service.file_preview(actor, service.load_visible(actor, slug), file_id)


@router.post("/{slug}/takedown", response_model=Message)
def takedown(slug: str, data: ReasonIn, restore: bool = Query(False), actor: Actor = Depends(require_user)) -> Message:
    service.takedown(actor, service.load_visible(actor, slug), data.reason, restore)
    return Message(message="Dataset restored." if restore else "Dataset taken down.")
