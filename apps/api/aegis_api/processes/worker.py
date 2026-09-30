"""Inline workflow worker: polls for runnable inline workflow runs and drives them in a bounded thread pool.

Leases make it safe to run several workers (and the API's own dispatcher) at once: a run is only ever driven by
the holder of its lease; a crashed worker's lease expires and another worker resumes the run by replay.

    python -m aegis_api.processes.worker
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

import structlog

from aegis_api.config import get_settings
from aegis_api.processes.common import setup
from aegis_api.workflows.engine import drive, poll_due

log = structlog.get_logger("aegis.worker")


def run_forever(max_workers: int = 4) -> None:
    stopper = setup("workflow-worker")
    settings = get_settings()
    in_flight: dict[str, Future[str]] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wf-driver") as pool:
        while not stopper.stopped:
            for key in [k for k, f in in_flight.items() if f.done()]:
                future = in_flight.pop(key)
                if future.exception() is not None:
                    log.error("drive_failed", run_id=key, error=str(future.exception())[:300])
            capacity = max_workers - len(in_flight)
            if capacity > 0:
                try:
                    due = poll_due(limit=capacity * 2)
                except Exception:
                    log.exception("poll_failed")
                    due = []
                for run_id, org_id in due:
                    if str(run_id) in in_flight or len(in_flight) >= max_workers:
                        continue
                    in_flight[str(run_id)] = pool.submit(drive, run_id, org_id)
            stopper.wait(settings.inline_workflow_poll_seconds)
    log.info("worker_stopped")


if __name__ == "__main__":
    run_forever()
