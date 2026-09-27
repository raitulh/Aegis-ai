"""Shared router helpers: pagination and filtered listing."""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.schemas.common import Page, PageParams

T = TypeVar("T")


def paginate(session: Session, stmt: Select, params: PageParams, mapper) -> Page:
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.scalars(stmt.limit(params.page_size).offset(params.offset)).all()
    return Page.build([mapper(r) for r in rows], int(total), params)
