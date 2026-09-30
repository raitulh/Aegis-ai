# Lab API

Base path `/api/v1`. The full, machine-readable contract is served at `/openapi.json` (interactive docs at
`/docs`); `scripts/export_openapi.py` writes it to disk for client generation. This page documents the
conventions and lists every lab endpoint (generated from the OpenAPI schema).

## Authentication and authorization

- **Browser sessions** (HttpOnly cookie via the web BFF), **API keys** (`Authorization: Bearer aegis_...`,
  role + scopes that can only *narrow* the role), and **service accounts** (optionally restricted to projects).
- Organization selection: the session's current organization, or `X-Aegis-Org: <uuid>` for members of several
  organizations. The client-supplied value is only honoured if the caller holds an active membership.
- Every endpoint declares a permission (`require("mission:run")`, ...). Some actions are **human-only** and are
  refused for API keys, service accounts and workflows regardless of role: approval decisions, discovery
  approval/publication, memory review, plan approval, strategy promotion, autonomy changes.
- Project visibility (`private` projects) and service-account project restrictions are enforced on every
  lookup; "not found" and "not permitted" both return **404** so existence never leaks across tenants.

## Errors

```json
{"error": {"code": "validation_error", "message": "…", "request_id": "req_…", "details": {…}}}
```

| Status | Codes |
| --- | --- |
| 401 | `unauthenticated` |
| 402 | `budget_exceeded` (model or compute budget would be exceeded) |
| 403 | `forbidden`, `policy_denied`, `feature_disabled`, `plan_limit_exceeded` |
| 404 | `not_found` (also for other tenants' resources) |
| 409 | `conflict` (stale `If-Match`), `invalid_state` (disallowed transition), `approval_required`, `idempotency_conflict` |
| 413 | `payload_too_large` |
| 422 | `validation_error`, `idempotency_key_reused` |
| 429 | `rate_limited` (with `Retry-After`), `quota_exceeded` |
| 503 | `service_unavailable` (e.g. `no_eligible_model` when external-model consent is missing) |

Every response carries `X-Request-ID`; logs and audit entries use the same id.

## Conventions

**Pagination** — list endpoints take `page` (≥ 1) and `page_size` (≤ 200) and return
`{"items": [...], "meta": {"page", "page_size", "total", "total_pages"}}`.

**Idempotency** — `POST /missions/{id}/launch`, `/research`, `/experiments/{id}/execute`,
`/experiment-runs/{id}/replay`, `/claims/{id}/verify`, `/missions/{id}/reports`, `/evolution-runs` and
`/benchmarks/runs` accept `Idempotency-Key`. A replay returns the stored response with
`Idempotent-Replayed: true`; the same key with a different body is rejected with `idempotency_key_reused`; a
concurrent duplicate still in progress gets 409 `idempotency_conflict`.

**Optimistic concurrency** — `PATCH /missions/{id}` honours `If-Match: <lock_version>`; a stale version returns
409 and nothing is changed.

**Asynchronous work** — endpoints that start work return **202** with `{"id", "status", "workflow_run_id"}`.
Progress is observable through the mission event stream, `/workflow-runs/{id}` and resource status fields.

**Event stream (SSE)** — `GET /missions/{id}/events/stream`:

```
id: 42
event: EXPERIMENT_COMPLETED
data: {"id": 42, "event_id": "<uuid>", "mission_id": "…", "event_type": "EXPERIMENT_COMPLETED", "level": "info",
       "message": "…", "data": {…}, "actor": "workflow:…", "trace_id": "…", "created_at": "…"}
```

Sequence ids are gap-free per mission. Reconnect with `Last-Event-ID: 42` to resume without loss; the server
sends `retry: 3000`, `: keep-alive` comments as heartbeats, and `event: stream_end` once the mission is terminal. The stream uses short database
transactions per poll (never one long transaction) and wakes on the event bus when Redis is configured.

**Uploads and downloads** — artifacts and datasets are streamed to object storage with size limits enforced
while streaming (413), filenames are sanitized, content is checksummed (SHA-256) and optionally malware
scanned. Downloads require `artifact:download`, are audit-logged, and are either a short-lived presigned
redirect (307) or streamed with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` and a
sandboxing CSP.

**Rate limits** — per principal and bucket (`research`, `execution`, `model`, `download`, plus the platform
defaults), configurable through `RATE_LIMIT_*`.

## Example: launch a mission and follow it

```bash
API=http://localhost:8000/api/v1; KEY="Authorization: Bearer $AEGIS_API_KEY"
curl -s -X POST $API/missions -H "$KEY" -H 'content-type: application/json' -d '{
  "project_id": "…", "title": "Annealing vs random search",
  "objective": "Determine whether simulated annealing beats random search on 5-D Rastrigin.",
  "success_criteria": [{"description": "lower objective value", "metric": "objective_value"}]
}'
# A human raises autonomy (API keys cannot), then:
curl -s -X POST $API/missions/$MISSION/launch -H "$KEY" -H "Idempotency-Key: $(uuidgen)"
curl -N $API/missions/$MISSION/events/stream -H "$KEY" -H 'Last-Event-ID: 0'
```

## Endpoint map

### Organization (18)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/organizations/current` | Current Organization |
| `PATCH` | `/api/v1/organizations/current` | Update Organization |
| `GET` | `/api/v1/projects` | List Projects |
| `POST` | `/api/v1/projects` | Create Project |
| `GET` | `/api/v1/projects/{project_id}` | Get Project Endpoint |
| `PATCH` | `/api/v1/projects/{project_id}` | Update Project |
| `GET` | `/api/v1/projects/{project_id}/members` | Project Members |
| `PUT` | `/api/v1/projects/{project_id}/members` | Set Project Member |
| `DELETE` | `/api/v1/projects/{project_id}/members/{user_id}` | Remove Project Member |
| `GET` | `/api/v1/teams` | List Teams |
| `POST` | `/api/v1/teams` | Create Team |
| `PUT` | `/api/v1/teams/{team_id}/members` | Set Team Members |
| `GET` | `/api/v1/users` | List Users |
| `GET` | `/api/v1/users/me` | Me |
| `GET` | `/api/v1/workspaces` | List Workspaces |
| `POST` | `/api/v1/workspaces` | Create Workspace |
| `PATCH` | `/api/v1/workspaces/{workspace_id}` | Update Workspace |
| `PUT` | `/api/v1/workspaces/{workspace_id}/members` | Set Workspace Member |

### Missions (29)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/agent-roles` | Agent Roles |
| `GET` | `/api/v1/agent-runs` | List Agent Runs |
| `GET` | `/api/v1/agent-runs/{run_id}` | Get Agent Run |
| `GET` | `/api/v1/agents` | List Agents |
| `POST` | `/api/v1/agents` | Create Agent |
| `GET` | `/api/v1/agents/{agent_id}/versions` | Agent Versions |
| `POST` | `/api/v1/agents/{agent_id}/versions` | Create Agent Version |
| `GET` | `/api/v1/autonomy-levels` | Autonomy Levels |
| `GET` | `/api/v1/events/{event_id}` | Get Event |
| `GET` | `/api/v1/missions` | List Missions |
| `POST` | `/api/v1/missions` | Create Mission |
| `GET` | `/api/v1/missions/{mission_id}` | Get Mission |
| `PATCH` | `/api/v1/missions/{mission_id}` | Update Mission |
| `POST` | `/api/v1/missions/{mission_id}/autonomy` | Change Autonomy |
| `POST` | `/api/v1/missions/{mission_id}/cancel` | Cancel Mission |
| `GET` | `/api/v1/missions/{mission_id}/events` | Mission Events |
| `GET` | `/api/v1/missions/{mission_id}/events/stream` | Mission Event Stream |
| `GET` | `/api/v1/missions/{mission_id}/evidence` | Mission Evidence |
| `GET` | `/api/v1/missions/{mission_id}/evidence/verify` | Verify Mission Evidence |
| `POST` | `/api/v1/missions/{mission_id}/launch` | Launch Mission |
| `GET` | `/api/v1/missions/{mission_id}/observability` | Mission Observability |
| `POST` | `/api/v1/missions/{mission_id}/pause` | Pause Mission |
| `POST` | `/api/v1/missions/{mission_id}/resume` | Resume Mission |
| `GET` | `/api/v1/missions/{mission_id}/versions` | Mission Versions |
| `GET` | `/api/v1/prompts` | List Prompts |
| `POST` | `/api/v1/prompts` | Register Prompt |
| `POST` | `/api/v1/prompts/{prompt_id}/retire` | Retire Prompt |
| `GET` | `/api/v1/workflow-runs` | List Workflow Runs |
| `GET` | `/api/v1/workflow-runs/{run_id}` | Get Workflow Run |

### Science (51)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/artifacts` | List Artifacts |
| `POST` | `/api/v1/artifacts` | Upload Artifact |
| `GET` | `/api/v1/artifacts/{artifact_id}` | Get Artifact |
| `GET` | `/api/v1/artifacts/{artifact_id}/download` | Download Artifact |
| `GET` | `/api/v1/dataset-versions/{version_id}/lineage` | Dataset Lineage |
| `GET` | `/api/v1/datasets` | List Datasets |
| `POST` | `/api/v1/datasets` | Create Dataset |
| `GET` | `/api/v1/datasets/{dataset_id}/versions` | Dataset Versions |
| `POST` | `/api/v1/datasets/{dataset_id}/versions` | Upload Dataset Version |
| `GET` | `/api/v1/environments` | List Envs |
| `POST` | `/api/v1/environments` | Register Env |
| `GET` | `/api/v1/experiment-runs/{run_id}` | Get Run |
| `GET` | `/api/v1/experiment-runs/{run_id}/manifest` | Run Manifest |
| `POST` | `/api/v1/experiment-runs/{run_id}/replay` | Replay Run |
| `GET` | `/api/v1/experiments` | List Experiments |
| `POST` | `/api/v1/experiments` | Create Experiment |
| `POST` | `/api/v1/experiments/validate` | Validate Experiment Spec |
| `GET` | `/api/v1/experiments/{experiment_id}` | Get Experiment |
| `POST` | `/api/v1/experiments/{experiment_id}/code` | Attach Code |
| `GET` | `/api/v1/experiments/{experiment_id}/comparisons` | Experiment Comparisons |
| `GET` | `/api/v1/experiments/{experiment_id}/evaluations` | Experiment Evaluations |
| `POST` | `/api/v1/experiments/{experiment_id}/execute` | Execute Experiment |
| `GET` | `/api/v1/experiments/{experiment_id}/reproducibility-package` | Reproducibility Package |
| `GET` | `/api/v1/experiments/{experiment_id}/runs` | Experiment Runs |
| `GET` | `/api/v1/experiments/{experiment_id}/versions` | Experiment Versions |
| `POST` | `/api/v1/experiments/{experiment_id}/versions` | Create Experiment Version |
| `GET` | `/api/v1/graph/nodes` | Graph Nodes |
| `GET` | `/api/v1/graph/nodes/{node_id}/neighborhood` | Graph Neighborhood |
| `GET` | `/api/v1/harnesses` | List Harnesses |
| `GET` | `/api/v1/hypotheses` | List Hypotheses |
| `POST` | `/api/v1/hypotheses` | Create Hypothesis |
| `GET` | `/api/v1/hypotheses/{hypothesis_id}` | Get Hypothesis |
| `POST` | `/api/v1/hypotheses/{hypothesis_id}/evidence` | Add Hypothesis Evidence |
| `POST` | `/api/v1/hypotheses/{hypothesis_id}/transition` | Transition Hypothesis |
| `GET` | `/api/v1/knowledge/documents` | List Documents |
| `POST` | `/api/v1/knowledge/documents` | Upload Document |
| `POST` | `/api/v1/knowledge/search` | Search Knowledge |
| `POST` | `/api/v1/knowledge/urls` | Ingest Url |
| `GET` | `/api/v1/memory` | List Memory |
| `POST` | `/api/v1/memory` | Propose Memory |
| `POST` | `/api/v1/memory/search` | Search Memory |
| `GET` | `/api/v1/memory/{memory_id}` | Get Memory |
| `POST` | `/api/v1/memory/{memory_id}/review` | Review Memory |
| `POST` | `/api/v1/memory/{memory_id}/supersede` | Supersede Memory |
| `GET` | `/api/v1/papers` | List Sources |
| `GET` | `/api/v1/research` | List Research |
| `POST` | `/api/v1/research` | Create Research |
| `GET` | `/api/v1/research/{task_id}` | Get Research |
| `POST` | `/api/v1/research/{task_id}/cancel` | Cancel Research |
| `GET` | `/api/v1/research/{task_id}/events` | Research Events |
| `GET` | `/api/v1/research/{task_id}/report` | Research Report |

### Verification (31)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/claims` | List Claims |
| `POST` | `/api/v1/claims` | Create Claim |
| `GET` | `/api/v1/claims/{claim_id}` | Get Claim |
| `GET` | `/api/v1/claims/{claim_id}/lineage` | Claim Lineage |
| `POST` | `/api/v1/claims/{claim_id}/verify` | Verify Claim |
| `GET` | `/api/v1/discoveries` | List Discoveries |
| `GET` | `/api/v1/discoveries/{discovery_id}` | Get Discovery |
| `POST` | `/api/v1/discoveries/{discovery_id}/contest` | Contest Discovery |
| `POST` | `/api/v1/discoveries/{discovery_id}/publication` | Request Publication |
| `POST` | `/api/v1/discoveries/{discovery_id}/review` | Review Discovery |
| `GET` | `/api/v1/evaluations/evaluators` | Evaluator Catalog |
| `GET` | `/api/v1/evolution-runs` | List Evolution Runs |
| `POST` | `/api/v1/evolution-runs` | Start Evolution |
| `GET` | `/api/v1/evolution-runs/{run_id}` | Get Evolution Run |
| `GET` | `/api/v1/failures` | List Failures |
| `GET` | `/api/v1/failures/{failure_id}` | Get Failure |
| `POST` | `/api/v1/failures/{failure_id}/status` | Set Failure Status |
| `GET` | `/api/v1/lessons` | List Lessons |
| `POST` | `/api/v1/missions/{mission_id}/reports` | Generate Report |
| `GET` | `/api/v1/research-reports` | List Reports |
| `GET` | `/api/v1/research-reports/{report_id}` | Get Report |
| `GET` | `/api/v1/strategies` | List Strategies |
| `POST` | `/api/v1/strategies` | Create Strategy |
| `GET` | `/api/v1/strategies/{strategy_id}` | Get Strategy |
| `POST` | `/api/v1/strategies/{strategy_id}/promote` | Promote Strategy |
| `GET` | `/api/v1/strategies/{strategy_id}/promotion-check` | Promotion Check |
| `POST` | `/api/v1/strategies/{strategy_id}/rollback` | Rollback Strategy |
| `POST` | `/api/v1/strategies/{strategy_id}/versions` | Create Strategy Version |
| `GET` | `/api/v1/strategy-versions/{version_id}/lineage` | Strategy Lineage |
| `GET` | `/api/v1/verifications` | List Verifications |
| `GET` | `/api/v1/verifications/{verification_id}` | Get Verification |

### Governance (38)

| Method | Path | Summary |
| --- | --- | --- |
| `POST` | `/api/v1/admin/billing/rollup` | Run Rollup |
| `POST` | `/api/v1/admin/retention/run` | Run Retention |
| `GET` | `/api/v1/admin/workflow-engine` | Workflow Engine Info |
| `GET` | `/api/v1/approvals` | List Approvals |
| `GET` | `/api/v1/approvals/{approval_id}` | Get Approval |
| `POST` | `/api/v1/approvals/{approval_id}/decide` | Decide Approval |
| `GET` | `/api/v1/audit` | Lab Audit |
| `GET` | `/api/v1/benchmarks/runs` | List Benchmark Runs |
| `POST` | `/api/v1/benchmarks/runs` | Start Benchmark |
| `GET` | `/api/v1/benchmarks/runs/{run_id}` | Get Benchmark Run |
| `GET` | `/api/v1/benchmarks/suites` | Benchmark Suites |
| `GET` | `/api/v1/billing` | Billing Summary |
| `GET` | `/api/v1/lab-policies` | List Lab Policies |
| `POST` | `/api/v1/lab-policies` | Create Lab Policy |
| `GET` | `/api/v1/lab-policies/baseline` | Baseline Policy |
| `POST` | `/api/v1/lab-policies/simulate` | Simulate Policy |
| `GET` | `/api/v1/lab-policies/{policy_id}` | Get Lab Policy |
| `POST` | `/api/v1/lab-policies/{policy_id}/status` | Set Lab Policy Status |
| `POST` | `/api/v1/lab-policies/{policy_id}/versions` | Create Lab Policy Version |
| `GET` | `/api/v1/mcp/servers` | List Mcp Servers |
| `POST` | `/api/v1/mcp/servers` | Register Mcp Server |
| `POST` | `/api/v1/mcp/servers/{server_id}/disable` | Disable Mcp Server |
| `POST` | `/api/v1/mcp/servers/{server_id}/discover` | Discover Mcp Tools |
| `POST` | `/api/v1/mcp/servers/{server_id}/review` | Review Mcp Server |
| `GET` | `/api/v1/mcp/servers/{server_id}/tools` | List Mcp Tools |
| `POST` | `/api/v1/mcp/tools/{tool_id}/approval` | Approve Mcp Tool |
| `GET` | `/api/v1/models` | List Models |
| `POST` | `/api/v1/models` | Upsert Model Config |
| `POST` | `/api/v1/models/route-preview` | Route Preview |
| `GET` | `/api/v1/tool-calls` | List Tool Calls |
| `GET` | `/api/v1/tools` | List Tools |
| `GET` | `/api/v1/usage` | Usage Summary |
| `GET` | `/api/v1/webhooks` | List Webhooks |
| `POST` | `/api/v1/webhooks` | Create Webhook |
| `GET` | `/api/v1/webhooks/events` | Webhook Event Names |
| `DELETE` | `/api/v1/webhooks/{webhook_id}` | Delete Webhook |
| `GET` | `/api/v1/webhooks/{webhook_id}/deliveries` | Webhook Deliveries |
| `POST` | `/api/v1/webhooks/{webhook_id}/rotate-secret` | Rotate Webhook Secret |

### Health (5)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/system/info` | System Info |
| `GET` | `/health` | Health |
| `GET` | `/health/live` | Live |
| `GET` | `/health/ready` | Ready Detailed |
| `GET` | `/ready` | Ready |
