# Deployment

Aegis runs as four application processes — **web**, **api**, **worker** — plus **PostgreSQL 16 (pgvector)** and **Redis**. The provided `docker-compose.yml` wires all of them together for local and single-host deployments; the same images run behind an orchestrator.

## Docker Compose (fastest)

```bash
cp .env.example .env     # optional locally; required-secrets apply in production
docker compose up --build
```

Services:

| Service | Image / build | Role |
| --- | --- | --- |
| `db` | `pgvector/pgvector:pg16` | PostgreSQL with pgvector; bootstraps the `aegis` app role via `docker/postgres-init.sql` |
| `redis` | `redis:7-alpine` | Celery broker/back-end |
| `migrate` | `docker/Dockerfile.api` | one-shot: `alembic upgrade head` + demo seed, then exits |
| `api` | `docker/Dockerfile.api` | FastAPI (uvicorn) on `:8000` |
| `worker` | `docker/Dockerfile.api` | Celery worker (same image, different command) |
| `web` | `docker/Dockerfile.web` | Next.js standalone server on `:3000` |

`api`, `worker`, and `web` gate on the `migrate` service completing successfully and on `db`/`redis` being healthy, so the stack comes up in the right order without manual steps. The `web` service proxies to the API over the internal Docker network (`API_INTERNAL_URL=http://api:8000`); that origin is never exposed to the browser.

### Images

- **`docker/Dockerfile.api`** — Python 3.12 slim, dependencies installed from `uv.lock` with `uv sync --frozen` in a cached layer, runs as a non-root user, and serves the API by default (`uvicorn aegis_api.app:app`). The worker service overrides the command to run Celery. A `curl`-based `HEALTHCHECK` hits `/health`.
- **`docker/Dockerfile.web`** — multi-stage Node 20 + pnpm build that emits Next.js **standalone** output; the runtime stage copies only the pruned server bundle, static assets, and `public/`, and runs `node apps/web/server.js` as non-root.

## Configuration

All configuration is environment-based — see [`.env.example`](../.env.example) for the annotated list. The essentials:

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `development` \| `test` \| `production` |
| `DATABASE_URL` | app-role connection (RLS enforced) |
| `DATABASE_ADMIN_URL` | owner connection (migrations, seeding, identity) |
| `REDIS_URL` | broker; when unset, jobs run inline in-process |
| `JOB_BACKEND` | `celery` (needs Redis) \| `inline` |
| `SECRETS_ENCRYPTION_KEY` | **required in production** — Fernet key for provider secrets |
| `API_KEY_PEPPER` | **required in production** — pepper for API-key hashing |
| `CORS_ORIGINS` | allowed browser origins |
| `API_INTERNAL_URL` | server-side origin the web BFF proxies to |

The API **refuses to start** in production without both secrets. Generate them:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Database provisioning

The application connects as a **non-owner** role (`aegis`) that is a member of `aegis_app`; RLS policies apply to it. Migrations, seeding, and the identity layer use the **owner** role.

- Under Compose, `docker/postgres-init.sql` creates the `aegis_app` group and the `aegis` login role on first volume init; the migration then applies the grants.
- On managed Postgres, run the equivalent once (create `aegis_app NOLOGIN`, create the `aegis` login role, `GRANT aegis_app TO aegis`), point `DATABASE_ADMIN_URL` at the owner, and run `alembic upgrade head`.

Migrations are Alembic; every migration has a working `downgrade()`.

## Health & readiness

- `GET /health` — liveness: process is up (`{"status":"ok",...}`).
- `GET /ready` — readiness: checks the database and (if configured) Redis, and reports the active job backend. Returns `degraded` if a dependency is unreachable. Use `/health` for liveness probes and `/ready` for readiness/gating.

## Scaling

- **API** is stateless — run multiple replicas behind a load balancer. Sessions are cookie/token based and stored in the database, so any replica can serve any request.
- **Worker** scales horizontally: add replicas or raise `WORKER_CONCURRENCY`. Audits are dispatched after commit, so they survive an API restart.
- **Web** is stateless standalone Next.js; scale replicas freely.
- **Database** is the stateful core — use managed Postgres with backups. Evidence immutability triggers travel with the schema.

For a single-container or serverless-worker setup, set `JOB_BACKEND=inline` and omit Redis; audits then run in an in-process thread pool. This is simplest but couples job execution to the API process — prefer Celery for anything beyond a demo.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and PR: Ruff + ESLint, mypy + `tsc`, the full pytest/vitest suites against a real Postgres + Redis, the web build, Playwright E2E, and a build of both Docker images. It mirrors the same role/migration bootstrap described above.
