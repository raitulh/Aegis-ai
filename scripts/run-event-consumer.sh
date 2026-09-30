#!/usr/bin/env bash
# Start the lab event consumer (outbox events → signed webhook deliveries, deduplicated per event).
# Knobs: EVENT_CONSUMER_INTERVAL_SECONDS (default 2), EVENT_CONSUMER_SETTLE_SECONDS (default 30),
# METRICS_PORT (default 9102).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
export PYTHONPATH="apps/api:.:${PYTHONPATH:-}"
exec uv run python -m aegis_api.lab.events.consumer
