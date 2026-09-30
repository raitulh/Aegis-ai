"""``Idempotency-Key`` support for expensive, externally-triggered writes.

Protocol (per organization + principal + key):

1. **Claim** — insert an ``in_progress`` row in its *own* committed transaction, so concurrent duplicates see it
   immediately (unique constraint). A duplicate while in progress → 409 ``idempotency_conflict`` (Retry-After).
2. **Complete** — the handler stores the response on the row using the *request* session, so the stored
   response commits atomically with the resource it describes. A crash before commit leaves an expired
   in-progress claim that a later retry may safely re-claim (nothing was committed).
3. **Replay** — a completed key with the same request fingerprint returns the stored response verbatim with
   ``Idempotent-Replayed: true``; the same key with a different payload → 422.

This prevents, e.g., a network retry from launching the same GPU experiment twice.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.deps import get_current_principal
from aegis_api.errors import IdempotencyConflict, ValidationFailed
from aegis_api.models import IdempotencyKey
from aegis_api.security.context import Principal

HEADER = "Idempotency-Key"
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:]{8,255}$")
CLAIM_LOCK_SECONDS = 120


@dataclass
class Idempotency:
    key: str | None
    principal: Principal
    request_hash: str
    record_id: Any = None
    replay_response: JSONResponse | None = None
    _completed: bool = field(default=False)

    @property
    def active(self) -> bool:
        return self.key is not None

    def complete(self, db: Session, status_code: int, body: Any) -> None:
        """Store the response in the request transaction (commits atomically with the resource)."""
        if not self.active or self.record_id is None:
            return
        record = db.get(IdempotencyKey, self.record_id)
        if record is None:
            return
        record.status = "completed"
        record.response_status = status_code
        record.response_body = json.loads(json.dumps(body, default=str))
        record.locked_until = None
        self._completed = True

    def release(self) -> None:
        """Drop an unfinished claim after a failure so the client may retry immediately."""
        if not self.active or self.record_id is None or self._completed:
            return
        session = session_factory()()
        try:
            session.begin()
            set_tenant(session, self.principal.organization_id, self.principal.user_id)
            session.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.id == self.record_id, IdempotencyKey.status == "in_progress"
                )
            )
            session.commit()
        finally:
            session.close()


def _fingerprint(method: str, path: str, body: bytes) -> str:
    return hashlib.sha256(method.encode() + b"\x00" + path.encode() + b"\x00" + body).hexdigest()


def _principal_key(principal: Principal) -> str:
    return principal.actor_id[:96]


async def idempotency(
    request: Request, principal: Principal = Depends(get_current_principal)
) -> AsyncIterator[Idempotency]:
    """FastAPI dependency. Endpoints call ``idem.replay_response`` / ``idem.complete`` / ``idem.release``."""
    key = request.headers.get(HEADER)
    body = await request.body()
    idem = Idempotency(key=None, principal=principal, request_hash=_fingerprint(request.method, request.url.path, body))
    if key is None:
        yield idem
        return
    if not _KEY_RE.match(key):
        raise ValidationFailed("Idempotency-Key must be 8-255 characters of [A-Za-z0-9_-.:]")
    idem.key = key
    await run_in_threadpool(_claim, idem, request)
    try:
        yield idem
    except Exception:
        await run_in_threadpool(idem.release)
        raise


def _claim(idem: Idempotency, request: Request) -> None:
    principal = idem.principal
    settings = get_settings()
    session = session_factory()()
    try:
        session.begin()
        set_tenant(session, principal.organization_id, principal.user_id)
        existing = session.scalar(
            select(IdempotencyKey)
            .where(
                IdempotencyKey.organization_id == principal.organization_id,
                IdempotencyKey.principal_key == _principal_key(principal),
                IdempotencyKey.key == idem.key,
            )
            .with_for_update()
        )
        now = utcnow()
        if existing is not None:
            if existing.request_hash != idem.request_hash:
                raise ValidationFailed(
                    "Idempotency-Key was already used with a different request", code="idempotency_key_reused"
                )
            if existing.status == "completed" and existing.expires_at > now:
                idem.replay_response = JSONResponse(
                    existing.response_body,
                    status_code=existing.response_status or 200,
                    headers={"Idempotent-Replayed": "true"},
                )
                session.commit()
                return
            if existing.status == "in_progress" and existing.locked_until and existing.locked_until > now:
                raise IdempotencyConflict("A request with this Idempotency-Key is still in progress")
            # Expired claim or expired completion: take it over.
            existing.status = "in_progress"
            existing.locked_until = now + timedelta(seconds=CLAIM_LOCK_SECONDS)
            existing.expires_at = now + timedelta(hours=settings.idempotency_ttl_hours)
            existing.response_body = None
            existing.response_status = None
            idem.record_id = existing.id
            session.commit()
            return
        record = IdempotencyKey(
            organization_id=principal.organization_id,
            principal_key=_principal_key(principal),
            key=idem.key or "",
            method=request.method,
            path=request.url.path[:500],
            request_hash=idem.request_hash,
            status="in_progress",
            locked_until=now + timedelta(seconds=CLAIM_LOCK_SECONDS),
            expires_at=now + timedelta(hours=settings.idempotency_ttl_hours),
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise IdempotencyConflict("A request with this Idempotency-Key is already in progress") from exc
        idem.record_id = record.id
    finally:
        session.close()
