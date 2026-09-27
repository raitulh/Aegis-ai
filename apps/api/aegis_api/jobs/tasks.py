"""Celery application and generic task wrapper (only used when JOB_BACKEND=celery)."""

from __future__ import annotations

import uuid

from celery import Celery

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.jobs.jobs import JOB_REGISTRY

settings = get_settings()
celery_app = Celery("aegis", broker=settings.redis_url or "memory://", backend=settings.redis_url or "cache+memory://")
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    broker_transport_options={"protocol": 2},
    result_backend_transport_options={"protocol": 2},
)


@celery_app.task(name="aegis.run_service_job", bind=True, max_retries=2)
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
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
