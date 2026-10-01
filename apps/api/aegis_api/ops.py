"""Operator commands (run inside the API image or a dev checkout)::

    python -m aegis_api.ops tick [--only deliver_webhooks reap_stale_runs ...]
    python -m aegis_api.ops jobs [--status dead] [--limit 50]
    python -m aegis_api.ops redrive <job-run-id>

``redrive`` re-arms a failed or dead-lettered job (attempts reset) and dispatches it through the normal
backend, so it runs with the same ledger, claim and idempotency guarantees as the original.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from sqlalchemy import select


def list_jobs(status: str | None, limit: int) -> list[dict[str, Any]]:
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import JobRun

    with admin_session_scope() as s:
        stmt = select(JobRun).order_by(JobRun.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(JobRun.status == status)
        return [
            {
                "id": str(j.id),
                "job": j.job,
                "status": j.status,
                "attempts": f"{j.attempts}/{j.max_attempts}",
                "organization_id": str(j.organization_id) if j.organization_id else None,
                "error_class": j.error_class,
                "error": (j.error or "")[:200],
                "created_at": j.created_at.isoformat(),
            }
            for j in s.scalars(stmt).all()
        ]


def redrive(job_run_id: uuid.UUID) -> uuid.UUID | None:
    """Re-dispatch a finished (failed/dead/succeeded) job. Returns the ledger id, or None if not re-drivable."""
    from aegis_api.db.session import admin_session_scope
    from aegis_api.jobs import dispatcher
    from aegis_api.jobs.jobs import JOB_REGISTRY
    from aegis_api.models import JobRun

    with admin_session_scope() as s:
        job = s.get(JobRun, job_run_id)
        if job is None:
            raise SystemExit(f"job run {job_run_id} not found")
        if job.status in ("queued", "running", "retrying"):
            raise SystemExit(f"job run {job_run_id} is {job.status}; nothing to re-drive")
        name, org, args, key = job.job, job.organization_id, list(job.args), job.idempotency_key
    fn = JOB_REGISTRY.get(name)
    if fn is None:
        raise SystemExit(f"job '{name}' is not re-drivable (not in the job registry)")
    return dispatcher.dispatch(fn, org, *args, idempotency_key=key, force=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aegis_api.ops", description="Aegis operator commands")
    sub = parser.add_subparsers(dest="command", required=True)
    tick = sub.add_parser("tick", help="run one maintenance tick now")
    tick.add_argument("--only", nargs="*", help="run only these maintenance tasks")
    jobs = sub.add_parser("jobs", help="list background jobs from the ledger")
    jobs.add_argument("--status", choices=["queued", "running", "retrying", "succeeded", "failed", "dead"])
    jobs.add_argument("--limit", type=int, default=50)
    rd = sub.add_parser("redrive", help="re-arm and dispatch a failed or dead-lettered job")
    rd.add_argument("job_run_id", type=uuid.UUID)
    args = parser.parse_args(argv)

    if args.command == "tick":
        from aegis_api.jobs import maintenance

        out: Any = maintenance.tick(only=args.only)
    elif args.command == "jobs":
        out = list_jobs(args.status, args.limit)
    else:
        job_id = redrive(args.job_run_id)
        out = {"redriven": str(job_id) if job_id else None}
    sys.stdout.write(json.dumps(out, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
