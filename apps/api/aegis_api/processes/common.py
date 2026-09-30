"""Shared process plumbing: logging, graceful shutdown and tenant enumeration for system maintenance."""

from __future__ import annotations

import contextlib
import signal
import threading
import uuid

import structlog
from sqlalchemy import text

from aegis_api.config import get_settings
from aegis_api.db.session import admin_session_scope
from aegis_api.logging import configure_logging

log = structlog.get_logger("aegis.process")


class Stopper:
    def __init__(self) -> None:
        self.event = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError):  # not in the main thread (tests)
                signal.signal(sig, self._handle)

    def _handle(self, signum: int, _frame: object) -> None:
        log.info("shutdown_requested", signal=signum)
        self.event.set()

    @property
    def stopped(self) -> bool:
        return self.event.is_set()

    def wait(self, seconds: float) -> bool:
        return self.event.wait(seconds)


def setup(name: str) -> Stopper:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    structlog.contextvars.bind_contextvars(process=name)
    log.info("process_starting", process=name, environment=settings.environment)
    return Stopper()


def organization_ids() -> list[uuid.UUID]:
    """System maintenance only: enumerate tenant ids (ids only); all work then runs in tenant sessions."""
    with admin_session_scope() as db:
        return [r[0] for r in db.execute(text("SELECT id FROM organizations ORDER BY created_at")).all()]
