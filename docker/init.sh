#!/usr/bin/env bash
# ==============================================================================
# One-shot initialiser: apply migrations (owner/admin connection) and optionally
# seed the demo workspace. Run once by the compose `migrate` service; the API and
# worker wait for it to finish before starting.
# ==============================================================================
set -euo pipefail

echo "[init] applying database migrations…"
alembic upgrade head

if [ "${DEMO_SEED_ON_INIT:-true}" = "true" ]; then
  echo "[init] seeding demo workspace (idempotent)…"
  python scripts/seed_demo.py || echo "[init] demo seed skipped/failed (non-fatal)"
fi

echo "[init] done."
