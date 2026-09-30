"""Governance side effects that must survive the caller's rollback.

Governance functions run inside the caller's transaction and usually end with the caller raising a typed
error (``BudgetExceeded``, ``PolicyDenied``, ``ApprovalRequired``, an expired-approval ``Conflict``). The
request/unit of work then rolls back — which would silently discard the mission pause, the denial event,
the approval request the error points to, or the expiry. :func:`on_rollback` registers a compensating
action that re-applies such a side effect in a *fresh*, short tenant transaction only if the caller's
transaction rolls back (it is discarded on commit, where the original write already persisted).

Compensations run after the rollback released the caller's locks, must be idempotent (re-check state
before writing) and must work only from ids/values captured at registration — never from ORM instances of
the rolled-back session. Failures are logged and never mask the caller's original exception.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from aegis_api.db.session import session_scope

log = structlog.get_logger("aegis.lab.governance")

_KEY = "governance_rollback_compensations"

Compensation = Callable[[Session], None]


@dataclass(frozen=True)
class _Entry:
    root: SessionTransaction | None
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    fn: Compensation
    name: str


def on_rollback(
    db: Session, organization_id: uuid.UUID, fn: Compensation, *, user_id: uuid.UUID | None = None, name: str = ""
) -> None:
    """Run ``fn(fresh_session)`` in a new tenant transaction if ``db``'s outermost transaction rolls back.

    The compensation is bound to the current outermost transaction: it is dropped once that transaction
    commits (entries of finished transactions are pruned here and on the next rollback).
    """
    root = db.get_transaction()
    entries = [e for e in db.info.get(_KEY, ()) if e.root is root]
    entries.append(_Entry(root, organization_id, user_id, fn, name or getattr(fn, "__name__", "compensation")))
    db.info[_KEY] = entries


def pending_compensations(db: Session) -> int:
    root = db.get_transaction()
    return sum(1 for e in db.info.get(_KEY, ()) if e.root is root)


@event.listens_for(Session, "after_soft_rollback")
def _replay_on_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    if previous_transaction.parent is not None:  # savepoint rollback: the outer transaction still decides
        return
    entries: list[_Entry] = session.info.pop(_KEY, None) or []
    for entry in entries:
        if entry.root is not previous_transaction:
            continue  # registered in an earlier transaction that committed
        try:
            with session_scope(entry.organization_id, entry.user_id) as fresh:
                entry.fn(fresh)
        except Exception:
            log.warning("governance_compensation_failed", compensation=entry.name, exc_info=True)


def jsonable(value: Any) -> Any:
    """Plain-JSON copy of ``value`` (UUIDs, datetimes and Decimals converted)."""
    from aegis_api.lab.core.events import _jsonable

    return _jsonable(value)
