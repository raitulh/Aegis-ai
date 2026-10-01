"""Engine/session management with per-transaction tenant context for Row Level Security.

Every transaction started by a session that carries ``session.info['org_id']`` executes
``set_config('app.current_org_id', ..., true)`` so PostgreSQL RLS policies can enforce isolation as a
second line of defence behind the application-level organization filters.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from aegis_api.config import get_settings

_ENGINES: dict[str, Engine] = {}


def _make_engine(url: str, pool_size: int, max_overflow: int, echo: bool) -> Engine:
    engine = _ENGINES.get(url)
    if engine is None:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=1800,
            echo=echo,
        )
        _ENGINES[url] = engine
    return engine


def get_engine() -> Engine:
    s = get_settings()
    return _make_engine(s.database_url, s.db_pool_size, s.db_max_overflow, s.db_echo)


def get_admin_engine() -> Engine:
    s = get_settings()
    return _make_engine(s.admin_database_url, 3, 5, s.db_echo)


def reset_engines() -> None:
    """Dispose cached engines (used by tests when settings change)."""
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()


def session_factory(admin: bool = False) -> sessionmaker[Session]:
    return sessionmaker(bind=get_admin_engine() if admin else get_engine(), expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction: object, connection: object) -> None:
    org_id = session.info.get("org_id")
    user_id = session.info.get("user_id")
    if org_id is None and user_id is None:
        return
    connection.execute(  # type: ignore[attr-defined]
        text("select set_config('app.current_org_id', :org, true), set_config('app.current_user_id', :usr, true)"),
        {"org": str(org_id) if org_id else "", "usr": str(user_id) if user_id else ""},
    )


@event.listens_for(Session, "after_commit")
def _run_after_commit(session: Session) -> None:
    """Dispatch background jobs queued during the transaction, only after it commits."""
    hooks = session.info.pop("after_commit", None)
    if not hooks:
        return
    from aegis_api.jobs import dispatcher

    for fn, org_id, *args in hooks:
        try:
            dispatcher.dispatch(fn, org_id, *args)
        except Exception:
            import structlog

            structlog.get_logger("aegis.jobs").exception("dispatch_failed")


@event.listens_for(Session, "after_rollback")
def _discard_after_commit(session: Session) -> None:
    """Jobs queued by a rolled-back transaction must never run."""
    session.info.pop("after_commit", None)


def rowcount(result: object) -> int:
    """Rows affected by an UPDATE/DELETE result (typed helper for SQLAlchemy's CursorResult)."""
    return int(getattr(result, "rowcount", 0) or 0)


def set_tenant(session: Session, org_id: uuid.UUID | None, user_id: uuid.UUID | None = None) -> None:
    """Bind tenant context to the session and apply it to the current transaction immediately."""
    session.info["org_id"] = org_id
    if user_id is not None:
        session.info["user_id"] = user_id
    if session.in_transaction():
        session.execute(
            text("select set_config('app.current_org_id', :org, true), set_config('app.current_user_id', :usr, true)"),
            {
                "org": str(org_id) if org_id else "",
                "usr": str(session.info.get("user_id") or ""),
            },
        )


@contextmanager
def session_scope(org_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> Iterator[Session]:
    session = session_factory()()
    session.info["org_id"] = org_id
    session.info["user_id"] = user_id
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


tenant_session = session_scope


@contextmanager
def admin_session_scope() -> Iterator[Session]:
    """Owner connection (bypasses RLS). Only for migrations, seeding and system maintenance."""
    session = session_factory(admin=True)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
