#!/usr/bin/env bash
# ==============================================================================
# One-shot initialiser: apply migrations (owner/admin connection) and optionally
# seed demo data. Run once by the compose `migrate` service (the API and workers
# wait for it to finish) and by the Kubernetes migrate Job.
#
#   DEMO_SEED_ON_INIT=true       seed the Aegis assurance demo workspace (default: true)
#   LAB_DEMO_SEED_ON_INIT=true   seed the AI Scientist Lab demo (default: false here;
#                                docker-compose.yml turns it on for local dev).
#                                Skipped when scripts/seed_lab_demo.py is absent.
#
# Seeds are idempotent and non-fatal: a failed seed never blocks startup, a
# failed migration always does. The lab seed refuses to run in production on
# its own (see scripts/seed_lab_demo.py); do not enable it there.
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[init] applying database migrations…"
alembic upgrade head

if [ "${DEMO_SEED_ON_INIT:-true}" = "true" ]; then
  echo "[init] seeding demo workspace (idempotent)…"
  python scripts/seed_demo.py || echo "[init] demo seed skipped/failed (non-fatal)"
fi

if [ "${LAB_DEMO_SEED_ON_INIT:-false}" = "true" ]; then
  if [ -f scripts/seed_lab_demo.py ]; then
    echo "[init] seeding AI Scientist Lab demo (idempotent)…"
    python scripts/seed_lab_demo.py || echo "[init] lab demo seed skipped/failed (non-fatal)"
  else
    echo "[init] scripts/seed_lab_demo.py not present — lab demo seed skipped"
  fi
fi

echo "[init] done."
