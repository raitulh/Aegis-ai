#!/usr/bin/env bash
# Run database migrations (owner connection). Usage: bash scripts/migrate.sh [upgrade head|downgrade -1|...]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
exec uv run alembic "${@:-upgrade head}"
