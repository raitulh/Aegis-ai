"""``Idempotency-Key`` support for expensive, externally-triggered writes.

Usage in a route (the dependency shares the request's RLS-scoped DB session)::

    @router.post("/missions/{mission_id}/start", status_code=202)
    def start(..., idem: Idempotency = Depends(idempotency), db: Session = Depends(get_db)):
        if (replay := idem.replay()) is not None:
            return replay
        result = service.start(...)
        return idem.remember(202, MissionOut.model_validate(result))

Semantics:

* The key row is inserted in the **same transaction** as the work (``INSERT … ON CONFLICT DO NOTHING``).
  A concurrent duplicate blocks on the unique index until the first request finishes; if the first
  committed, the duplicate replays its stored response; if it rolled back, the duplicate proceeds.
* Reusing a key with a different method/path/body is rejected (422 ``idempotency_key_reused``).
* Keys are scoped per organization and per credential, and expire after ``IDEMPOTENCY_TTL_HOURS``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.deps import get_current_principal, get_db
from aegis_api.errors import ValidationFailed
from aegis_api.lab.models import IdempotencyKey
from aegis_api.security.context import Principal

HEADER = "Idempotency-Key"
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,255}$")


class Idempotency:
    def __init__(self, db: Session, row: IdempotencyKey | None, *, replayed: bool) -> None:
        self._db = db
        self._row = row
        self._replayed = replayed

    @property
    def enabled(self) -> bool:
        return self._row is not None

    def replay(self) -> JSONResponse | None:
        """The stored response when this request is a retry of a completed request."""
        if not self._replayed or self._row is None:
            return None
        return JSONResponse(
            self._row.response_body,
            status_code=self._row.response_status,
            headers={"Idempotent-Replayed": "true"},
        )

    def remember(self, status_code: int, body: BaseModel | dict[str, Any] | list[Any]) -> Any:
        """Persist the response for future retries (same transaction as the work) and return ``body``."""
        if self._row is not None and not self._replayed:
            payload = jsonable_encoder(body)
            self._row.response_status = status_code
            self._row.response_body = payload
            self._row.status = "completed"
            if isinstance(payload, dict) and payload.get("id"):
                self._row.resource_id = str(payload["id"])[:64]
            self._db.flush()
        return body


def _principal_key(principal: Principal) -> str:
    if principal.api_key_id:
        return f"key:{principal.api_key_id}"
    return f"user:{principal.user_id}"


async def idempotency(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Idempotency:
    key = request.headers.get(HEADER)
    if not key:
        return Idempotency(db, None, replayed=False)
    if not _KEY_RE.match(key):
        raise ValidationFailed("Idempotency-Key must be 8-255 characters of [A-Za-z0-9_-:.]")
    body = await request.body()
    fingerprint = hashlib.sha256(
        json.dumps({"m": request.method, "p": request.url.path, "b": body.decode("utf-8", "replace")}).encode()
    ).hexdigest()
    principal_key = _principal_key(principal)
    now = utcnow()
    stmt = (
        insert(IdempotencyKey)
        .values(
            organization_id=principal.organization_id,
            principal_key=principal_key,
            key=key,
            method=request.method,
            path=request.url.path[:500],
            request_hash=fingerprint,
            status="in_progress",
            response_status=0,
            response_body={},
            created_at=now,
            expires_at=now + timedelta(hours=get_settings().idempotency_ttl_hours),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency_keys_scope")
        .returning(IdempotencyKey.id)
    )
    inserted = db.execute(stmt).scalar()
    row = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.organization_id == principal.organization_id,
            IdempotencyKey.principal_key == principal_key,
            IdempotencyKey.key == key,
        )
    )
    if row is None:  # pragma: no cover - defensive
        return Idempotency(db, None, replayed=False)
    if inserted is not None:
        return Idempotency(db, row, replayed=False)
    if row.request_hash != fingerprint:
        raise ValidationFailed(
            "Idempotency-Key was already used with a different request", code="idempotency_key_reused"
        )
    if row.expires_at < now:
        # Expired key: treat as new work under the same key.
        row.request_hash = fingerprint
        row.status = "in_progress"
        row.expires_at = now + timedelta(hours=get_settings().idempotency_ttl_hours)
        return Idempotency(db, row, replayed=False)
    return Idempotency(db, row, replayed=True)
