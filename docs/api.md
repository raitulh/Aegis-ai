# API, SDK & MCP

Aegis exposes its capabilities four ways: a **REST API**, a **Python SDK**, an **MCP server**, and the web console (which itself uses the REST API through a BFF proxy). This document covers the REST conventions and points to the SDK and MCP.

## Base URL & versioning

All application endpoints are under `/api/v1`. Interactive documentation is generated from the live schema:

- Swagger UI — `GET /docs`
- OpenAPI JSON — `GET /openapi.json`
- Health/readiness (unversioned) — `GET /health`, `GET /ready`

From the browser, the web app never calls the API origin directly — it goes through the Next.js BFF at `/bff/api/v1/...`, which forwards cookies and SSE.

## Authentication

Three credential types are accepted, checked in order:

1. **Session cookie** — set by `POST /api/v1/auth/signup` / `login` / `guest`. Used by the web console.
2. **Session bearer token** — the same session as a `Authorization: Bearer <token>` header.
3. **API key** — `Authorization: Bearer aeg_live_…` (or `aeg_test_…`). Create keys under **API & SDK → API keys**; each key has a role (never above its creator's) and scopes (`read`, `write`, `run`, `ingest`, `runtime`). Only a prefix is stored in clear; the rest is peppered and hashed.

All authenticated requests are scoped to the caller's organisation by Row-Level Security — you only ever see your own workspace's data.

### Auth endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/signup` | Create an organisation + owner, returns a session |
| `POST` | `/api/v1/auth/login` | Authenticate, returns a session |
| `POST` | `/api/v1/auth/guest` | Short-lived guest sandbox session |
| `POST` | `/api/v1/auth/supabase` | Exchange a Supabase JWT for a session (optional) |
| `GET` | `/api/v1/auth/session` | Current principal |
| `POST` | `/api/v1/auth/logout` | End the session |
| `POST` | `/api/v1/auth/invitations/accept` | Accept a workspace invitation (signed in as the invited email, or create an account) |

## Authorization

Endpoints declare the permissions they need via `require(*perms)`, checked against the principal's role (and the API key's scopes). A missing permission returns `403` with the standard error envelope. `GET /api/v1/roles` returns the role → permission matrix. Cookie-authenticated writes must carry a trusted `Origin`.

## Errors

Every error is a consistent envelope — the client never sees a stack trace or raw framework error:

```json
{
  "error": {
    "code": "not_found",
    "message": "System not found",
    "request_id": "01J…",
    "details": {}
  }
}
```

`request_id` also appears in server logs and response headers, so a user-visible error can be correlated with backend logs.

## Idempotency

Mutating endpoints that create work (audits, re-runs, change triggers, red-team runs and others) accept an `Idempotency-Key` header. The first request's response is stored for `IDEMPOTENCY_TTL_HOURS`; a retry with the same key and the same body returns the stored response (with `Idempotency-Replayed: true`) instead of acting twice; reusing the key with a different body is rejected (`422 idempotency_mismatch`), and a retry while the first request is still running gets `409 idempotency_in_progress`. The SDK sets a key automatically and reuses it across its retries.

## Pagination and filtering

List endpoints return `{"items": [...], "meta": {"page", "page_size", "total", "total_pages"}}` and accept `page` and `page_size` (≤ 200). Findings accept `severity`, `status` (comma-separated), `category`, `system_id`, `risk_level`, `audit_id` (observed by that audit), `source`, `tag`, `assignee_id`, `q`, `open_only` and `sort` (`risk | newest | oldest | sla | number`).

## Rate limits and plan limits

Per-IP tiers — public, authenticated, expensive operations, guest-sandbox creation — return `429` with `Retry-After`. Plan limits return `403` with code `plan_limit_exceeded` (or `feature_not_in_plan`) and details `{metric, used, limit, plan, next_plan}`; see [billing.md](billing.md).

## Live audit progress (SSE)

```
GET /api/v1/audits/{audit_id}/stream          text/event-stream; resumable
GET /api/v1/audits/{audit_id}/events?after=N  the same events as JSON (polling fallback)
```

Each event has a sequence id. Reconnecting with `Last-Event-ID` (sent automatically by `EventSource`) or `?after=` resumes exactly after the last seen event. The stream sends heartbeats every `SSE_HEARTBEAT_SECONDS`, ends with a `done` event when the audit reaches a terminal state, and closes after 30 minutes (clients resume). Events are fanned out through Redis, so any API replica can serve any stream.

## Surface at a glance

163 endpoints under `/api/v1` (OpenAPI at `/docs` is authoritative):

| Group | Paths | What |
| --- | --- | --- |
| Auth | `/auth/*` | signup, login, guest sandbox, Supabase exchange, session, logout, invitation acceptance |
| Systems & providers | `/systems`, `/providers` | register systems, provider connections and tests, versions, change impact, baseline, runtime mode |
| Audits | `/audits` | create (idempotent), stream, events, results, matrix, claims, findings observed, compare, cancel, re-run, report, regression |
| Findings | `/findings` | filters, bulk triage, transitions, comments, events, occurrences, explanation, remediation, re-test, CSV/JSON export |
| Evidence | `/evidence`, `/audits/{id}/evidence/*` | records, graph, reveal, verification, integrity, signed export, package verification, signing key |
| Runtime Guard | `/runtime/*` | ingest, synchronous check, events, overview, traces, approvals |
| Policy Studio | `/runtime-policies/*` | templates, validate, test, simulate, versions, diff, publish, rollback, enable/disable, clone, assignments |
| Compliance policies | `/policies`, `/controls`, `/frameworks` | upload, compile, versions, controls with provenance, framework mappings |
| Continuous assurance | `/assurance/*` | schedules, change triggers |
| Red team & operations | `/redteam`, `/agents`, `/traces`, `/monitoring`, `/monitors`, `/alerts` | campaigns, agent traces, monitoring |
| Workspace | `/overview`, `/search`, `/team`, `/roles`, `/api-keys`, `/integrations`, `/notifications`, `/audit-log`, `/jobs`, `/organization`, `/graph` | |
| Billing & usage | `/plans`, `/usage`, `/billing/*` | plan catalogue (public), usage and quotas, checkout/portal |
| Webhooks | `/webhooks` | endpoints, event catalogue, deliveries, test |
| Public | `/public/trust`, `/public/contact` | trust-center facts, contact requests |
| Demo | `/demo/*` | public demo data |

## Python SDK

`packages/sdk/python` (`aegis-ai`): typed errors (`PermissionDeniedError`, `PlanLimitError`, `NotFoundError`, `ConflictError`, `ValidationError`, `RateLimitError`), automatic retries with idempotency keys, sync and async clients.

```python
from aegis_ai import Aegis

aegis = Aegis(api_key, base_url="http://localhost:8000")
audit = aegis.audit(system_id, ["fairness", "privacy"])  # create + wait
findings = aegis.findings.list(system_id=system_id, open_only=True)
report = aegis.evidence.verify(audit["id"])
package = aegis.evidence.export(audit["id"])  # bytes of the signed zip
decision = aegis.runtime.check(system_id=system_id, event_type="tool.call", tool="delete_records")
aegis.policy.simulate(source_yaml=open("policy.yaml").read(), days=7)
aegis.assurance.trigger(system_id=system_id, event_type="deployment", ref=git_sha)
```

See [packages/sdk/python/README.md](../packages/sdk/python/README.md).

## MCP server

`packages/mcp` (`aegis-mcp`) exposes Aegis to MCP clients: posture, systems, audits, findings, evidence and its verification, runtime checks and overview, change reporting and policy simulation. It authenticates with an API key and has no privileges of its own. See [packages/mcp/README.md](../packages/mcp/README.md).

## Generating TypeScript types

```bash
pnpm gen:types      # scripts/export_openapi.py → @aegis/types generate
```

The API schemas (`aegis_api.schemas`) are the contract; regenerate types after changing the surface.
