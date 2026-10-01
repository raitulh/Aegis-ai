"""Celery application, the generic service-job task and the periodic maintenance task.

Only used when ``JOB_BACKEND=celery``. Run workers with::

    celery -A aegis_api.jobs.tasks:celery_app worker --loglevel=info
    celery -A aegis_api.jobs.tasks:celery_app beat   --loglevel=info   # one instance: periodic maintenance
"""

from __future__ import annotations

import uuid

from celery import Celery
from celery.signals import worker_shutting_down

from aegis_api.config import get_settings
from aegis_api.jobs.jobs import JOB_REGISTRY

settings = get_settings()
celery_app = Celery("aegis", broker=settings.redis_url or "memory://", backend=settings.redis_url or "cache+memory://")
celery_app.conf.update(
    task_track_started=True,
    # A message is acknowledged only after the task finishes, and re-queued if the worker dies mid-task.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"protocol": 2, "visibility_timeout": 3900},
    result_backend_transport_options={"protocol": 2},
    result_expires=24 * 3600,
    beat_schedule={
        "aegis-maintenance": {
            "task": "aegis.maintenance_tick",
            "schedule": float(settings.scheduler_interval_seconds),
            "options": {"expires": float(settings.scheduler_interval_seconds)},
        }
    },
)


@celery_app.task(name="aegis.run_service_job", bind=True, max_retries=10)
def run_service_job(self, dotted: str, organization_id: str | None, args: list, job_id: str | None = None) -> None:
    from aegis_api.jobs.dispatcher import execute

    fn = JOB_REGISTRY.get(dotted)
    if fn is None:
        raise ValueError(f"Unknown job {dotted}")
    org = uuid.UUID(organization_id) if organization_id else None
    delay = execute(uuid.UUID(job_id) if job_id else None, fn, org, *args)
    if delay is not None:
        raise self.retry(countdown=delay)


@celery_app.task(name="aegis.maintenance_tick")
def maintenance_tick() -> dict:
    from aegis_api.jobs.maintenance import tick

    return tick()


@worker_shutting_down.connect
def _on_shutdown(**_: object) -> None:  # pragma: no cover - process lifecycle
    import structlog

    structlog.get_logger("aegis.jobs").info("worker_shutting_down")
