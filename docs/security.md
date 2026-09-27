# Security model

Aegis handles other organisations' AI systems, provider credentials, and audit evidence. Its security posture rests on four pillars: **hard tenant isolation**, **immutable evidence**, **encrypted secrets**, and a **red-team engine that ships no attacks**.

## 1. Multi-tenancy via PostgreSQL Row-Level Security

Tenant isolation is enforced by the database, not just the application.

- Every tenant table carries an `org_id` and has an RLS policy comparing it to a transaction-local setting `app.current_org_id`.
- The API and worker connect as the **application role** (`aegis`, a member of the `aegis_app` group role). RLS policies **apply** to this role — it can only see its own org's rows.
- On each request, the RLS-scoped session runs `select set_config('app.current_org_id', :org, true), set_config('app.current_user_id', :usr, true)` (the `true` makes the setting transaction-local, so it can't leak across pooled connections).
- The **owner role** (`postgres`) is used only for **migrations, seeding, and the identity layer** (signup, session lookup) — operations that must run before a tenant context exists or across tenants. It bypasses RLS by design. This is why `deps._raw_session()` uses the admin engine: creating the first organisation for a new user happens before any `org_id` exists.

The upshot: a bug in application-layer filtering cannot cross tenants, because the database itself refuses to return another org's rows to the app role. `tests/integration/test_security.py` asserts this isolation directly.

## 2. Immutable, hash-chained evidence

Evidence must be trustworthy after the fact.

- Each evidence record is linked into a per-scope **hash chain** (`ChainState`): every record commits to the hash of the previous one, so any tampering breaks the chain.
- Database **triggers** (`aegis.protect_evidence`) make the `evidence` table and the audit log **append-only** — `UPDATE` and `DELETE` are rejected at the database. Application code *cannot* rewrite history even with a bug or a compromised service account scoped to the app role.

## 3. Secrets at rest

- **Provider credentials** (API keys for Ollama/Gemini/OpenAI/Anthropic) are encrypted with a **Fernet** key (`SECRETS_ENCRYPTION_KEY`) before being stored.
- **API keys** issued by Aegis are hashed with a server-side **pepper** (`API_KEY_PEPPER`); only a prefix is stored in the clear for display.
- Both secrets are **required in production** — the API refuses to start (`RuntimeError`) if `ENVIRONMENT=production` and either is missing. In development, throwaway values are generated and flagged loudly at startup so you never accidentally rely on them.

Generate real values:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # SECRETS_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"                                 # API_KEY_PEPPER
```

## 4. The red-team engine ships no attacks

This is a deliberate safety constraint.

- The red-team engine runs against an **imported corpus only** (`engines/redteam/corpus.py` defaults to an empty corpus; `engine.py` uses a null mutation provider by default).
- Aegis contains **no built-in attack payloads, jailbreak strings, or mutation/transform strategies**. A user brings their own adversarial dataset; Aegis executes it against the target and scores *resistance*.
- The console reflects this: a run with no imported corpus shows "No probes executed — import an adversarial dataset", not a synthesised attack.

Do not add built-in attacks. If you extend the engine, extend how imported probes are *evaluated*, not a library of exploits.

## Authentication & authorization

- **Authentication** accepts a session cookie, a session bearer token, or an API key (`Authorization: Bearer aeg_live_…`). Guest sandbox sessions are supported for the demo with a short TTL.
- **Authorization** is role-based. Endpoints declare required permissions with `require(*perms)`; the principal's role is checked per request.
- All authenticated data access is tenant-scoped through the RLS session.

## HTTP hardening

Pure-ASGI middleware adds request IDs, secure response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`), and request/upload **body-size limits**. Rate limiting is configurable per tier (public / authenticated / expensive). Outbound calls to private-network targets are blocked unless explicitly allowed (`ALLOW_PRIVATE_NETWORK_TARGETS`), mitigating SSRF from user-supplied endpoints.

## Error handling

Errors return a consistent envelope — `{"error": {"code", "message", "request_id", "details"}}` — that never leaks internals. The `request_id` correlates a client-visible error with server logs.

## Reporting a vulnerability

Treat security findings in Aegis itself as you would any responsible disclosure: report privately to the maintainers with reproduction steps before any public discussion.
