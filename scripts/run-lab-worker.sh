#!/usr/bin/env bash
# Local development: run every AI Scientist Lab background process in one terminal —
#   * Temporal worker(s) for LAB_WORKER_QUEUES (default "default,execution"; only when TEMPORAL_ADDRESS is set —
#     without Temporal the local durable engine runs workflows inside the API/Celery processes instead)
#   * the maintenance scheduler
#   * the event consumer
# Each process gets its own METRICS_PORT (9104 / 9103 / 9102). Ctrl-C (or SIGTERM) stops all of them gracefully;
# if any process exits, the others are stopped too. Production runs these as separate services
# (docker-compose.yml / deploy/k8s).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
[ -f .env ] && set -a && . ./.env && set +a

pids=()
stop_all() {
  trap - INT TERM
  for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  wait || true
}
trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM

if [ -n "${TEMPORAL_ADDRESS:-}" ]; then
  METRICS_PORT=9104 bash scripts/run-temporal-worker.sh "${LAB_WORKER_QUEUES:-default,execution}" &
  pids+=("$!")
else
  echo "[lab] TEMPORAL_ADDRESS not set — skipping Temporal workers (local workflow engine in use)"
fi
METRICS_PORT=9103 bash scripts/run-scheduler.sh &
pids+=("$!")
METRICS_PORT=9102 bash scripts/run-event-consumer.sh &
pids+=("$!")

echo "[lab] started ${#pids[@]} process(es): ${pids[*]}"
# Exit as soon as one process stops (crash or signal) and take the others down with it.
status=0
wait -n "${pids[@]}" || status=$?
echo "[lab] a process exited (status ${status}); stopping the rest"
stop_all
exit "$status"
