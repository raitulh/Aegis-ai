from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk
from app.models.enums import ProjectStatus, ProjectVisibility


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_tags", "tags", postgresql_using="gin"),)

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(140))
    summary: Mapped[str] = mapped_column(String(280), default="")
    description_md: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    technologies: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    repo_url: Mapped[str | None] = mapped_column(String(500))
    demo_url: Mapped[str | None] = mapped_column(String(500))
    paper_url: Mapped[str | None] = mapped_column(String(500))
    video_url: Mapped[str | None] = mapped_column(String(500))
    docs_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default=ProjectStatus.active)
    visibility: Mapped[str] = mapped_column(String(16), default=ProjectVisibility.public)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    competition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitions.id", ondelete="SET NULL"))
    is_featured: Mapped[bool] = mapped_column(default=False)
    is_open_source: Mapped[bool] = mapped_column(default=False)
    github_repo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("github_repositories.id", ondelete="SET NULL"))
    maintainer_verified_at: Mapped[datetime | None]
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    taken_down: Mapped[bool] = mapped_column(default=False)
    takedown_reason: Mapped[str | None] = mapped_column(String(500))
    cover_style: Mapped[str] = mapped_column(String(32), default="aurora")
    is_demo: Mapped[bool] = mapped_column(default=False)


class ProjectMember(UUIDPk, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProjectMedia(UUIDPk, Base):
    __tablename__ = "project_media"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(40))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    alt_text: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProjectDataset(Base):
    __tablename__ = "project_datasets"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)


class Upload(UUIDPk, Base):
    """Generic media uploads (avatars, logos) tracked so orphans can be cleaned up."""

    __tablename__ = "uploads"

    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[str] = mapped_column(String(24))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    attached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
