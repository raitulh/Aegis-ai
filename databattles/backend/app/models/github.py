from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class GitHubAccount(UUIDPk, Base):
    """A user's linked GitHub identity. The access token is encrypted at rest and never sent to browsers."""

    __tablename__ = "github_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(64))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[str | None] = mapped_column(String(200))
    connected_at: Mapped[datetime] = mapped_column(server_default=func.now())


class GitHubRepository(UUIDPk, Base):
    """Tracked by GitHub's immutable numeric id; full_name may change on rename/transfer."""

    __tablename__ = "github_repositories"

    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), index=True)
    owner_login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(40))
    stars: Mapped[int | None] = mapped_column(Integer)
    forks: Mapped[int | None] = mapped_column(Integer)
    open_issues: Mapped[int | None] = mapped_column(Integer)
    topics: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, server_default="{}")
    html_url: Mapped[str] = mapped_column(String(500))
    homepage: Mapped[str | None] = mapped_column(String(500))
    license: Mapped[str | None] = mapped_column(String(64))
    is_private: Mapped[bool] = mapped_column(default=False)
    is_archived: Mapped[bool] = mapped_column(default=False)
    default_branch: Mapped[str | None] = mapped_column(String(100))
    pushed_at: Mapped[datetime | None]
    contributors_count: Mapped[int | None] = mapped_column(Integer)
    sync_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ok|failed|gone|rate_limited
    sync_error: Mapped[str | None] = mapped_column(String(500))
    last_synced_at: Mapped[datetime | None]
    issues_synced_until: Mapped[datetime | None]
    etag: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(16), default="github")  # github|seed
    registered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class GitHubIssue(UUIDPk, Base):
    __tablename__ = "github_issues"
    __table_args__ = (Index("ix_github_issues_open", "repo_id", "state"),)

    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("github_repositories.id", ondelete="CASCADE"))
    github_issue_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(16))
    labels: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list, server_default="{}")
    is_beginner_friendly: Mapped[bool] = mapped_column(default=False)
    is_promoted: Mapped[bool] = mapped_column(default=False)
    promoted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    html_url: Mapped[str] = mapped_column(String(500))
    author_login: Mapped[str | None] = mapped_column(String(64))
    comments: Mapped[int] = mapped_column(Integer, default=0)
    gh_created_at: Mapped[datetime | None]
    gh_updated_at: Mapped[datetime | None]
    gh_closed_at: Mapped[datetime | None]


class GitHubPullRequest(UUIDPk, Base):
    __tablename__ = "github_pull_requests"
    __table_args__ = (Index("ix_github_prs_author", "author_github_id", "merged"),)

    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("github_repositories.id", ondelete="CASCADE"), index=True)
    github_pr_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(16))
    merged: Mapped[bool] = mapped_column(default=False)
    merged_at: Mapped[datetime | None]
    author_github_id: Mapped[int | None] = mapped_column(BigInteger)
    author_login: Mapped[str | None] = mapped_column(String(64))
    html_url: Mapped[str] = mapped_column(String(500))
    additions: Mapped[int | None] = mapped_column(Integer)
    deletions: Mapped[int | None] = mapped_column(Integer)
    gh_created_at: Mapped[datetime | None]
    gh_updated_at: Mapped[datetime | None]


class WebhookDelivery(UUIDPk, Base):
    """Delivery ids are unique: duplicates and replays are acknowledged but not re-processed."""

    __tablename__ = "webhook_deliveries"

    provider: Mapped[str] = mapped_column(String(16), default="github")
    delivery_id: Mapped[str] = mapped_column(String(80), unique=True)
    event: Mapped[str] = mapped_column(String(40))
    action: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="received")  # received|processed|ignored|failed
    error: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    processed_at: Mapped[datetime | None]
