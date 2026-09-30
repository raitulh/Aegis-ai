"""Distributed locks via PostgreSQL advisory locks.

Transaction-scoped advisory locks (``pg_advisory_xact_lock``) serialize critical sections across every
API and worker process without extra infrastructure, and are released automatically on commit/rollback
(no leaked locks after a crash). Used for strategy promotion, evidence-chain appends, mission state
transitions and budget reservations. Optimistic concurrency (``lock_version`` columns) covers ordinary
concurrent edits.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _key(name: str):
    return func.hashtextextended(name, 0)


def advisory_xact_lock(db: Session, name: str) -> None:
    """Block until the named lock is held for the rest of the current transaction."""
    db.execute(select(func.pg_advisory_xact_lock(_key(name))))


def try_advisory_xact_lock(db: Session, name: str) -> bool:
    """Try to take the named lock for the current transaction without waiting."""
    return bool(db.scalar(select(func.pg_try_advisory_xact_lock(_key(name)))))
