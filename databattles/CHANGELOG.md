# Changelog

## 1.0.0 — initial release

* FastAPI modular-monolith API (266 operations) with PostgreSQL, Alembic migration `0001`, append-only audit log trigger.
* Competitions end to end: creation wizard, visibility modes, teams, sandboxed CSV scoring, live/final leaderboards,
  snapshots and corrections, judged events, analytics, certificates and badges.
* Datasets, projects, open-source hub with GitHub linking/webhooks, learning (courses, quizzes, challenges, paths),
  discussions and moderation, notifications with email outbox, full-text search, organizations and university admin,
  sponsor dashboard, platform admin.
* Next.js 16 web app (95 routes) with a dark-first design system, light theme and reduced-motion support.
* Synthetic demo seed with really-scored submissions; purge tool.
* Tests: 42 backend tests, 11 Playwright E2E tests; CI workflow; Docker Compose deployment files; documentation.
