"""Shared primitives for lab services: actors, identifiers, hashing and pagination."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.security.context import Principal

T = TypeVar("T")

MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class Actor:
    """Who performed an action. Humans act through principals; agents and workflows act *on behalf of* the
    principal that launched them and can never exceed that principal's permissions."""

    type: str  # user | api_key | service_account | agent | workflow | system
    id: str
    label: str
    user_id: uuid.UUID | None = None

    @classmethod
    def of(cls, principal: Principal) -> Actor:
        return cls(
            type=principal.actor_type,
            id=principal.actor_id,
            label=principal.actor_label,
            user_id=principal.user_id if principal.actor_type == "user" else None,
        )

    @classmethod
    def agent(cls, run_id: uuid.UUID | str, role: str) -> Actor:
        return cls(type="agent", id=f"agent_run:{run_id}", label=f"agent:{role}")

    @classmethod
    def workflow(cls, run_id: uuid.UUID | str, workflow: str) -> Actor:
        return cls(type="workflow", id=f"workflow:{run_id}", label=f"workflow:{workflow}")

    @property
    def is_human(self) -> bool:
        return self.type == "user"

    def fact(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "label": self.label}


SYSTEM_ACTOR = Actor(type="system", id="system", label="system")


def parse_uuid(value: uuid.UUID | str | None, label: str = "Resource") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise NotFound(f"{label} not found") from exc


def opt_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"'{value}' is not a valid id") from exc


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Page[T]:
    items: list[T]
    total: int
    limit: int
    offset: int


def paginate[T](db: Session, stmt: Select[T], *, limit: int = 50, offset: int = 0) -> Page[T]:
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))
    total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    items = list(db.scalars(stmt.limit(limit).offset(offset)).all())
    return Page(items=items, total=total, limit=limit, offset=offset)


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"
