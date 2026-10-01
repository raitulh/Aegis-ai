# DataBattles

A university AI **competition, learning, open-source and reputation** platform.

**Learn → Build → Compete → Contribute → Verify → Showcase → Connect**

Students take short courses, train models anywhere (Colab, Kaggle, their laptop — no GPU needed on the platform), upload
predictions that are scored reproducibly in a sandbox, contribute to campus open source, and collect results,
certificates and badges that anyone can verify. Universities, clubs and sponsors host events, verify membership and
see privacy-respecting analytics.

| Layer | Technology |
|---|---|
| Web | Next.js 16 (App Router), React 19, TypeScript (strict), Tailwind CSS v4, TanStack Query, Radix UI |
| API | FastAPI, Pydantic v2, SQLAlchemy 2 (typed), Alembic |
| Database | PostgreSQL 16 (also the job queue and the full-text search index) |
| Worker | Python process: sandboxed scoring, email outbox, GitHub sync, reminders, maintenance |
| Storage | Local volume or any S3-compatible bucket (signed, expiring download URLs) |

The backend is a **modular monolith** (`backend/app/modules/*`), sized for a single ~8 GB machine and ready to split later.

---

## Quick start (local development)

Prerequisites: Python 3.11+, Node 22+, PostgreSQL 16 (or Docker for `make db`).

```bash
make setup          # venv + pip install, npm ci, copies .env examples
make db             # optional: PostgreSQL in Docker (then create databattles_test, see output)
make migrate        # alembic upgrade head
make seed           # synthetic demo data (really scored leaderboards)

# three terminals
make api            # http://localhost:8000  (OpenAPI docs at /docs in development)
make worker         # background jobs — required for scoring and email
make web            # http://localhost:3000
```

Demo accounts (password `DemoPass!2026`, **demo data only**):

| Email | Role |
|---|---|
| `student@example.com` | Student with submissions, a certificate, badges, courses and merged PRs |
| `organizer@example.com` | Competition organizer / club owner |
| `uniadmin@example.com` | University administrator |
| `judge@example.com` | Hackathon judge |
| `sponsor@example.com` | Sponsor organization owner |
| `maintainer@example.com` | Open-source maintainer |
| `moderator@example.com` | Community moderator |
| `admin@example.com` | Platform administrator |

Emails are not sent in development — open **http://localhost:3000/dev/mailbox** to click verification links.
Remove demo data with `python -m app.seed purge` (or the admin *Tools* page).

## Docker (single node)

```bash
cp .env.example .env    # set POSTGRES_PASSWORD and SECRET_KEY at minimum
docker compose up -d --build
docker compose run --rm api python -m app.seed   # optional demo data (sets is_demo on everything it creates)
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for HTTPS, backups, S3, SMTP, GitHub and the Docker evaluation sandbox.

## Commands

| Command | What it does |
|---|---|
| `make test` | Backend unit + integration + authorization tests (needs `databattles_test` DB) |
| `make test-e2e` | Playwright end-to-end tests against a running, seeded stack |
| `make lint` / `make typecheck` / `make build` | Ruff, ESLint, `tsc --noEmit`, Next production build |
| `make openapi` | Regenerate `backend/openapi.json` and `web/src/lib/api-schema.d.ts` |
| `make reset-demo` | Purge and recreate demo data |

## Repository layout

```
backend/
  app/core/          config, db, security (argon2, signing, Fernet), CSRF & body-limit middleware, permissions, audit, rate limits
  app/models/        SQLAlchemy models (single source of schema truth)
  app/modules/       auth, users, orgs, competitions, teams, submissions, leaderboards, judging, credentials,
                     datasets, projects, opensource, learning, discussions, moderation, notifications, search,
                     analytics, admin, billing, meta, files
  app/evaluation/    stdlib-only evaluator (metrics, validation, runner) + sandboxes (subprocess, docker)
  app/jobs/          PostgreSQL job queue (SKIP LOCKED), periodic tasks
  app/seed/          synthetic demo data generator, purge
  alembic/           migrations (0001 = full schema + audit-log immutability trigger)
  tests/             pytest suite (runs against real PostgreSQL)
web/
  src/app/           95 routes (App Router)
  src/components/    ui (design system), shell, charts, domain cards, feature folders
  src/lib/           API client (CSRF, errors, uploads), hooks, query keys, formatting
  e2e/               Playwright tests
docs/                architecture, setup, deployment, API, security, guides …
```

## What was verified in this build

* Backend: **42 pytest tests pass** against PostgreSQL 16 (auth flows, CSRF, rate limiting, competition lifecycle
  from creation to finalization and corrections, hidden ground truth never exposed, idempotent uploads, private/university/
  invite-only visibility, teams, judging, certificates/badges, org domain verification, learning quiz grading,
  discussions/moderation, signed dataset downloads, GitHub webhook signatures/idempotency/out-of-order handling,
  notifications, search visibility, evaluator sandbox and metrics, sanitization, SSRF/zip-bomb guards).
* Migrations: `alembic upgrade head` → `alembic check` reports no drift; downgrade/upgrade round-trip works.
* Seed: 158 synthetic submissions are scored by the real sandboxed evaluator; one competition is finalized with certificates.
* Web: `tsc --noEmit` clean, ESLint 0 errors, `next build` succeeds (95 routes). A crawl of every major route as
  visitor, student, organizer, judge, org admin, sponsor, moderator and admin rendered without client errors.
* **11 Playwright E2E tests pass** locally (Chromium), including signing in, uploading a CSV through the UI and
  seeing it scored by the worker.

Not verified here: Docker image builds, real SMTP delivery, S3 storage, Google/GitHub OAuth against live providers,
the Docker evaluation sandbox, and load/performance testing. See [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Setup](docs/SETUP.md) · [Deployment](docs/DEPLOYMENT.md) · [API](docs/API.md) ·
[Security](docs/SECURITY.md) · [Evaluator authoring](docs/EVALUATOR_AUTHORING.md) · [GitHub setup](docs/GITHUB_SETUP.md) ·
[External training](docs/EXTERNAL_TRAINING.md) · [Feature matrix](docs/FEATURE_MATRIX.md) · [Roadmap](docs/ROADMAP.md) ·
[Known limitations](docs/KNOWN_LIMITATIONS.md)

Guides: [Organizers](docs/guides/ORGANIZER.md) · [University admins](docs/guides/UNIVERSITY_ADMIN.md) ·
[Judges](docs/guides/JUDGE.md) · [Sponsors](docs/guides/SPONSOR.md) · [Moderators](docs/guides/MODERATOR.md)

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md)

> All seeded organizations, people, datasets, scores and repositories are **synthetic** and labelled "Demo data" in
> the UI. They do not describe real institutions or results.
