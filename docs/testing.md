# Testing

| Suite | Location | Needs | What it covers |
| --- | --- | --- | --- |
| Unit | `tests/unit` | nothing | engines (privacy detectors, policy compiler, risk, evidence chain), SSRF guard, SDK (mock transport), configuration parsing |
| Evaluation | `tests/evaluation` | nothing | known-answer cases for engine correctness (deterministic verdicts on fixed inputs) |
| Integration | `tests/integration` | PostgreSQL 16 + pgvector | the API end to end through FastAPI's test client against a real database with RLS: audit lifecycle and SSE, cancellation and duplicate delivery, finding lifecycle, Runtime Guard modes and approvals, Policy Studio (versions, diff, rollback, simulation), evidence export + offline verification + tamper detection, entitlements and the usage ledger, billing and outbound webhook signatures, SSRF, CSRF/Origin guard, login throttling, RBAC, **cross-tenant isolation** (core tables and every tenant table added by migrations 0003–0004), the DEMO sandbox, operator re-drive |
| Web unit | `apps/web/src/**/*.test.ts(x)` | nothing | API client path encoding and errors, safe redirects, plan-limit and DEMO notices, deterministic graph layout, badges |
| End-to-end | `apps/web/e2e` | PostgreSQL; Playwright Chromium | landing/pricing/trust; signup → system → audit → evidence verification; every dashboard page in the sandbox with zero page errors (incl. hydration) |

## Running

```bash
# Python (creates nothing: point it at a migrated database with the app role)
export TEST_DATABASE_URL=postgresql://aegis:aegis@127.0.0.1:5432/aegis_test
export TEST_DATABASE_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:5432/aegis_test
DATABASE_ADMIN_URL=$TEST_DATABASE_ADMIN_URL uv run alembic upgrade head
uv run pytest                     # integration tests are skipped automatically without a database

# Web
pnpm --filter @aegis/web test     # vitest
pnpm --filter @aegis/web build
E2E_DATABASE_URL=postgresql://aegis:aegis@127.0.0.1:5432/aegis_e2e \
E2E_DATABASE_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:5432/aegis_e2e \
  pnpm --filter @aegis/web test:e2e   # starts its own API (inline jobs) and `next start`
```

The database needs the `vector` extension and the `aegis` / `aegis_app` roles (see [deployment.md](deployment.md) or the CI workflow).

## CI

`.github/workflows/ci.yml` runs on every push and pull request:

- **backend** — ruff (lint + format), mypy, migrations (`upgrade head`, `alembic check` for model drift, one-step downgrade/upgrade round-trip), pytest with coverage against Postgres + Redis.
- **web** — eslint, tsc, vitest, production build.
- **e2e** — Playwright against a real API and database.
- **security** — `pip-audit` on the locked Python dependencies, `pnpm audit --prod`.
- **docker** — both images build.

## Principles

- Integration tests use a real PostgreSQL with Row-Level Security; tenancy is never mocked.
- Numbers in tests come from running the code (audits, simulations, usage) rather than fixtures asserted against themselves.
- Bugs found in end-to-end testing get a regression test at the lowest level that reproduces them (for example, sandbox seeding inside an uncommitted transaction is covered by an integration test, and hydration mismatches by the Playwright page-error check).
