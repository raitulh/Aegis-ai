"""Discussions, moderation and notifications."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk
from app.models.enums import ReportStatus


class DiscussionCategory(UUIDPk, Base):
    __tablename__ = "discussion_categories"

    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    staff_only_posting: Mapped[bool] = mapped_column(default=False)


class Thread(UUIDPk, Base):
    __tablename__ = "threads"
    __table_args__ = (
        Index("ix_threads_listing", "category_id", "pinned", "last_activity_at"),
        Index("ix_threads_competition", "competition_id", "last_activity_at"),
        Index("ix_threads_project", "project_id", "last_activity_at"),
    )

    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discussion_categories.id", ondelete="SET NULL"))
    competition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body_md: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(default=False)
    locked: Mapped[bool] = mapped_column(default=False)
    hidden: Mapped[bool] = mapped_column(default=False)
    is_announcement: Mapped[bool] = mapped_column(default=False)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_comment_id: Mapped[uuid.UUID | None] = mapped_column()
    last_activity_at: Mapped[datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Comment(UUIDPk, Base):
    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_thread_created", "thread_id", "created_at"),)

    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comments.id", ondelete="SET NULL"))
    body_md: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(default=False)
    edited_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PostRevision(UUIDPk, Base):
    __tablename__ = "post_revisions"
    __table_args__ = (Index("ix_post_revisions_target", "target_type", "target_id"),)

    target_type: Mapped[str] = mapped_column(String(16))  # thread|comment
    target_id: Mapped[uuid.UUID] = mapped_column()
    title: Mapped[str | None] = mapped_column(String(160))
    body_md: Mapped[str] = mapped_column(Text)
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ThreadMute(Base):
    __tablename__ = "thread_mutes"

    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Report(UUIDPk, Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("uq_reports_open_per_reporter", "reporter_id", "target_type", "target_id", unique=True,
              postgresql_where=text("status = 'open'")),
        Index("ix_reports_status", "status", "created_at"),
    )

    reporter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[uuid.UUID] = mapped_column()
    reason: Mapped[str] = mapped_column(String(16))
    details: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default=ReportStatus.open)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution_note: Mapped[str | None] = mapped_column(String(1000))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    group_key: Mapped[str | None] = mapped_column(String(200))
    group_count: Mapped[int] = mapped_column(Integer, default=1)
    read_at: Mapped[datetime | None]
    emailed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    in_app: Mapped[bool] = mapped_column(default=True)
    email: Mapped[bool] = mapped_column(default=False)


class EmailOutbox(UUIDPk, Base):
    __tablename__ = "email_outbox"

    to_email: Mapped[str] = mapped_column(String(320))
    template: Mapped[str] = mapped_column(String(48))
    template_version: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(200))
    text_body: Mapped[str | None] = mapped_column(Text)   # only stored when EMAIL_STORE_BODIES (dev)
    html_body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]
