#!/usr/bin/env bash
# Start the lab maintenance scheduler (pending workflow starts, stale-run recovery, approval expiry, retention,
# usage roll-ups). Safe to run more than once: each task takes a global advisory lock.
# Knobs: SCHEDULER_INTERVAL_SECONDS (default 15), METRICS_PORT (default 9103).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
[ -f .env ] && set -a && . ./.env && set +a
export PYTHONPATH="apps/api:.:${PYTHONPATH:-}"
exec uv run python -m aegis_api.lab.workflows.scheduler
