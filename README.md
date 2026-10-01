# Aegis AI

**Continuous AI assurance: test AI systems and agents before release, guard their actions in production, and keep verifiable evidence of every result.**

Aegis is a self-hostable platform for teams that need to know — and show — how their models and agents behave. It runs evaluation audits (fairness, grounding, safety, privacy, prompt-injection resistance, agent actions), checks agent actions at runtime against versioned policies, turns problems into tracked findings, and records everything as hash-chained evidence that can be exported as a signed package and verified offline.

> **Deterministic where possible, model-assisted where useful, evidence-backed everywhere.**
> A model may assist a judgment but is never the sole basis for a finding.

Aegis produces *assessments* and *readiness* signals. It does not certify compliance with any law or standard, and its framework mappings are reference data, not legal advice.

---

## What it does

| Area | What you get |
| --- | --- |
| **Audits** | Deterministic, seeded evaluation runs across fairness (counterfactual treatment tests with significance testing), grounding (claim verification against sources), safety, privacy (PII / secret detection), injection and jailbreak resistance (from a corpus *you* import) and agent-action authorization. Live progress over a resumable event stream. |
| **Runtime Guard** | Agents report events (`aegis.runtime.v1`) and ask for decisions before acting. Per system: **observe** (record), **audit** (record + findings) or **enforce** (block, or hold for human approval). Payloads are redacted before storage. |
| **Policy Studio** | Runtime policies in a small YAML DSL: validate, version, diff, test against a sample event, **simulate on your recorded events**, publish, roll back and assign to the workspace, an environment or a system. A library of starter templates is included. |
| **Policy compiler** | Written policies (PDF, DOCX, text) compiled into testable controls with provenance back to the source section and page. Controls map to NIST AI RMF, the NIST Generative AI Profile, OWASP Top 10 for LLM Applications and ISO/IEC 42001 as reference data. |
| **Findings** | Deduplicated per system, observed per audit, risk-scored with explained factors. Lifecycle with SLAs, comments, bulk triage, risk acceptance with justification and expiry, verified re-tests, and regression re-opening. |
| **Evidence** | Append-only (database triggers reject edits), SHA-256 hash-chained per audit and per runtime system, Ed25519-signed export packages with a standalone `verify.py`. |
| **Continuous assurance** | Schedules and change triggers from CI/CD (deployment, prompt, model, tool, policy changes) with risk-based test selection, known-good baselines and regression detection. |
| **Platform** | Multi-tenant with PostgreSQL Row-Level Security, nine roles, scoped API keys, signed webhooks, usage ledger and plan entitlements, audit log, Prometheus metrics, Python SDK and an MCP server. |

---

## Architecture

```
Browser ──▶ Next.js 16 web (dashboard + site) ──▶ same-origin BFF /bff/api/v1 ──┐
                                                                                 ▼
SDK · MCP · CI ───────────────────────────────────────────────────────▶ FastAPI /api/v1
                                                                   auth · RBAC · RLS session
                                                                   services · orchestrator
                                     ┌──────────────────────────────┬───────────┴──────────┐
                                     ▼                              ▼                      ▼
                               pure engines/               job ledger → Celery         evidence
                          (no DB, no web framework)        (or inline pool)        hash chains +
                                                           + maintenance ticks     append-only triggers
                                     └──────────────────────────────┴──────────────────────┘
                                                  PostgreSQL 16 + pgvector (RLS)  ·  Redis
```

Details: [docs/architecture.md](docs/architecture.md).

---

## Quick start

### Docker Compose

```bash
cp .env.example .env          # development defaults work as-is; see docs/deployment.md for production
docker compose up --build     # db, redis, migrate (one-shot), api, worker, scheduler, web
```

- Console: <http://localhost:3000> — create a workspace, or click **Try the sandbox** for a temporary, clearly marked DEMO workspace with a simulated hiring agent.
- API reference (OpenAPI): <http://localhost:8000/docs>

### Local development

Prerequisites: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js ≥ 20.19 with pnpm 10, PostgreSQL 16 with pgvector, Redis (optional — without it jobs run in-process).

```bash
uv sync && pnpm install
cp .env.example .env
pnpm db:migrate               # alembic upgrade head (create the app role first — see docs/deployment.md)
pnpm db:seed                  # optional: demo reference workspace
pnpm dev                      # web :3000, api :8000, worker
```

### Testing a local model with Ollama

```bash
ollama pull qwen3:1.7b
python scripts/register_ollama_qwen.py   # registers an Ollama provider and system
python scripts/test_audit_qwen.py        # runs an audit and prints the posture
```

Or add the provider under **Integrations** and launch an audit from **Audits → New audit**.

---

## Python SDK

```bash
pip install ./packages/sdk/python        # published name: aegis-ai
```

```python
import os
from aegis_ai import Aegis

aegis = Aegis(os.environ["AEGIS_API_KEY"], base_url="http://localhost:8000")

audit = aegis.audit(system_id, ["fairness", "privacy", "safety"])  # waits for the result
print(audit["status"], audit["findings_count"])
print(aegis.evidence.verify(audit["id"])["status"])  # VERIFIED / TAMPERED / ...

# Runtime Guard: ask before acting
with aegis.runtime.trace(system_id, agent="support-agent") as trace:
    decision = trace.check("tool.call", tool="send_email", payload={"destination": "external"})
    if decision.requires_approval:
        approved = aegis.runtime.wait_for_approval(decision.approval_id)
    elif decision.allowed:
        send_email(...)

# CI/CD: report a change; Aegis selects and runs the relevant tests
aegis.assurance.trigger(system_id=system_id, event_type="prompt_change", ref=git_sha)
```

Create API keys under **API & SDK → API keys**. See [packages/sdk/python/README.md](packages/sdk/python/README.md).

## MCP server

```bash
pip install ./packages/mcp
AEGIS_API_KEY=aeg_live_... AEGIS_BASE_URL=http://localhost:8000 aegis-mcp
```

```json
{ "mcpServers": { "aegis": { "command": "aegis-mcp", "env": { "AEGIS_API_KEY": "aeg_live_...", "AEGIS_BASE_URL": "http://localhost:8000" } } } }
```

Tools include `aegis_runtime_check`, `aegis_get_risk_posture`, `aegis_get_findings`, `aegis_verify_evidence`, `aegis_report_change` and `aegis_simulate_policy`. The server holds no privileges of its own; every call uses the key's role and scopes. See [packages/mcp/README.md](packages/mcp/README.md).

---

## Repository layout

```
apps/api            FastAPI application (routers, services, jobs, security, observability)
apps/web            Next.js 16 / React 19 console and website (BFF proxy, TanStack Query, Tailwind 4)
engines/            Pure evaluators and libraries: fairness, hallucination, safety, privacy, security,
                    agent, redteam, policy, compliance, runtime (schema + policy DSL), evidence (package
                    + verifier), risk, generation, monitoring, providers
packages/sdk/python Python SDK (aegis-ai)
packages/mcp        MCP server (aegis-mcp)
database/           Alembic environment and migrations
docker/             Dockerfiles and init scripts
tests/              unit · integration (real Postgres, RLS) · evaluation (engine correctness)
docs/               architecture, security, deployment, operations, feature guides, readiness
```

## Documentation

- [Architecture](docs/architecture.md) · [Security](docs/security.md) · [Deployment](docs/deployment.md) · [Operations & recovery](docs/operations.md)
- [Runtime Guard](docs/runtime-guard.md) · [Policy Studio](docs/policy-studio.md) · [Policy compiler](docs/policy-engine.md) · [Evidence verification](docs/evidence.md)
- [Plans, usage & billing](docs/billing.md) · [API conventions](docs/api.md) · [Evaluation methodology](docs/evaluation.md) · [Testing](docs/testing.md)
- [Production readiness](docs/production-readiness.md) · [Repository audit (Oct 2026)](docs/audit-2026-10.md)

## Quality gates

```bash
pnpm lint          # ruff check + ruff format --check + eslint
pnpm typecheck     # mypy + tsc
pnpm test          # pytest (needs PostgreSQL) + vitest
pnpm build         # Next.js production build
pnpm --filter @aegis/web test:e2e   # Playwright against a real API + database
```

## Ground rules for contributors

Read [AGENTS.md](AGENTS.md). In short: engines stay pure; tenant data goes through the RLS-scoped session; evidence is immutable; the red-team engine ships no attack payloads; never claim legal compliance. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
