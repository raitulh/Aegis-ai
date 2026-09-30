#!/usr/bin/env bash
# Start a Temporal worker for the AI Scientist Lab (requires TEMPORAL_ADDRESS, e.g. localhost:7233).
#
#   scripts/run-temporal-worker.sh                    # default queue (workflows + default activities)
#   scripts/run-temporal-worker.sh execution          # execution queue (sandboxed experiments)
#   scripts/run-temporal-worker.sh default,execution  # both in one process (local development)
#
# The execution queue runs experiment containers through EXECUTION_BACKEND (local_docker needs access to the
# Docker socket — equivalent to root on this host; see docker-compose.yml). Metrics: METRICS_PORT (default 9104).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
[ -f .env ] && set -a && . ./.env && set +a
export PYTHONPATH="apps/api:.:${PYTHONPATH:-}"
QUEUES="${1:-${TEMPORAL_WORKER_QUEUES:-default}}"
exec uv run python -m aegis_api.lab.workflows.worker --queues "$QUEUES"
