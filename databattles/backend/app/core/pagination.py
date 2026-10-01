"""Pagination primitives: offset pages for browsable lists, cursors for feeds."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed

T = TypeVar("T")


class PageParams:
    def __init__(self, page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=100)) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


def paginate(db: Session, stmt: Select[Any], params: PageParams) -> tuple[list[Any], int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return rows, total


def make_page(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "has_next": params.offset + len(items) < total,
    }


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        ts, rid = json.loads(raw)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValidationFailed("Invalid cursor.", code="invalid_cursor")
