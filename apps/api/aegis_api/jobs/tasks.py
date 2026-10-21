"""Celery application, DLQ routing, and resilient task execution with exponential backoff."""

from __future__ import annotations

import uuid

import structlog
from celery import Celery
from kombu import Exchange, Queue

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.jobs.jobs import JOB_REGISTRY

log = structlog.get_logger("aegis.celery")
settings = get_settings()

default_exchange = Exchange("aegis", type="direct")
dlq_exchange = Exchange("aegis.dlq", type="direct")

celery_app = Celery("aegis", broker=settings.redis_url or "memory://", backend=settings.redis_url or "cache+memory://")
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    broker_transport_options={"protocol": 2},
    result_backend_transport_options={"protocol": 2},
    task_queues=(
        Queue("aegis.default", default_exchange, routing_key="aegis.default"),
        Queue("aegis.dlq", dlq_exchange, routing_key="aegis.dlq"),
    ),
    task_default_queue="aegis.default",
    task_default_exchange="aegis",
    task_default_routing_key="aegis.default",
)


@celery_app.task(name="aegis.dlq_sink")
def dlq_sink(task_name: str, organization_id: str, args: list, error: str) -> None:
    """Sink for exhausted jobs routed to the dead-letter queue."""
    log.error(
        "job_dead_lettered",
        task=task_name,
        organization_id=organization_id,
        args=args,
        error=error,
    )


@celery_app.task(
    name="aegis.run_service_job",
    bind=True,
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def run_service_job(self, dotted: str, organization_id: str, args: list) -> None:
    fn = JOB_REGISTRY.get(dotted)
    if fn is None:
        raise ValueError(f"Unknown job {dotted}")
    session = session_factory()()
    session.begin()
    set_tenant(session, uuid.UUID(organization_id), None)
    try:
        fn(session, *args)
        session.commit()
    except Exception as exc:
        session.rollback()
        log.warning("job_attempt_failed", job=dotted, retry=self.request.retries, max_retries=self.max_retries)
        if self.request.retries >= self.max_retries:
            dlq_sink.apply_async(
                args=(dotted, organization_id, args, str(exc)),
                queue="aegis.dlq",
            )
            raise
        raise self.retry(exc=exc) from exc
    finally:
        session.close()
