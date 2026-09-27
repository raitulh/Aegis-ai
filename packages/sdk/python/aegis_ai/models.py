"""Lightweight typed containers. Resources are returned as dicts to stay forward-compatible."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class Page(Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int

    @staticmethod
    def from_response(body: dict[str, Any]) -> Page[dict[str, Any]]:
        meta = body.get("meta", {})
        items: list[dict[str, Any]] = list(body.get("items", []))
        return Page(
            items=items,
            page=meta.get("page", 1),
            page_size=meta.get("page_size", 0),
            total=meta.get("total", 0),
            total_pages=meta.get("total_pages", 0),
        )

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)
