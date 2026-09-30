"""Pagination for lab list endpoints.

* Offset pagination (``Page[T]`` from ``aegis_api.schemas.common``) with a whitelisted ``sort`` parameter
  for ordinary resource lists.
* Keyset (cursor) pagination (``CursorPage[T]``) for high-volume streams such as events, runs, usage
  ledgers and agent steps — stable under concurrent inserts and O(1) per page.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.schemas.common import Page, PageParams


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(default=None, description="Opaque cursor for the next page; null at the end")
    limit: int


class CursorParams(BaseModel):
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1, le=500)] = 50


def cursor_params(
    cursor: str | None = Query(None, description="Opaque cursor from a previous page"),
    limit: int = Query(50, ge=1, le=500),
) -> CursorParams:
    return CursorParams(cursor=cursor, limit=limit)


def encode_cursor(values: dict[str, Any]) -> str:
    raw = json.dumps(values, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValidationFailed("Invalid pagination cursor") from exc
    if not isinstance(data, dict):
        raise ValidationFailed("Invalid pagination cursor")
    return data


def paginate_keyset(
    db: Session,
    stmt: Select,
    params: CursorParams,
    *,
    time_col: Any,
    id_col: Any,
    mapper: Callable[[Any], Any],
    descending: bool = True,
) -> CursorPage:
    """Keyset pagination on (time_col, id_col). ``id_col`` must be unique."""
    if params.cursor:
        c = decode_cursor(params.cursor)
        try:
            t = datetime.fromisoformat(c["t"])
            i = c["i"]
        except (KeyError, ValueError) as exc:
            raise ValidationFailed("Invalid pagination cursor") from exc
        if descending:
            stmt = stmt.where(or_(time_col < t, and_(time_col == t, id_col < i)))
        else:
            stmt = stmt.where(or_(time_col > t, and_(time_col == t, id_col > i)))
    order = (time_col.desc(), id_col.desc()) if descending else (time_col.asc(), id_col.asc())
    rows = db.scalars(stmt.order_by(*order).limit(params.limit + 1)).all()
    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"t": getattr(last, time_col.key), "i": getattr(last, id_col.key)})
    return CursorPage(items=[mapper(r) for r in rows], next_cursor=next_cursor, limit=params.limit)


def paginate_by_id(
    db: Session, stmt: Select, params: CursorParams, *, id_col: Any, mapper: Callable[[Any], Any]
) -> CursorPage:
    """Ascending keyset pagination on a monotonically increasing integer id (e.g. events)."""
    if params.cursor:
        c = decode_cursor(params.cursor)
        try:
            after = int(c["after"])
        except (KeyError, ValueError) as exc:
            raise ValidationFailed("Invalid pagination cursor") from exc
        stmt = stmt.where(id_col > after)
    rows = db.scalars(stmt.order_by(id_col.asc()).limit(params.limit + 1)).all()
    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    next_cursor = encode_cursor({"after": getattr(rows[-1], id_col.key)}) if has_more and rows else None
    return CursorPage(items=[mapper(r) for r in rows], next_cursor=next_cursor, limit=params.limit)


def sort_clause(model: Any, sort: str | None, allowed: Sequence[str], default: str = "-created_at") -> Any:
    """Translate ``sort=field`` / ``sort=-field`` into an ORDER BY clause over whitelisted columns."""
    value = sort or default
    desc = value.startswith("-")
    name = value.lstrip("-+")
    if name not in allowed:
        raise ValidationFailed(f"Unsupported sort field '{name}'. Allowed: {', '.join(allowed)}")
    column = getattr(model, name)
    return column.desc() if desc else column.asc()


def paginate(db: Session, stmt: Select, params: PageParams, mapper: Callable[[Any], Any]) -> Page:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.limit(params.page_size).offset(params.offset)).all()
    return Page.build([mapper(r) for r in rows], int(total), params)
