from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import Field

from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.images import store_image
from app.core.pagination import Page, PageParams, make_page
from app.core.rate_limit import enforce
from app.core.schemas import Message, Schema, UserMini
from app.modules.projects import service

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCard(Schema):
    id: uuid.UUID
    slug: str
    title: str
    summary: str
    tags: list[str]
    technologies: list[str]
    owner: UserMini | None
    is_featured: bool
    is_open_source: bool
    repo_url: str | None
    demo_url: str | None
    cover_style: str
    cover_image_url: str | None
    visibility: str
    status: str
    maintainer_verified: bool
    updated_at: datetime
    is_demo: bool


class ProjectWrite(Schema):
    title: str | None = Field(default=None, max_length=140)
    slug: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=280)
    description_md: str | None = Field(default=None, max_length=100_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    technologies: list[str] | None = Field(default=None, max_length=30)
    repo_url: str | None = Field(default=None, max_length=500)
    demo_url: str | None = Field(default=None, max_length=500)
    paper_url: str | None = Field(default=None, max_length=500)
    video_url: str | None = Field(default=None, max_length=500)
    docs_url: str | None = Field(default=None, max_length=500)
    visibility: str | None = Field(default=None, pattern="^(public|unlisted|private)$")
    status: str | None = Field(default=None, pattern="^(draft|active|archived)$")
    is_open_source: bool | None = None
    cover_style: str | None = Field(default=None, max_length=32)
    org_id: uuid.UUID | None = None
    competition_slug: str | None = Field(default=None, max_length=80)
    dataset_slugs: list[str] | None = Field(default=None, max_length=10)


class MemberIn(Schema):
    handle: str = Field(max_length=31)
    role: str = Field(pattern="^(maintainer|contributor)$")


class TransferIn(Schema):
    user_id: uuid.UUID


class MediaPatch(Schema):
    alt_text: str | None = Field(default=None, max_length=200)
    position: int | None = Field(default=None, ge=0, le=20)


class ReasonIn(Schema):
    reason: str = Field(min_length=3, max_length=500)


@router.get("", response_model=Page[ProjectCard])
def list_projects(params: PageParams = Depends(), q: str | None = Query(None, max_length=80), tag: str | None = Query(None, max_length=48),
                  tech: str | None = Query(None, max_length=48), open_source: bool | None = None, featured: bool | None = None,
                  org: str | None = Query(None, max_length=80), owner: str | None = Query(None, pattern="^me$"),
                  sort: str = Query("updated", pattern="^(updated|new|title)$"), actor: Actor = Depends(get_actor)) -> dict:
    items, total = service.list_projects(actor, params, q=q, tag=tag, tech=tech, open_source=open_source, featured=featured,
                                         org=org, owner=owner, sort=sort)
    return make_page(items, total, params)


@router.post("", status_code=201)
def create_project(data: ProjectWrite, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    enforce("project_create", str(actor.id), limit=20, window_seconds=86400)
    p = service.create_project(actor, data.model_dump(exclude_unset=True))
    return service.detail(actor, p)


@router.get("/{slug}")
def project_detail(slug: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.detail(actor, service.load_visible(actor, slug))


@router.patch("/{slug}")
def update_project(slug: str, data: ProjectWrite, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    p = service.load_editable(actor, slug)
    return service.detail(actor, service.update_project(actor, p, data.model_dump(exclude_unset=True, exclude={"slug"})))


@router.delete("/{slug}", response_model=Message)
def delete_project(slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.delete_project(actor, service.load_visible(actor, slug))
    return Message(message="Project deleted.")


@router.post("/{slug}/members", response_model=Message, status_code=201)
def add_member(slug: str, data: MemberIn, actor: Actor = Depends(require_user)) -> Message:
    service.add_member(actor, service.load_visible(actor, slug), data.handle, data.role)
    return Message(message="Member added.")


@router.delete("/{slug}/members/{user_id}", response_model=Message)
def remove_member(slug: str, user_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.remove_member(actor, service.load_visible(actor, slug), user_id)
    return Message(message="Member removed.")


@router.post("/{slug}/transfer", response_model=Message)
def transfer(slug: str, data: TransferIn, actor: Actor = Depends(require_user)) -> Message:
    service.transfer_ownership(actor, service.load_visible(actor, slug), data.user_id)
    return Message(message="Ownership transferred.")


@router.post("/{slug}/media", status_code=201)
def upload_media(slug: str, file: UploadFile = File(...), alt_text: str = Form("", max_length=200),
                 actor: Actor = Depends(require_user)) -> dict[str, Any]:
    p = service.load_editable(actor, slug)
    enforce("project_media", str(actor.id), limit=30, window_seconds=3600)
    key, url, w, h = store_image(file.file, max_side=1920)
    m = service.add_media(actor, p, key, w, h, alt_text)
    return {"id": m.id, "url": url, "width": w, "height": h, "alt_text": m.alt_text, "position": m.position}


@router.patch("/{slug}/media/{media_id}", response_model=Message)
def update_media(slug: str, media_id: uuid.UUID, data: MediaPatch, actor: Actor = Depends(require_user)) -> Message:
    service.update_media(actor, service.load_editable(actor, slug), media_id, data.alt_text, data.position)
    return Message(message="Image updated.")


@router.delete("/{slug}/media/{media_id}", response_model=Message)
def delete_media(slug: str, media_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.delete_media(actor, service.load_editable(actor, slug), media_id)
    return Message(message="Image removed.")


@router.post("/{slug}/feature", response_model=Message)
def feature(slug: str, featured: bool = Query(True), actor: Actor = Depends(require_user)) -> Message:
    service.set_featured(actor, service.load_visible(actor, slug), featured)
    return Message(message="Project featured." if featured else "Project unfeatured.")


@router.post("/{slug}/takedown", response_model=Message)
def takedown(slug: str, data: ReasonIn, restore: bool = Query(False), actor: Actor = Depends(require_user)) -> Message:
    service.takedown(actor, service.load_visible(actor, slug), data.reason, restore)
    return Message(message="Project restored." if restore else "Project taken down.")
