#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
export PYTHONPATH="apps/api:${PYTHONPATH:-}"
exec uv run uvicorn aegis_api.app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" --reload
