"""Operator CLI: list the job ledger and re-drive dead-lettered jobs through the normal dispatch path."""

from __future__ import annotations

import time
import uuid

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def _dead_job() -> uuid.UUID:
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import JobRun

    with admin_session_scope() as s:
        job = JobRun(
            job="aegis_api.jobs.jobs:run_audit_job",
            idempotency_key=f"test-redrive:{uuid.uuid4()}",
            args=[str(uuid.uuid4())],  # an audit that does not exist → the re-run fails permanently
            status="dead",
            attempts=3,
            max_attempts=3,
            error_class="transient",
            error="ConnectionError: provider unreachable",
        )
        s.add(job)
        s.flush()
        return job.id


def _status(job_id: uuid.UUID) -> tuple[str, int]:
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import JobRun

    with admin_session_scope() as s:
        job = s.get(JobRun, job_id)
        assert job is not None
        return job.status, job.attempts


def test_redrive_rearms_and_dispatches_a_dead_job(client):
    from aegis_api import ops

    job_id = _dead_job()
    assert any(j["id"] == str(job_id) for j in ops.list_jobs("dead", 200))
    assert ops.redrive(job_id) == job_id
    deadline = time.time() + 20
    while time.time() < deadline and _status(job_id)[0] in ("queued", "running"):
        time.sleep(0.1)
    status, attempts = _status(job_id)
    assert status == "failed" and attempts == 1  # attempts were reset; the missing audit is a permanent failure


def test_redrive_refuses_active_jobs(client):
    from aegis_api import ops
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import JobRun

    job_id = _dead_job()
    with admin_session_scope() as s:
        job = s.get(JobRun, job_id)
        assert job is not None
        job.status = "running"
    with pytest.raises(SystemExit, match="nothing to re-drive"):
        ops.redrive(job_id)
