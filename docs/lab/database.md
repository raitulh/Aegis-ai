# Database

PostgreSQL 16 with pgvector. The lab adds the `lab` schema (62 tables) next to the assurance tables in
`public` (140 tables in total). Migrations live in `database/migrations/versions/`; every revision has a
working `downgrade()`.

| Revision | Content |
| --- | --- |
| `0001` | Assurance platform schema, RLS helpers (`aegis.current_org_id()`, `aegis.is_org_member()`). |
| `0002` | Security/search hardening, the original evidence immutability trigger. |
| `0003` | Tenancy extensions (workspaces, projects, teams, service accounts, custom roles, quotas, refresh tokens, idempotency keys, SSO, billing) and the full `lab` schema, RLS policies, append-only triggers, FTS/vector indexes, `lab.tool_usage` view. |
| `0004` | Evidence hardening: every recorded evidence field is frozen; purge is one-way and blocked under legal hold. |

```bash
bash scripts/migrate.sh                 # alembic upgrade head (owner connection: DATABASE_ADMIN_URL)
bash scripts/migrate.sh downgrade -1
```

## Tenancy: Row Level Security

- Two roles: the **owner** (`postgres`; migrations, seeding, the identity/onboarding layer) and the **app
  role** `aegis` (member of `aegis_app`) that the API and workers use. RLS is enforced for the app role.
- Every tenant table has the policy
  `USING/WITH CHECK (organization_id = aegis.current_org_id() OR aegis.is_org_member(organization_id))`.
- Request sessions (`get_db`) and workflow activity sessions (`session_scope(org)`) set
  `app.current_org_id` / `app.current_user_id` with `set_config(..., true)` — transaction-local, so a pooled
  connection never carries a tenant into the next transaction.
- Global catalogue tables readable by all tenants (writes only by the owner): `roles`,
  `lab.prompt_templates`, `lab.environments`, `lab.model_configs` (rows with `organization_id IS NULL` are
  platform-provided; tenants can add their own).
- **Defence in depth**: services also filter by the principal's organization and enforce project
  visibility, and caller-supplied references (mission, experiment, hypothesis ids) are resolved through the
  tenant- and project-scoped lookup `scoped_ref` before they are stored — foreign-key checks run without
  RLS, so an unchecked id could otherwise point into another tenant.

## Append-only data

`lab.protect_immutable(<mutable columns>)` rejects UPDATE/DELETE on history tables except for the listed
lifecycle columns:

| Table | Mutable columns |
| --- | --- |
| `lab.events`, `lab.research_events` | — (retention purge only, see below) |
| `lab.mission_versions`, `lab.agent_versions`, `lab.experiment_versions`, `lab.policy_versions`, `lab.discovery_versions` | — |
| `lab.evaluation_runs`, `lab.verification_runs` | — |
| `lab.model_usage`, `lab.compute_usage`, `lab.storage_usage` | — |
| `lab.dataset_versions` | `status`, `updated_at` |
| `lab.artifact_versions` | `scan_status`, `metadata`, `purged_at` |

`evidence` (shared with the assurance platform) is protected by `aegis.protect_evidence()`: identity, content,
hashes, chain position, title, source, storage key, confidence and ownership are frozen; only `legal_hold`,
`deleted_at` and a one-way purge (`purged_at` NULL → set, which may clear content) are allowed, and never under
legal hold. Deletion requires the transaction-local GUC `aegis.allow_evidence_delete = on`, which only the
retention job sets.

Evidence records form **hash chains** per scope (`mission:<id>` or `project:<id>`): each record stores
`content_hash` and `chain_hash = sha256(prev_hash || content_hash)`, written under an advisory lock.
`GET /missions/{id}/evidence/verify` recomputes both, so tampering by a database superuser is detected too.

## Ordering and concurrency

- `lab.events.seq` is allocated with `UPDATE missions SET event_seq = event_seq + 1 ... RETURNING` inside the
  writing transaction: per-mission sequence numbers are gap-free and strictly increasing (SSE `id:`).
- `missions.lock_version` implements optimistic locking for definition edits.
- `lab.workflow_runs` holds the lease (`lease_owner`, `lease_until`) for the inline engine and
  `temporal_workflow_id` / `temporal_run_id` for Temporal; `(organization_id, workflow, business_key)` is
  unique, which makes workflow starts idempotent.
- `lab.workflow_steps` memoizes activity results per `(run, step key)` for deterministic replay.

## Search indexes

| Index | Purpose |
| --- | --- |
| GIN FTS on memories, document chunks, research sources, claims, hypotheses, lessons, failures | keyword search |
| HNSW (`vector_cosine_ops`) on `lab.memories.embedding`, `lab.document_chunks.embedding` | semantic search |

Hybrid search fuses both rankings with reciprocal-rank fusion and only compares vectors produced by the same
embedding model (`embedding_model` column).

## Table groups

| Group | Tables |
| --- | --- |
| Missions & orchestration | `missions`, `mission_versions`, `events`, `workflow_runs`, `workflow_steps`, `workflow_signals`, `approvals` |
| Agents | `agents`, `agent_versions`, `agent_runs`, `agent_messages`, `prompt_templates`, `tool_calls` |
| Research & knowledge | `research_tasks`, `research_events`, `research_sources`, `documents`, `document_versions`, `document_chunks`, `graph_nodes`, `graph_edges`, `memories`, `memory_links` |
| Hypotheses & experiments | `hypotheses`, `hypothesis_evidence`, `experiments`, `experiment_versions`, `experiment_runs`, `experiment_metrics`, `run_comparisons`, `environments` |
| Execution & data | `compute_jobs`, `datasets`, `dataset_versions`, `artifacts`, `artifact_versions` |
| Evaluation, failures | `evaluators`, `evaluation_runs`, `failures`, `lessons` |
| Strategies & evolution | `strategies`, `strategy_versions`, `strategy_evaluations`, `strategy_mutations`, `evolution_runs` |
| Verification & discovery | `claims`, `claim_evidence`, `verifications`, `verification_runs`, `discoveries`, `discovery_versions`, `reports` |
| Governance & metering | `policies`, `policy_versions`, `mcp_servers`, `mcp_tools`, `model_configs`, `model_usage`, `compute_usage`, `storage_usage` |
| Benchmarks | `benchmark_runs`, `benchmark_results` |

## Retention

`python -m aegis_api.processes.scheduler` runs retention hourly per organization
(`POST /api/v1/admin/retention/run` on demand), using the organization's retention policy (defaults in days:
artifacts 365, raw outputs 90, agent logs 180, research events 365):

- standard-class artifact content past its window is deleted from object storage (row kept, `purged_at` set);
  ephemeral and log artifacts use the raw-output window;
- provider research events past their window are deleted (the only sanctioned delete on that append-only
  table, via `aegis.allow_evidence_delete`);
- expired idempotency keys and short-term memories are always removed; expired refresh tokens are removed by
  the identity layer (`purge_expired_refresh_tokens`).

Evidence-class artifacts (code bundles, run outputs referenced by evidence, reports) are never purged by
retention.
