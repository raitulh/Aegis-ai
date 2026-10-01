# Local setup

## Requirements

* Python 3.11+ · Node.js 22+ · PostgreSQL 16 · ~4 GB free RAM. No GPU is needed: model training happens elsewhere
  (see [EXTERNAL_TRAINING.md](EXTERNAL_TRAINING.md)); the platform only validates and scores prediction files.

## 1. Database

Use an existing PostgreSQL 16 or start one with Docker:

```bash
make db
docker exec databattles-db createdb -U databattles databattles_test   # for the test suite
```

The default URL is `postgresql+psycopg://databattles:databattles@localhost:5432/databattles` (see `backend/.env`).

## 2. Install

```bash
make setup
```

This creates `backend/.venv`, installs pinned dependencies (`requirements.lock`) plus dev tools, copies
`backend/.env.example → backend/.env` and `web/.env.example → web/.env.local`, and runs `npm ci`.

## 3. Migrate and seed

```bash
make migrate     # alembic upgrade head
make seed        # optional synthetic demo data; idempotent
```

The seed creates fictional universities, 32 demo users, 6 competitions (one finalized with certificates, one judged
hackathon, one members-only), 6 datasets, 11 projects, 4 courses, seeded GitHub repositories, discussions and badges.
Leaderboards come from **really scoring** generated submissions with the production evaluator, so the worker code path
is exercised during seeding. Seeding suppresses all outgoing email.

## 4. Run

```bash
make api      # FastAPI on :8000, reload on change, OpenAPI UI at http://localhost:8000/docs
make worker   # scoring, email outbox, GitHub sync, reminders (required for submissions to be scored)
make web      # Next.js on :3000
```

For tiny setups you may set `EMBEDDED_WORKER=true` to run the worker inside the API process instead.

Development email is not delivered — visit `/dev/mailbox` in the web app (disabled in production).

## 5. Test

```bash
make test        # backend: pytest against databattles_test (migrations run automatically)
make lint typecheck build
make test-e2e    # Playwright, requires the stack running with demo data
```

If Playwright's bundled browser is not installed, either run `npx playwright install chromium` or point
`CHROMIUM_PATH` at an existing Chromium binary.

## Useful environment variables (backend)

| Variable | Default | Notes |
|---|---|---|
| `ENV` | development | `production` enables strict startup checks |
| `SECRET_KEY` | dev value | ≥32 random chars in production (signing, token hashing) |
| `ENCRYPTION_KEY` | derived | Fernet key for OAuth tokens at rest |
| `WEB_BASE_URL` | http://localhost:3000 | Used in emails, OAuth redirects, CORS/CSRF origin allow-list |
| `COOKIE_SECURE` | false | Must be `true` behind HTTPS |
| `STORAGE_BACKEND` | local | `local` (`STORAGE_LOCAL_ROOT`) or `s3` (`S3_*`) |
| `EMAIL_BACKEND` | console | `smtp` with `SMTP_*` |
| `EVALUATOR_SANDBOX` | subprocess | `docker` for container isolation |
| `EVALUATOR_TIMEOUT_SECONDS` / `EVALUATOR_CPU_SECONDS` / `EVALUATOR_MEMORY_MB` | 120 / 90 / 1024 | Sandbox limits |
| `MAX_DATASET_FILE_MB` / `MAX_SUBMISSION_MB` / `MAX_IMAGE_MB` | 200 / 50 / 5 | Upload limits |
| `RATE_LIMIT_ENABLED` | true | In-memory limiter (single API process) |
| `DEMO_MODE` | true | Shows demo labelling in the UI footer |
| `GITHUB_*`, `GOOGLE_*` | empty | Optional integrations — see [GITHUB_SETUP.md](GITHUB_SETUP.md) |
