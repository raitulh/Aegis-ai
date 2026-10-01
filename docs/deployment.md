# Deployment

Aegis runs as **web** (Next.js), **api** (FastAPI), **worker** (Celery) and **scheduler** (Celery beat) on top of **PostgreSQL 16 with pgvector** and **Redis**. The same two images serve every role.

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

| Service | Image | Role |
| --- | --- | --- |
| `db` | `pgvector/pgvector:pg16` | PostgreSQL; `docker/postgres-init.sql` creates the `aegis_app` group and the `aegis` login role on first init |
| `redis` | `redis:7-alpine` | Celery broker and the real-time event bus for audit progress |
| `migrate` | `docker/Dockerfile.api` | one-shot: `alembic upgrade head`, then the demo reference seed (`DEMO_SEED_ON_INIT`) |
| `api` | `docker/Dockerfile.api` | FastAPI on `:8000` (health check: `/health`) |
| `worker` | `docker/Dockerfile.api` | Celery worker (`acks_late`, re-queue on worker loss) |
| `scheduler` | `docker/Dockerfile.api` | Celery beat: one maintenance tick every `SCHEDULER_INTERVAL_SECONDS` |
| `web` | `docker/Dockerfile.web` | Next.js standalone server on `:3000`; proxies `/bff/api/v1/*` to `API_INTERNAL_URL` |

`api`, `worker`, `scheduler` and `web` wait for `migrate` to finish. The API origin is never exposed to browsers; only the web origin needs to be public (expose the API separately only for SDK/CI/MCP clients).

## Production checklist

1. **Secrets** (the API refuses to start in production without them):
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # SECRETS_ENCRYPTION_KEY
   python -c "import secrets; print(secrets.token_urlsafe(48))"                                 # API_KEY_PEPPER
   python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"       # EVIDENCE_SIGNING_KEY
   python -c "import secrets; print(secrets.token_urlsafe(32))"                                 # METRICS_TOKEN
   ```
   Store them in your secret manager. Losing `SECRETS_ENCRYPTION_KEY` makes stored provider credentials and webhook secrets unreadable; changing `API_KEY_PEPPER` invalidates every API key; changing `EVIDENCE_SIGNING_KEY` changes the public key (keep the old one to verify old packages).
2. `ENVIRONMENT=production`, `WEB_BASE_URL=https://…` (must be https), `CORS_ORIGINS` set to your web origin (no `*`), `NEXT_PUBLIC_SITE_URL=https://…` on the web service (enables HSTS and `upgrade-insecure-requests`).
3. **TLS** at your reverse proxy / load balancer. Set `TRUSTED_PROXY_HOPS` to the number of proxies in front of the API so client IPs (rate limits, login throttling) are read correctly; leave it `0` if clients reach the API directly.
4. `ALLOW_PRIVATE_NETWORK_TARGETS` unset (blocked in production). Set it to `true` only for single-tenant deployments that must reach internal model endpoints, and understand that any workspace admin can then point providers and webhooks at internal addresses.
5. Database roles: the app connects as `aegis` (member of `aegis_app`, RLS enforced); migrations use the owner via `DATABASE_ADMIN_URL`. On managed Postgres create the roles once:
   ```sql
   CREATE ROLE aegis_app NOLOGIN;
   CREATE ROLE aegis LOGIN PASSWORD '<strong password>';
   GRANT aegis_app TO aegis;
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   then run `alembic upgrade head` with `DATABASE_ADMIN_URL` pointing at the owner.
6. `JOB_BACKEND=celery` with a worker and **exactly the scheduler you intend** (multiple beat instances are safe — a Postgres advisory lock serialises ticks — but one is enough).
7. Email (`SMTP_*`, `EMAIL_FROM`) for invitations, alerts and `SALES_INBOX`. Without SMTP, invitation links are shown once to the inviter.
8. Billing: leave `BILLING_PROVIDER=none` unless you configure Stripe (see [billing.md](billing.md)).
9. Monitoring: scrape `/metrics` with `Authorization: Bearer $METRICS_TOKEN`; alert on `/ready` (see [operations.md](operations.md)).
10. Backups: PostgreSQL point-in-time recovery; Redis holds no durable state.

## Health endpoints

| Path | Meaning | Use for |
| --- | --- | --- |
| `GET /live` | process is up | liveness probe |
| `GET /health` | process is up (with version/environment) | container health check |
| `GET /ready` | database reachable, migrations at the required revision, Redis reachable when configured, not draining; **503** otherwise | readiness probe / load-balancer gating |
| `GET /metrics` | Prometheus exposition (token-protected) | monitoring |

On shutdown the API first reports not-ready (so the load balancer drains it), then stops the in-process scheduler and closes event streams. Inline jobs interrupted by a shutdown are recovered by the stale-run reaper; Celery tasks are acknowledged late, so the broker re-delivers them if a worker dies mid-task (the ledger claim makes the re-delivery safe).

## Scaling

- **api** and **web** are stateless; scale horizontally. Live audit progress is fanned out through Redis, so any API replica can serve any stream.
- **worker** scales horizontally (`WORKER_CONCURRENCY` per container). Jobs are claimed atomically with a lease; a crashed worker's job is recovered after `JOB_LEASE_TIMEOUT_SECONDS`.
- **PostgreSQL** is the system of record (managed service with PITR recommended). Size connection pools (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`) × replicas below the server's connection limit.

## Single-process mode

Without Redis (`JOB_BACKEND=inline`), jobs run in an in-process thread pool and the API runs the maintenance scheduler itself. This is fine for evaluation and small single-tenant installs; use Celery for anything with real traffic.

## Images and releases

- `docker/Dockerfile.api` — Python 3.12 slim, `uv sync --frozen`, non-root user, `HEALTHCHECK` on `/health`.
- `docker/Dockerfile.web` — multi-stage Node build producing Next.js standalone output, non-root.
- `.github/workflows/release.yml` publishes both images to GHCR for `v*.*.*` tags with SBOM and provenance attestations.
