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
3. **API key** — `Authorization: Bearer aeg_live_…` (or `aeg_test_…`). Create keys in the workspace settings; only a prefix is stored in clear, the rest is peppered and hashed.

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

## Authorization

Endpoints declare the permissions they need via `require(*perms)`, checked against the principal's role. A missing permission returns `403` with the standard error envelope.

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

## Rate limits

Three tiers are enforced (configurable): **public** (unauthenticated), **authenticated**, and **expensive** (audit/monitoring ingestion). Exceeding a limit returns `429`.

## Live audit progress (SSE)

Audits run asynchronously. Subscribe to live stage/progress events with Server-Sent Events:

```
GET /api/v1/audits/{audit_id}/stream        # text/event-stream
GET /api/v1/audits/{audit_id}/events        # the same events as a JSON list (polling)
```

The web console uses `EventSource` against the BFF, which streams the API's SSE straight through. Findings expose an equivalent event history at `/api/v1/findings/{id}/events`.

## Surface at a glance

96 endpoints, grouped by router:

| Group | Prefix | What |
| --- | --- | --- |
| Auth | `/api/v1/auth` | sessions, guests |
| Systems | `/api/v1/systems`, `/api/v1/providers` | register AI systems, configure providers |
| Audits | `/api/v1/audits` | run audits, stream progress, results |
| Policies | `/api/v1/policies` | upload, compile, controls, frameworks |
| Findings | `/api/v1/findings` | findings, remediation, regression re-tests |
| Evidence | `/api/v1/evidence` | hash-chained evidence, reports |
| Operations | `/api/v1/agents`, `/api/v1/redteam`, `/api/v1/monitoring` | traces, red team, monitors, alerts |
| Workspace | `/api/v1/...` | overview, search, team, API keys, integrations, notifications, webhooks |
| Demo | `/api/v1/demo` | public, unauthenticated demo data |

Consult `/docs` for the authoritative, always-current request/response schemas.

## Python SDK

`packages/sdk/python` (`aegis-ai`) wraps the REST API with typed models and handles auth, pagination, and SSE. Sketch:

```python
from aegis_ai import AegisClient

client = AegisClient(base_url="http://localhost:8000", api_key="aeg_live_…")

system = client.systems.register(name="Support-RAG", system_type="rag", risk_tier="high")
audit = client.audits.run(system_id=system.id)
for event in client.audits.stream(audit.id):  # live SSE
    print(event.stage, event.progress)

findings = client.findings.list(system_id=system.id)
```

See the package README for the full surface. The SDK targets Python ≥ 3.10 (it keeps `typing.Generic` rather than PEP 695 syntax for that reason).

## MCP server

`packages/mcp` (`aegis-mcp`) exposes Aegis to Model Context Protocol clients, so an agent can register systems, launch audits, read findings, and pull evidence as MCP tools — putting continuous assurance directly in an agent's reach. It authenticates with an Aegis API key and speaks to the same REST API.

## Generating TypeScript types

The web app's API types are generated from the live OpenAPI schema:

```bash
pnpm gen:types      # export_openapi.py → @aegis/types generate
```

Keep the API schemas (`aegis_api.schemas`) as the single source of truth; regenerate types after changing the surface.
