"""Job entrypoints (importable by both the inline dispatcher and Celery)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from aegis_api.services import orchestrator, regression_service


def run_audit_job(session: Session, audit_id: str) -> None:
    orchestrator.run_audit(session, uuid.UUID(audit_id))


def run_redteam_job(session: Session, run_id: str) -> None:
    from aegis_api.services import redteam_service

    redteam_service.execute_run(session, uuid.UUID(run_id))


def run_regression_job(session: Session, run_id: str) -> None:
    regression_service.execute_run(session, uuid.UUID(run_id))


def compile_policy_job(session: Session, policy_version_id: str) -> None:
    from aegis_api.services import policy_service

    policy_service.compile_version(session, uuid.UUID(policy_version_id))


JOB_REGISTRY = {
    "aegis_api.jobs.jobs:run_audit_job": run_audit_job,
    "aegis_api.jobs.jobs:run_redteam_job": run_redteam_job,
    "aegis_api.jobs.jobs:run_regression_job": run_regression_job,
    "aegis_api.jobs.jobs:compile_policy_job": compile_policy_job,
}
