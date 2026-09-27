"""Shared schema primitives: pagination, ordering and the standard list envelope."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def _stringify_uuid(cls, value: Any) -> Any:
        return str(value) if isinstance(value, uuid.UUID) else value


class PageParams(BaseModel):
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=200)] = 25

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class Page[T](BaseModel):
    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> Page[T]:
        pages = (total + params.page_size - 1) // params.page_size if params.page_size else 0
        return cls(
            items=items, meta=PageMeta(page=params.page, page_size=params.page_size, total=total, total_pages=pages)
        )


class Message(BaseModel):
    message: str


class IdResponse(BaseModel):
    id: str


class Timestamped(ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime | None = None
