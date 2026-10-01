from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk
from app.models.enums import ContentVisibility, CourseStatus, LessonKind


class Course(UUIDPk, Timestamps, Base):
    __tablename__ = "courses"

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(140))
    summary: Mapped[str] = mapped_column(String(300), default="")
    description_md: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), index=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="beginner")
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=60)
    prerequisites: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, server_default="{}")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    visibility: Mapped[str] = mapped_column(String(16), default=ContentVisibility.public)
    status: Mapped[str] = mapped_column(String(16), default=CourseStatus.draft)
    version: Mapped[int] = mapped_column(Integer, default=1)
    published_at: Mapped[datetime | None]
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    badge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("badge_definitions.id", ondelete="SET NULL"))
    issues_certificate: Mapped[bool] = mapped_column(default=False)
    cover_style: Mapped[str] = mapped_column(String(32), default="aurora")
    enrollment_count: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(default=False)


class Lesson(UUIDPk, Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("course_id", "slug"),)

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(140))
    kind: Mapped[str] = mapped_column(String(16), default=LessonKind.article)
    body_md: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=10)
    # challenge: {"type": "prediction" | "code", "competition_slug": ..., "instructions": ...}
    challenge: Mapped[dict[str, Any] | None] = mapped_column()
    pass_threshold: Mapped[int] = mapped_column(Integer, default=70)
    version: Mapped[int] = mapped_column(Integer, default=1)


class QuizQuestion(UUIDPk, Base):
    __tablename__ = "quiz_questions"

    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(default=list)
    correct_option: Mapped[int] = mapped_column(Integer)  # never serialized to learners before answering
    explanation: Mapped[str | None] = mapped_column(Text)


class CourseEnrollment(UUIDPk, Base):
    __tablename__ = "course_enrollments"
    __table_args__ = (UniqueConstraint("course_id", "user_id"),)

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    enrolled_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_lesson_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None]
    course_version: Mapped[int] = mapped_column(Integer, default=1)


class LessonProgress(UUIDPk, Base):
    __tablename__ = "lesson_progress"
    __table_args__ = (UniqueConstraint("lesson_id", "user_id"),)

    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    quiz_score: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class LearningPath(UUIDPk, Base):
    __tablename__ = "learning_paths"

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(140))
    summary: Mapped[str] = mapped_column(String(300), default="")
    course_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    badge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("badge_definitions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
