# Security model

Aegis holds other organisations' AI system configurations, provider credentials, runtime telemetry and audit evidence. Its posture rests on: **database-enforced tenant isolation**, **least-privilege roles and scoped keys**, **immutable, verifiable evidence**, **encrypted secrets**, **guarded outbound requests**, and a **red-team engine that ships no attacks**. This document describes what is implemented; [production-readiness.md](production-readiness.md) lists what is not.

## 1. Tenant isolation (PostgreSQL Row-Level Security)

- Every tenant table carries `organization_id` and an RLS policy comparing it to the transaction-local setting `app.current_org_id`. All tables added in this release (runtime events/approvals/policies, usage, subscriptions, schedules, triggers, exports, comments, finding occurrences, job ledger, idempotency keys) are covered.
- The API and workers connect as the application role (`aegis`, member of `aegis_app`), to which RLS applies. Each request's session runs `set_config('app.current_org_id', …, true)` — transaction-local, so it cannot leak across pooled connections.
- The owner role is used only by the identity layer (resolving a session or API key before a tenant is known), onboarding, migrations, seeding and cross-tenant maintenance (job ledger, retention, sandbox purge). Identity resolution uses a short-lived session so long-lived streams never pin owner-pool connections.
- The workspace is always derived from the authenticated membership or API key, never from client input. Integration tests create two workspaces and assert that every tenant table is invisible across them.

## 2. Authentication

- Session cookie (`httpOnly`, `SameSite=Lax`, `Secure` in production), session bearer token, or API key (`Authorization: Bearer aeg_live_…`). Guest DEMO sandboxes get short-lived sessions on isolated, expiring workspaces.
- Passwords: strength rules on signup and invitation acceptance; per-account login throttling (`LOGIN_MAX_FAILURES` per `LOGIN_FAILURE_WINDOW_SECONDS`) in addition to per-IP rate limits. Successful sign-ins are written to the workspace audit log; failed attempts are throttled and written to the security log with a pseudonymised account id.
- Invitations: single-use tokens with expiry; an existing account must be signed in as the invited email to accept.
- Optional Supabase Auth integration (JWT verification) alongside local auth.

## 3. Authorization (roles, permissions, scopes)

Nine roles, checked on every request with `require(<permission>)`; the console hides controls a role cannot use, but the API is the boundary.

| Role | Purpose |
| --- | --- |
| viewer | read-only |
| developer | integrate agents: traces, runtime events and decisions, run audits |
| analyst | run audits and red-team campaigns, triage findings, author policies |
| ai_engineer | analyst + runtime integration and continuous-assurance schedules |
| security_engineer | accept risk, approve remediations and runtime actions, publish runtime policies |
| auditor | analyst + risk acceptance, evidence export and the audit log |
| admin | members, API keys, providers, integrations, webhooks |
| owner | everything, including billing; owner memberships can only be changed by an owner and the last owner cannot be demoted |
| service_account | machine identity for API keys only (not assignable to people) |

`GET /roles` returns the live permission matrix. API keys carry a role (never above the creator's) and **scopes** (`read`, `write`, `run`, `ingest`, `runtime`) that narrow it further; keys are stored as peppered HMAC hashes, shown once, optionally expiring, and revocable.

## 4. Web application

- All browser traffic goes to the same-origin BFF (`/bff/api/v1/*`), which forwards only `/api/v1` paths, re-encodes path segments, forwards an allow-list of headers and only the session cookie, caps request bodies, and checks that unsafe methods come from the site's own origin.
- The API independently rejects cookie-authenticated unsafe requests without a trusted `Origin` (or `Referer`) — CSRF defence in depth on top of SameSite cookies and strict CORS.
- Response headers: Content-Security-Policy (`connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`), `X-Frame-Options: DENY`, `nosniff`, Referrer-Policy, Permissions-Policy, COOP; HSTS and `upgrade-insecure-requests` for HTTPS deployments. `script-src` allows `'unsafe-inline'` because Next.js bootstraps with inline scripts; nonce-based CSP (dynamic rendering + middleware) is the documented next hardening step.
- Post-login redirects accept same-site paths only; external links from data (notifications, search) are never followed.

## 5. Outbound requests (SSRF)

Provider base URLs, HTTP endpoints and webhook targets are validated when saved **and at connect time**: a custom transport resolves the host, rejects any non-global address (private, loopback, link-local, CGNAT, NAT64/6to4-embedded private, multicast, reserved) and pins the connection to the vetted IP, defeating DNS rebinding. Redirects are not followed and proxy environment variables are ignored. Private targets are allowed only when `ALLOW_PRIVATE_NETWORK_TARGETS` is true (default: development only). Production webhooks must use HTTPS. Operator-configured endpoints from the environment (e.g. `OLLAMA_BASE_URL`) are trusted.

## 6. Secrets at rest

- Provider credentials and webhook signing secrets: Fernet (AES-128-CBC + HMAC-SHA256) with `SECRETS_ENCRYPTION_KEY`; never returned by the API.
- API keys: HMAC with `API_KEY_PEPPER`; only a display prefix is stored in clear.
- Evidence signing: Ed25519 key from `EVIDENCE_SIGNING_KEY`.
- In production the API refuses to start without these keys, with wildcard CORS, without an https `WEB_BASE_URL`, without `METRICS_TOKEN`, or with Stripe selected but not configured. In development, derived keys are used and flagged at startup and in the UI (development signing key badge).

## 7. Evidence integrity

Append-only evidence (database triggers reject `UPDATE`/`DELETE`; the purge path for retention requires a role outside `aegis_app`), per-audit and per-runtime-system hash chains, and signed export packages with a standalone verifier. See [evidence.md](evidence.md). The audit log and the usage ledger are append-only in the same way.

## 8. Runtime data minimisation

Runtime payloads are redacted at ingestion (secrets, credentials and personal data masked) before storage; policies evaluate derived signals. Sensitive evidence is masked by default; revealing it requires `evidence:reveal` and is audit-logged. Runtime events are purged after the plan's retention period.

## 9. The red-team engine ships no attacks

Red-team campaigns execute a corpus the customer imports; the engine contains no built-in payloads, jailbreak strings or mutation strategies, and an empty corpus produces "no probes executed". Success is detected deterministically (planted markers, disallowed tool calls). Do not add built-in attacks.

## 10. Abuse and availability controls

Per-IP rate limits by tier (public, authenticated, expensive operations, guest sandbox creation), request and upload size limits, idempotency keys on mutating endpoints, bounded batch sizes (runtime ingestion ≤ 500 events), simulation caps (20,000 events), regex length limits in policies, and quota checks that never block the synchronous runtime check.

## 11. Auditability

Security-relevant actions — sign-in, membership and role changes, API keys, providers, webhooks, policy publish/rollback/assignment/simulation, runtime mode changes and approval decisions, risk acceptance, evidence reveal and export, settings and billing changes — are written to the append-only audit log with actor, request id and before/after where relevant.

## Reporting a vulnerability

Report privately to the maintainers (contact form, "security review") with reproduction steps; do not open a public issue.
