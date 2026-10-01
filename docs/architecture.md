# Architecture

Aegis is a monorepo: a Next.js console and website, a FastAPI service, pure evaluation engines, a job system with a ledger, and PostgreSQL as the system of record (with Row-Level Security and append-only evidence).

## System overview

```
                         ┌───────────────────────────────────────────────────────────┐
  Browser ─────────────▶ │ WEB  Next.js 16 / React 19 (App Router)                    │
                         │  · website (static) + dashboard (dynamic, session-seeded)  │
                         │  · TanStack Query; resumable SSE hook with polling fallback │
                         │  · BFF route handler /bff/api/v1/* (cookies, SSE, origin)   │
                         └──────────────────────────────┬────────────────────────────┘
                                                        │ API_INTERNAL_URL
  Python SDK · MCP · CI ───────────────────────────────▶▼
                         ┌───────────────────────────────────────────────────────────┐
                         │ API  FastAPI /api/v1                                       │
                         │  middleware: request id + traceparent, metrics, secure     │
                         │  headers, Origin guard, body limits, CORS                  │
                         │  auth (cookie / bearer / API key) → RBAC → RLS session     │
                         │  services: audits (orchestrator), findings, evidence,      │
                         │  runtime guard, policy studio, assurance, entitlements …   │
                         └──────┬───────────────────┬──────────────────┬─────────────┘
                                │                   │                  │
                ┌───────────────▼──────┐ ┌──────────▼─────────┐ ┌──────▼──────────────┐
                │ JOBS                 │ │ ENGINES (pure)     │ │ REAL-TIME           │
                │ ledger (job_runs)    │ │ fairness, safety,  │ │ audit events → Redis│
                │ → Celery workers     │ │ privacy, security, │ │ pub/sub → SSE (any  │
                │   (or inline pool)   │ │ agent, redteam,    │ │ replica), resumable │
                │ maintenance tick     │ │ policy, runtime,   │ │ by Last-Event-ID    │
                │ (beat / in-process)  │ │ evidence, risk …   │ └─────────────────────┘
                └───────────────┬──────┘ └──────────┬─────────┘
                                └─────────┬─────────┘
                ┌─────────────────────────▼────────────────────────────────────────┐
                │ PostgreSQL 16 + pgvector — RLS on every tenant table;            │
                │ append-only triggers on evidence, audit log, usage ledger,       │
                │ published policy versions                                         │
                └──────────────────────────────────────────────────────────────────┘
  Model providers (optional, SSRF-guarded): Ollama · Gemini · OpenAI · Anthropic · simulators (demo)
```

## Components

**Web (`apps/web`).** The website is statically rendered; the dashboard is dynamic. The dashboard layout fetches the session on the server for the auth guard and seeds it into the query cache, so permission-gated UI renders identically on server and client. All API calls go through the BFF route handler, which keeps cookies first-party, streams request and response bodies (including SSE), and enforces same-origin writes. Shared primitives live in `src/components/ui` (forms, dialogs with focus management, data tables with link rows, plan-limit and DEMO notices).

**API (`apps/api/aegis_api`).** Routers group the surface: auth, systems, audits, policies, findings, evidence, runtime, runtime policies, assurance, billing/usage, webhooks, graph, workspace, public, health. Errors use one envelope (`{"error": {code, message, request_id, details}}`). Mutating endpoints accept `Idempotency-Key`; replays return the stored response.

**Engines (`engines/`).** Pure Python with no database or web imports: evaluators (fairness, hallucination/grounding, safety, privacy, security, agent actions), red team (imported corpus only), policy compiler and compliance mappings, **runtime** (event schema, signals, redaction, policy DSL compiler and evaluator, templates), **evidence** (canonical hashing, chain and package verification — the same file is shipped as `verify.py`), risk scoring, test generation, monitoring, and model providers behind a router. A model may assist a judgment but never replaces the deterministic signal.

**Jobs (`aegis_api/jobs`).** Every async job is recorded in `job_runs` with an idempotency key before dispatch (after the owning transaction commits). Workers claim jobs atomically; failures are classified as transient (retried with backoff, then dead-lettered) or permanent. A maintenance tick (Celery beat, or an in-process scheduler with the inline backend) runs under a PostgreSQL advisory lock. Operators use `python -m aegis_api.ops`.

**Evidence.** Each evidence record is content-hashed and chained per audit; Runtime Guard decisions are chained per system. Signed export packages and verification are described in [evidence.md](evidence.md).

## Audit execution

```
queued ──claim (UPDATE … WHERE status='queued' RETURNING, lease)──▶ running
  setup → test_generation → inference → evaluation → evidence → risk_scoring → policy_mapping → report_generation
  ── conditional terminal update (only if still running and not cancelled) ──▶ completed | partially_completed | failed | cancelled
```

- **Progress** is written through a separate, immediately committed channel and published to Redis, so the SSE stream is live and resumable while results stay in one transaction.
- **Heartbeats** extend the lease; a reaper re-queues runs whose heartbeat stopped (bounded attempts).
- **Cancellation** is cooperative: the runner checks between stages; the terminal write never overwrites `cancelled`.
- **Findings** are deduplicated per `(organization, system, fingerprint)`; each audit that observes a finding records an occurrence (so per-audit finding lists and comparisons are exact); a re-detected resolved finding re-opens as a regression. Finding numbers are allocated atomically.
- **After completion**: usage is recorded, the regression report against the previous audit and the known-good baseline is computed, notifications and webhooks are queued.

## Runtime Guard

```
agent ─▶ POST /runtime/check | /runtime/events
          normalize + derive signals + redact (engines.runtime.schema)
          resolve published policies assigned to org / environment / system
          evaluate (engines.runtime.policy): most restrictive matching action wins
          mode: observe → allow · audit → allow + finding/evidence · enforce → verdict (+ approval)
          store event, append evidence (advisory-locked chain), record usage, metrics
```

See [runtime-guard.md](runtime-guard.md) and [policy-studio.md](policy-studio.md).

## Continuous assurance

Schedules (interval and/or change events) and change triggers from CI/CD create audits with risk-based category selection for the change type. Every completed audit is compared with the previous audit and the system's baseline; regressions (new high/critical findings or a score drop > 5 points) raise notifications and the `regression.detected` webhook.

## Entitlements

Plans are configuration (`billing/plans.py`, `PLANS_JSON`, subscription overrides). Quotas are checked at the point of use from the append-only usage ledger and live counts; see [billing.md](billing.md).

## Multi-tenancy

Every tenant row carries `organization_id`; the app role is subject to RLS keyed on `app.current_org_id`, set per transaction. The identity layer, onboarding, migrations, seeding and maintenance use the owner role. See [security.md](security.md).

## Request path

```
Browser → /bff/api/v1/… (same-origin check, header allow-list)
        → FastAPI middleware (request id, traceparent, metrics, Origin guard, size limit)
        → authenticate (short-lived identity session) → require(permission)
        → RLS-scoped session → service → engines / database
        → response envelope (Set-Cookie and SSE relayed through the BFF)
```
