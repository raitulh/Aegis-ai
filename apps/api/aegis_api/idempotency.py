"""``Idempotency-Key`` support for non-idempotent POST endpoints.

The key row is inserted in the *same* transaction as the work it protects:

* first request: the row is inserted (``pending``), the work runs, the response is stored (``completed``),
  and everything commits together;
* a concurrent duplicate blocks on the unique index until the first commits, then replays the stored
  response (``Idempotency-Replayed: true``);
* a retry after a failed request finds no row (the failed transaction rolled it back) and executes again;
* reusing a key with a different request body is rejected (422).

Keys are scoped per workspace and expire after ``IDEMPOTENCY_TTL_HOURS``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, ValidationFailed
from aegis_api.models import IdempotencyKey
from aegis_api.security.context import Principal

HEADER = "idempotency-key"


@dataclass
class IdempotencyHandle:
    record_id: Any | None
    replay: JSONResponse | None = None


def _hash(body: Any) -> str:
    if isinstance(body, BaseModel):
        body = body.model_dump(mode="json")
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def begin(db: Session, principal: Principal, request: Request, body: Any) -> IdempotencyHandle:
    key = request.headers.get(HEADER, "").strip()
    if not key:
        return IdempotencyHandle(record_id=None)
    if len(key) > 128:
        raise ValidationFailed("Idempotency-Key must be at most 128 characters")
    request_hash = _hash({"path": request.url.path, "body": body})
    inserted = db.execute(
        insert(IdempotencyKey)
        .values(
            organization_id=principal.organization_id,
            key=key,
            method=request.method,
            path=request.url.path[:300],
            request_hash=request_hash,
            status="pending",
            expires_at=utcnow() + timedelta(hours=get_settings().idempotency_ttl_hours),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency_keys_org_key")
        .returning(IdempotencyKey.id)
    ).scalar()
    if inserted is not None:
        return IdempotencyHandle(record_id=inserted)
    existing = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.organization_id == principal.organization_id, IdempotencyKey.key == key
        )
    )
    if existing is None:  # pragma: no cover - expired and pruned between statements
        return IdempotencyHandle(record_id=None)
    if existing.request_hash != request_hash:
        raise ValidationFailed("Idempotency-Key was already used with a different request", code="idempotency_mismatch")
    if existing.status != "completed" or existing.response_status is None:
        raise Conflict("A request with this Idempotency-Key is still in progress", code="idempotency_in_progress")
    return IdempotencyHandle(
        record_id=existing.id,
        replay=JSONResponse(
            existing.response_body,
            status_code=existing.response_status,
            headers={"Idempotency-Replayed": "true"},
        ),
    )


def complete(db: Session, handle: IdempotencyHandle, status_code: int, response: Any) -> None:
    if handle.record_id is None:
        return
    record = db.get(IdempotencyKey, handle.record_id)
    if record is None:
        return
    record.status = "completed"
    record.response_status = status_code
    record.response_body = response.model_dump(mode="json") if isinstance(response, BaseModel) else response
