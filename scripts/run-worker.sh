#!/usr/bin/env bash
# Start the Celery worker (only needed when JOB_BACKEND=celery). With the inline backend, jobs run in-process.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
export PYTHONPATH="apps/api:${PYTHONPATH:-}"
exec uv run celery -A aegis_api.jobs.tasks:celery_app worker --loglevel=info --concurrency="${WORKER_CONCURRENCY:-2}"
