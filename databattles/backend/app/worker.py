"""Background worker: processes queued jobs (scoring, email, GitHub sync) and periodic maintenance.

Run with:  python -m app.worker
"""

from __future__ import annotations

import logging
import signal
import time

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.jobs.periodic import run_due
from app.jobs.queue import claim_next, run_job
from app.jobs.registry import load_handlers

logger = logging.getLogger("databattles.worker")
_stop = False
_last_periodic: dict[str, float] = {}


def run_once() -> bool:
    """Claim and run a single job. Returns True if a job was processed."""
    run_due(SessionLocal, _last_periodic, time.time())
    with SessionLocal() as db:
        job = claim_next(db, settings.WORKER_ID)
        if job is None:
            return False
        run_job(db, job)
        return True


def _handle_signal(signum: int, _frame: object) -> None:
    global _stop
    logger.info("worker_stopping", extra={"signal": signum})
    _stop = True


def main() -> None:
    configure_logging()
    load_handlers()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    logger.info("worker_started", extra={"worker_id": settings.WORKER_ID})
    while not _stop:
        try:
            processed = run_once()
        except Exception:  # noqa: BLE001 — keep the worker alive; the failure is logged
            logger.exception("worker_loop_error")
            processed = False
            time.sleep(5)
        if not processed:
            time.sleep(settings.WORKER_POLL_SECONDS)
    logger.info("worker_stopped")


if __name__ == "__main__":
    main()
