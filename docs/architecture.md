# Architecture

Aegis is a monorepo that ships a web console, a REST API, a set of pure evaluation engines, a job queue, and an immutable evidence store on top of PostgreSQL. This document explains how the pieces fit together and how a single audit flows through the system.

## System overview

```
                             ┌────────────────────────────────────────────────┐
   Browser ────────────────▶ │  WEB — Next.js 16 / React 19 (App Router)       │
                             │  · marketing + dashboard (35 pages)             │
                             │  · TanStack Query for server state              │
                             │  · BFF route handler: /bff/[...path]            │
                             └───────────────────────────┬────────────────────┘
                                 cookies + SSE, per-req   │  API_INTERNAL_URL
                                                          ▼
   Python SDK ───┐          ┌────────────────────────────────────────────────┐
   MCP server ───┼────────▶ │  API — FastAPI (96 endpoints)                   │
   curl / CI ────┘          │  ┌──────────────┬───────────────┬────────────┐  │
                             │  │ auth + RBAC  │ ASGI middleware│ error      │  │
                             │  │ sessions,    │ req-id, secure │ envelope   │  │
                             │  │ API keys     │ headers, limits│            │  │
                             │  └──────────────┴───────────────┴────────────┘  │
                             │  services · orchestrator (AuditRunner)          │
                             └───┬───────────────────┬───────────────────┬─────┘
                                 │ enqueue           │ read/write         │ append
                     ┌───────────▼─────────┐ ┌───────▼────────┐ ┌─────────▼────────┐
                     │ QUEUE               │ │ ENGINES        │ │ EVIDENCE         │
                     │ Celery + Redis      │ │ deterministic  │ │ hash-chain +     │
                     │  · after_commit     │ │ evaluators     │ │ DB triggers      │
                     │    dispatch         │ │ (pure Python)  │ │ (append-only)    │
                     │ or inline threadpool│ │                │ │                  │
                     └───────────┬─────────┘ └───────┬────────┘ └─────────┬────────┘
                                 │                    │                    │
                                 └────────────────────▼────────────────────┘
                             ┌────────────────────────────────────────────────┐
                             │  DATABASE — PostgreSQL 16 + pgvector            │
                             │  · Row-Level Security (per-org isolation)       │
                             │  · 60 tables · HNSW vector + GIN FTS indexes    │
                             │  · immutability triggers on evidence/audit log  │
                             └────────────────────────────────────────────────┘

   PROVIDERS (optional, via ModelRouter): Ollama · Gemini · OpenAI · Anthropic
   Embeddings: local hash (default) · Ollama · Gemini · OpenAI
```

## Components

### Web (`apps/web`)
Next.js 16 App Router. The browser never talks to the API origin directly; every request goes through a **runtime BFF proxy** (`src/app/bff/[...path]/route.ts`) that reads `API_INTERNAL_URL` per request and forwards cookies and streamed bodies. This keeps auth cookies first-party and lets Server-Sent Events (live audit progress) pass through unbuffered. Server state is managed by TanStack Query; the API client (`src/lib/api.ts`) targets `/bff/api/v1`.

### API (`apps/api/aegis_api`)
FastAPI, assembled in `app.py`. Routers group the surface (auth, systems, audits, policies, findings, evidence, operations, workspace, demo, health). Cross-cutting concerns are pure-ASGI middleware: request IDs, secure headers, and body-size limits. Every error is returned as a consistent envelope. Services hold business logic; the **orchestrator** (`services/orchestrator.py`, `AuditRunner`) drives an audit through its stages.

### Engines (`engines/`)
Seventeen domains of **pure** Python — no database, no FastAPI, no app imports. Each takes plain inputs and returns plain results, which makes them independently unit-testable and reusable by the API, the worker, and monitoring. Domains include `fairness`, `hallucination`, `safety`, `privacy`, `security`, `agent` (trace auditing), `policy` (compiler), `compliance` (framework mapping), `evidence`, `risk`, `generation` (test generation), `monitoring`, and `providers`. A `ModelRouter` offers optional model-assisted judgments; these augment but never replace deterministic signals.

### Queue (`aegis_api/jobs`)
Audits and other long-running work run out-of-band. With `REDIS_URL` set, jobs run on **Celery**; otherwise an **inline thread-pool** backend runs them in-process (used in tests and single-container setups). Dispatch happens on a SQLAlchemy `after_commit` event, so a job never starts before its owning transaction is durable.

### Evidence store
Every judgment produces an evidence record linked into a per-scope **hash chain** (`ChainState`). Database triggers (`aegis.protect_evidence`) make evidence and the audit log **append-only** — updates and deletes are rejected at the database, not just in application code. This is what lets a finding cite tamper-evident proof.

### Database
PostgreSQL 16 with pgvector. 60 tables under Row-Level Security for tenant isolation, HNSW indexes for vector similarity (grounding/retrieval), and GIN full-text indexes for search. See [`security.md`](security.md) for the tenancy model.

## Multi-tenancy in one paragraph

Every tenant row carries an `org_id`. The application connects as a non-owner role (`aegis`, a member of the `aegis_app` group) for which RLS policies are enforced. On each request the session sets `app.current_org_id` / `app.current_user_id` (via `set_config(..., true)` so it is transaction-local), and RLS policies compare rows against those settings. The **identity layer** (signup, session lookup) and **migrations/seeding** use the owner role, which bypasses RLS by design — because they must operate before or across tenants.

## The audit lifecycle

`AuditRunner` moves an audit through explicit stages, emitting SSE progress after each:

```
setup ─▶ test_generation ─▶ inference ─▶ evaluation ─▶ evidence
   ─▶ policy_mapping ─▶ risk_scoring ─▶ report_generation
```

1. **setup** — resolve the target system, its version, and the controls/tests in scope.
2. **test_generation** — generate test cases for each in-scope control (deterministic, seeded).
3. **inference** — run the system under test over the cases (parallelised with a thread pool; agent systems also record traces).
4. **evaluation** — run the relevant engines over each result to decide pass/fail with a confidence.
5. **evidence** — capture inputs, outputs, and engine reasoning as hash-chained evidence.
6. **policy_mapping** — attach failing results to the controls and framework references they touch.
7. **risk_scoring** — aggregate into risk-scored findings.
8. **report_generation** — assemble the audit report.

## Remediation & re-test loop

A finding carries recommended remediations; some create a **regression test**. Applying a remediation (e.g. tightening a guardrail on a simulated system) triggers `regression_service.run_regression`, which re-runs the failing cases and records a **measured before/after delta** — the fail→pass (or not) is evidence, not an assertion. Monitoring samples live traffic and evaluates it continuously, raising alerts on drift or threshold breaches that can open new findings, closing the loop back to the top of the lifecycle.

## Request path (end to end)

```
Browser → /bff/api/v1/... (Next route handler)
        → API_INTERNAL_URL → FastAPI middleware (req-id, headers, size)
        → auth (cookie / bearer / API key) → RBAC (require(*perms))
        → RLS-scoped session (org/user set) → service → engines / DB
        → response envelope (+ Set-Cookie / SSE relayed back through the BFF)
```

## AI Scientist Evolution Lab

The lab (`lab` schema, `services/lab`, `workflows/`, `engines/lab`) extends this architecture with durable
workflows (Temporal or the inline engine), a sandboxed execution fabric, a governed agent runtime and a
verification pipeline. See [`docs/lab/architecture.md`](lab/architecture.md) and the [lab documentation map](lab/README.md).
