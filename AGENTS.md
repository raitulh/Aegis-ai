# AGENTS.md

Conventions for humans and coding agents working in this repository. Keep changes consistent with what is already here; when in doubt, match the surrounding code.

## Project shape

Monorepo with two package managers:

- **Python** (`uv` workspace) — `apps/api` (FastAPI), `engines/` (pure evaluators), `packages/sdk/python`, `packages/mcp`, `database/`.
- **JavaScript** (`pnpm` workspace) — `apps/web` (Next.js 16), `packages/{ui,types,config}`.

`engines/` and `apps/api` are import roots (`PYTHONPATH=apps/api:.`). `database/` holds the Alembic env and migrations.

## Golden rules

1. **Deterministic where possible, model-assisted where useful, evidence-backed everywhere.** Engine verdicts must be reproducible. A model may *assist* a judgment but is never the *sole* basis for a finding — always pair it with a deterministic signal and record both in evidence.
2. **Engines are pure.** Code under `engines/` must not import the database, FastAPI, or app services. Engines take plain inputs and return plain results. This keeps them unit-testable and reusable.
3. **Never claim legal compliance.** Use "compliance assessment", "readiness", "aligned to". Never "certified", "compliant", or "guarantees".
4. **The red-team engine ships no attacks.** It runs against an *imported* corpus only. Do not add built-in attack payloads, jailbreak strings, or mutation strategies.
5. **Respect tenancy.** All tenant data access goes through the RLS-scoped session (`get_db`). Only the identity/onboarding layer and migrations/seeding use the admin session that bypasses RLS — see `docs/security.md`.
6. **Evidence is immutable.** Never `UPDATE`/`DELETE` `evidence` rows or edit the hash chain; database triggers reject it.

## Backend conventions

- Settings come from `aegis_api.config.get_settings()` (Pydantic). Don't read `os.environ` directly in app code.
- Sessions: `get_db` (RLS-scoped, request-bound) for tenant data; `session_factory(admin=True)` only for identity, migrations, seeding.
- Errors use the envelope `{"error": {"code", "message", "request_id", "details"}}` via `aegis_api.errors`. Raise the app's typed errors rather than bare `HTTPException` where one fits.
- Long-running work is dispatched as a job (`aegis_api.jobs`) after commit — never block the request thread on an audit.
- Schemas (`aegis_api.schemas`) are the API contract. `ORMModel` coerces types (e.g. UUID → str); keep response models explicit.

## Frontend conventions

- Server state is TanStack Query (`src/lib/queries.ts`); the API client is `src/lib/api.ts` targeting `/bff/api/v1`.
- All API traffic goes through the runtime BFF route handler (`src/app/bff/[...path]/route.ts`) so cookies and SSE pass through — do not call the API origin directly from the browser.
- Styling is Tailwind 4 with CSS variables (`--color-*`, `--radius-*`). Reuse primitives in `src/components/ui`.
- Client components that use hooks/handlers need `"use client"`.

## Definition of done

Before considering a change complete, all of these must pass:

```bash
pnpm lint          # ruff + eslint
pnpm typecheck     # mypy + tsc
pnpm test          # pytest + vitest
pnpm build         # web production build
```

For anything touching the API surface or engine behaviour, add or update tests under `tests/` (unit for engines, integration for API/RLS, evaluation for engine correctness). Migrations must have a working `downgrade()`.

## Commit style

Small, focused commits with imperative subjects (e.g. `Fix counterfactual pooling for low-N cases`). Don't commit `.env`, build output, or caches (see `.gitignore`).
