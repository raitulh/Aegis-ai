# Production readiness — evidence-based assessment (October 2026)

This is a factual record of what was verified for this release, how, and what remains. "Verified" means a command was run or a test exercises the behaviour; anything not verified is stated as such.

## Quality gates (run on this release)

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `pnpm lint` (ruff check, ruff format --check, eslint) | pass — 0 errors, 0 warnings |
| Types | `pnpm typecheck` (mypy on 184 source files, tsc) | pass |
| Python tests | `pnpm test:py` against PostgreSQL 16 + pgvector | **125 passed** |
| Web unit tests | `pnpm test:web` (vitest) | **21 passed** (7 files) |
| Coverage | `uv run pytest --cov` | 78 % of 13,068 statements |
| Production build | `pnpm build` | pass — 37 routes (website static, dashboard dynamic) |
| End-to-end | `pnpm --filter @aegis/web test:e2e` (real API + database + Chromium) | **4 passed**, including a smoke test that opens all 25 dashboard views in the sandbox with zero page errors |
| Migrations | `alembic upgrade head` → `alembic check` → `downgrade -1` → `upgrade head`; and `upgrade head` → `downgrade base` → `upgrade head` on an empty database | pass, no model drift |
| Dependency audit | `pip-audit` (locked production deps), `pnpm audit --prod` | no known vulnerabilities at time of run |
| Compose | `docker compose config` | valid |
| Docker images | `docker build` | **not verified here** (no Docker daemon in the build environment); the CI `docker` job builds both images |

Baseline before this work: 46 Python tests, 11 web tests, 19 eslint warnings.

## P0 blockers

All P0 items from the [repository audit](audit-2026-10.md) are fixed and covered by tests: live progress, cancellation races, duplicate delivery, finding-number races, finding attribution per audit, per-system deduplication, SSRF (rebinding and missing ranges), private-network provider URLs, owner demotion, immutability bypass, identity-pool exhaustion by streams, `X-Forwarded-For` spoofing, missing login throttling.

Additional defects found and fixed during end-to-end verification of this release:

- The DEMO sandbox could not be created (finding numbers were allocated in a separate transaction that could not see the uncommitted sandbox workspace) — fixed, regression test added.
- React hydration mismatches on permission-gated dashboard pages — fixed by seeding the server-fetched session; the Playwright smoke test fails on any page error.
- `.env.example` could not be loaded as-is (empty boolean, inline comments parsed as values) — fixed; empty values now fall back to defaults; regression test added.
- Evidence-export quota was not enforced — fixed, test added.

**No known open P0.**

## Tenant isolation

PostgreSQL RLS on every tenant table (including all tables added in migrations 0003–0004); the app role cannot bypass it. `test_new_tables_are_tenant_isolated` and `tests/integration/test_security.py` assert cross-workspace invisibility with two real workspaces. The workspace is always derived from the authenticated principal.

## Billing and entitlements

Plan catalogue is configuration with no invented prices (asserted by test). Quotas are enforced from the append-only usage ledger at each point of use (systems, seats, audits, red team, runtime ingestion, evidence exports) and tested; the synchronous runtime check is never quota-blocked. The Stripe adapter's signature verification and idempotent event handling are tested with synthetic signed payloads; **it has not been run against a live Stripe account** — validate in Stripe test mode before charging customers.

## Feature status

| Feature | Status |
| --- | --- |
| Audits, findings lifecycle, evidence chains, signed export + offline verifier | implemented, integration + E2E tested |
| Runtime Guard (observe / audit / enforce, approvals, redaction, runtime evidence chain) | implemented, integration tested; sandbox demonstrates it with real engine decisions |
| Policy Studio (DSL, versions, diff, test, simulation on recorded events, publish, rollback, assignments) | implemented, integration tested |
| Continuous assurance (schedules, CI/CD triggers, baselines, regression detection) | implemented, integration tested |
| Usage ledger, plans, quotas, usage alerts | implemented, tested |
| Webhooks (HMAC-signed, retried, auto-disabled, SSRF-guarded) | implemented, tested against a local receiver |
| Assurance graph (2D map, list, optional WebGL view) | implemented; built from records; layout unit-tested |
| Job ledger, maintenance scheduler, operator re-drive | implemented, tested |
| Metrics (`/metrics`), structured logs, W3C `traceparent` correlation | implemented; no distributed tracing exporter (OpenTelemetry) yet |
| SSO (SAML/OIDC), SCIM | **not implemented** — shown as roadmap everywhere |
| AI assistant, marketplace | not implemented (feature flags off) |
| Slack / GitHub native integrations | not implemented — webhooks and the trigger API are the integration path |

## Known limitations and remaining risks (P1/P2)

1. **CSP allows `'unsafe-inline'` scripts** (Next.js inline bootstrap). Moving to nonce-based CSP needs dynamic rendering of every page; recommended for high-assurance deployments.
2. **Rotation tooling** for `SECRETS_ENCRYPTION_KEY` and `API_KEY_PEPPER` does not exist; rotation is a manual re-encrypt / re-issue.
3. **Runtime Guard depends on agents reporting truthfully**; it cannot see unreported actions. Detectors are pattern-based and can miss novel encodings of personal data or secrets.
4. **Evaluation methodology limits** are documented in [evaluation.md](evaluation.md): automated tests sample behaviour and do not prove safety; fairness results are controlled counterfactual differences, not proof of real-world discrimination.
5. **Single-region, single-database design.** Horizontal scale is for stateless tiers; PostgreSQL is the scaling and availability boundary.
6. **Without Redis, live progress is polled.** Streams then read new events from the database once per second (any replica can serve them); with Redis they are push-based. The inline job backend also ties audit execution to the API process.
7. **No load or soak testing** has been performed for this release; capacity figures are not claimed.
8. **Email delivery** depends on operator SMTP; without it, invitation links are shown once to the inviter.
9. **Docker images were not built in this environment** (see gates); CI builds them on every push.
10. **External attestations** (SOC 2, ISO/IEC 27001, ISO/IEC 42001 certification) have not been performed; the trust center says so.

## Go-live checklist

Follow [deployment.md](deployment.md#production-checklist): secrets in a manager (including `EVIDENCE_SIGNING_KEY` and `METRICS_TOKEN`), HTTPS origins, `TRUSTED_PROXY_HOPS`, private network targets blocked, Celery worker + scheduler, SMTP, PITR backups with tested restores, `/ready` and metrics alerts, and a dry run of the [operations runbooks](operations.md).
