"""Tie object writes to the surrounding database transaction.

Objects are written before the rows that reference them are committed. ``delete_on_rollback`` registers a
freshly written key on the session; if the outermost transaction rolls back, the object is deleted so no
orphaned bytes are left behind, and on commit the registration is simply dropped. Keys registered here
must be unique to the transaction (version keys carry the new version row id) so a cleanup can never
remove an object another transaction committed.
"""

from __future__ import annotations

import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from aegis_api.lab.storage.base import ObjectStorage

log = structlog.get_logger("aegis.lab.storage")

_PENDING_KEY = "lab_storage_uncommitted_objects"


def delete_on_rollback(db: Session, key: str, storage: ObjectStorage | None = None) -> None:
    """Delete ``key`` if the session's current transaction rolls back."""
    db.info.setdefault(_PENDING_KEY, []).append((storage, key))


@event.listens_for(Session, "after_commit")
def _forget_committed(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _delete_uncommitted(session: Session, previous_transaction: SessionTransaction) -> None:
    if getattr(previous_transaction, "parent", None) is not None:
        return  # savepoint rollback: the outer transaction may still commit
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    from aegis_api.lab.storage import get_storage

    for storage, key in pending:
        try:
            (storage or get_storage()).delete(key)
        except Exception:  # cleanup is best-effort; an orphan is harmless, a crash here is not
            log.warning("orphan_object_cleanup_failed", key=key, exc_info=True)
