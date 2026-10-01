# Runtime Guard

Runtime Guard checks what agents actually do in production. Agents (or the SDK, the MCP server, or any HTTP client) send events describing actions; Aegis evaluates them against the **published runtime policies assigned to that system** and returns a decision.

## Event schema (`aegis.runtime.v1`)

```json
{
  "event_id": "optional, unique per event (idempotency key)",
  "event_type": "tool.call",
  "system_id": "<AI system id>",
  "timestamp": "2026-10-01T12:00:00Z",
  "source": "sdk",
  "environment": "production",
  "agent": "support-agent",
  "actor": "user-123",
  "session_id": "…", "trace_id": "…", "span_id": "…", "parent_span_id": "…",
  "tool": "send_email",
  "payload": { "destination": "external", "data_classification": "confidential", "to": "…" }
}
```

Event types: `agent.start|step|stop`, `tool.call`, `tool.result`, `model.request`, `model.response`, `policy.check`, `permission.request`, `network.request`, `file.read`, `file.write`, `database.query`, `mcp.tool.call`, `human.approval` (aliases: `external.network.request`, `mcp.call`). The envelope is strict; unknown payload keys are kept after redaction.

### Signals and redaction

At ingest, deterministic detectors derive **signals** from the payload — destination (internal/external, from hosts, URLs and recipient domains; private and loopback hosts count as internal), host, recipient domains, PII and secret types, data classification (`public < internal < confidential < restricted`), SQL statement kind, file path, and whether a human already approved the action. The stored payload is **redacted** (secrets, credentials and personal data are masked) before it is written; policies evaluate the signals, not raw secrets.

## Modes (per system)

| Mode | Decision returned to the agent | Side effects |
| --- | --- | --- |
| **observe** | always `allow` (the policy verdict is recorded as `decision`) | event + decision recorded |
| **audit** | always `allow` | violations create/refresh a **finding** with runtime **evidence** |
| **enforce** | the policy verdict (`allow`, `flag`, `require_approval`, `block`) | as audit, plus an **approval request** for `require_approval` |

`effective_decision` is what the agent must honour; `decision` is what the policy said. If the workspace plan does not include enforcement, an enforce-mode system is evaluated in audit mode and the response says so. Change the mode under **Runtime Guard → System modes** (`PUT /systems/{id}/runtime-mode`, permission `runtime:manage`).

## Endpoints

| Method | Path | Purpose | Permission |
| --- | --- | --- | --- |
| POST | `/runtime/events` | Batch ingest (≤ 500); idempotent per `event_id`; returns one decision per event | `runtime:ingest` |
| POST | `/runtime/check` | Synchronous decision for one action **before** it happens (never quota-limited) | `runtime:decide` |
| GET | `/runtime/events` | Filter by system, type, decision, agent, trace, time window | `runtime:read` |
| GET | `/runtime/overview` | Counts by decision and type, timeline, agents, pending approvals | `runtime:read` |
| GET | `/runtime/traces/{trace_id}` | All events of one trace | `runtime:read` |
| GET | `/runtime/approvals`, `/runtime/approvals/{id}` | Approval queue; agents poll the single approval | `runtime:read` |
| POST | `/runtime/approvals/{id}/decision` | `{approve, note}` — recorded as evidence and in the audit log | `approvals:decide` |
| GET | `/systems/{id}/runtime/verify` | Recompute the system's runtime evidence chain | `evidence:read` |

API keys need the `runtime` scope (or `ingest` for ingestion only).

## Approvals

An approval request expires after one hour if nobody decides; an expired or denied request means "do not proceed". Agents either poll `GET /runtime/approvals/{id}` or use `aegis.runtime.wait_for_approval(approval_id, timeout=…)`. Decisions are recorded with who decided, when, and the note.

## Evidence and findings

Each system has its own runtime evidence hash chain (appends are serialised with a PostgreSQL advisory lock). In audit and enforce mode, a policy match creates — or re-observes — a finding fingerprinted by `(system, policy, rule)`, linked to the evidence record. Findings then follow the normal lifecycle (triage, remediation, risk acceptance, re-open).

## SDK

```python
with aegis.runtime.trace(system_id, agent="billing-agent") as trace:
    decision = trace.check("tool.call", tool="issue_refund", payload={"amount": 1200})
    if decision.requires_approval:
        ok = aegis.runtime.wait_for_approval(decision.approval_id, timeout=300)
    elif decision.allowed:
        issue_refund(...)
    trace.event("model.response", payload={"output": text})  # batched; flushed on exit or every 100 events
```

## Limits and honest caveats

- Runtime Guard can only judge what it is told. An agent that does not report an action, or reports it inaccurately, is not protected. Use enforce mode with the synchronous check at the point where the action is executed.
- Detectors are deterministic pattern- and rule-based; they miss novel encodings of personal data or secrets. Treat `contains_pii=false` as "no known pattern found", not as proof.
- Retention: runtime events are purged after the plan's retention period; decisions that became evidence are kept.
