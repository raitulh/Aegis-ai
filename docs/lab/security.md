# Lab security model

The lab runs untrusted inputs (documents, web pages, model output, generated code) next to sensitive assets
(tenant data, credentials, budgets). Its posture is **fail-safe by default**: network, secrets, production
integrations, auto-promotion and auto-approval are denied unless explicitly granted, and every consequential
decision is taken by deterministic code or a human — never by a model alone.

This page complements the platform-wide [security.md](../security.md) (authentication, RLS, secrets storage,
HTTP hardening).

## Threats and controls

| Threat | Control | Where | Proven by |
| --- | --- | --- | --- |
| Generated code attacks the platform | Code runs only in sandboxes (no network, no credentials, non-root, read-only FS, dropped capabilities, noexec tmp, limits); never in API/worker processes | `infrastructure/execution/` | `tests/unit/lab/test_sandbox_docker.py` |
| Code under test fakes its results | Metrics come from a separate harness container; self-reported metrics are flagged and cannot satisfy verification; independent re-evaluation and checksum re-verification | `services/lab/execution.py`, `verification.py` | `test_mission_e2e.py`, `test_verification_security_knowledge.py` |
| Prompt injection via documents, pages, tool output, memories | Detection + sanitization + nonce-fenced untrusted blocks, protected system policy, allowlisted tools only, quarantine at score ≥ 0.8 | `engines/lab/security/prompt_injection.py`, `services/lab/agents.py`, `tools.py` | `test_lab_security.py::test_tool_broker_*`, unit injection tests |
| Agent escalates its own autonomy or permissions | Autonomy changes are human-only and ceiling-bounded in code (`validate_autonomy_change`), with baseline deny rules for agents/workflows as defence in depth; workflow principals are snapshots of the launcher and can never exceed them | `services/lab/missions.py`, `engines/lab/policy/engine.py`, `security/context.py` | `test_lab_api.py::test_autonomy_rules_*`, `test_lab_security.py::test_workflow_principal_*` |
| Evolved strategy gains capabilities | Immutable governance envelope; escalation check denied by baseline policy; promotion gated statistically + human | `services/lab/strategies.py`, `engines/lab/evolution/` | `test_lab_security.py::test_strategy_versions_cannot_escalate_governance` |
| Self-approval / rubber-stamping | Approvals human-only, permission per approval kind, separation of duties (requester and mission launcher cannot approve their own discovery) | `services/lab/approvals.py`, `discoveries.py` | `test_lab_api.py::test_approvals_*`, `test_mission_e2e.py` |
| Durable memory poisoning | Automation-proposed durable memories require human review; quarantine; provenance; supersession instead of edits | `services/lab/memory.py` | `test_lab_api.py::test_memory_governance_*` |
| Cross-tenant access | RLS on every tenant table + application filters + 404-on-not-permitted + scoped references (FK checks bypass RLS) | `services/lab/access.py`, migration 0003 | `test_lab_api.py::test_projects_*`, `test_lab_security.py::test_other_tenant_*` |
| SSRF through ingestion, webhooks, MCP | `validate_outbound_url` (scheme, credentials-in-URL, blocked hosts, private/link-local/metadata ranges after DNS resolution), re-validated on each redirect and at call time | `security/ssrf.py` | `test_lab_security.py::test_ssrf_*` |
| Malicious uploads | Streaming size limits, filename sanitization, executable rejection, optional ClamAV, checksums, dataset path validation | `services/lab/artifacts.py`, `datasets.py`, `knowledge.py` | `test_lab_security.py::test_dataset_path_traversal_*`, `test_lab_api.py::test_artifact_*` |
| Unregistered or unapproved MCP tools | Servers start `pending_review`; each tool needs human approval; schema changes revoke approval; credentials write-only and encrypted | `services/lab/mcp.py` | `test_lab_security.py::test_unapproved_mcp_*` |
| Evidence tampering | Append-only triggers (all recorded fields), per-scope hash chains, chain verification that recomputes content hashes | migrations 0003/0004, `services/lab/evidence.py` | `test_lab_security.py::test_evidence_is_append_only_*` |
| Runaway cost | Budgets (mission/project/org) checked before model calls and executions through policy; per-run tool-call cap; step limits; rate limits | `services/lab/usage.py`, `model_gateway.py` | `test_model_gateway.py::test_budget_*` |
| Data leaving the organization | External providers require `allow_external_models` consent; hash embeddings otherwise | `services/lab/model_gateway.py` | `test_model_gateway.py::test_external_providers_require_consent` |
| Secrets in logs or records | Structured-log redaction, `redact_data` for persisted tool arguments, write-only secrets, sandbox env allowlist | `logging.py`, `tools.py`, `execution/base.py` | `test_adapters.py::test_redaction_*`, `test_sandbox_docker.py` |
| Replay / duplicate side effects | Idempotency keys, idempotent workflow starts, memoized activities, deterministic run keys | `idempotency.py`, `workflows/` | `test_lab_api.py::test_idempotency_*`, `test_workflow_engine.py` |
| Forged webhooks | HMAC-SHA256 signatures with timestamp and 5-minute tolerance; per-endpoint secrets shown once; rotation | `security/webhook_signing.py`, `services/webhook_service.py` | `test_adapters.py::test_webhook_signature_*` |

## Policy engine

`engines/lab/policy/engine.py` evaluates JSON rules over a facts document (actor, organization quota, mission,
budget, tool, execution, strategy, discovery, memory ...) with **deny-overrides** precedence
(`deny > require_approval > allow`). A non-removable **platform baseline** (`GET /api/v1/lab-policies/baseline`)
encodes the fail-safe defaults; organization policies (`/lab-policies`, versioned) can add restrictions but can
never relax the baseline (keys starting with `system.` are reserved). `POST /lab-policies/simulate` dry-runs a
decision. Denials are audited.

Actions evaluated by the services: `model.call`, `tool.call`, `experiment.execute`, `reproduction.run`,
`strategy.create`, `strategy.mutate`, `strategy.promote`, `memory.promote`, `discovery.approve`,
`discovery.publish`. The baseline also carries deny rules for `autonomy.change`, `permission.grant` and
`role.assign` by agents/workflows as defence in depth; autonomy itself is enforced in code
(`validate_autonomy_change` + human-only checks), independent of policy configuration.

## Human-only actions

Regardless of role, these are refused for API keys, service accounts, guests and workflows:
approval decisions, discovery approval and publication, memory review, plan approval, strategy promotion,
autonomy changes, MCP server review and tool approval.

## Residual risks and operator responsibilities

- The Docker backend in `docker-compose.yml` uses a privileged Docker-in-Docker daemon; it isolates the host
  daemon but is not a hardened multi-tenant boundary. Use the Kubernetes backend with gVisor (or Kata) for
  production multi-tenant workloads.
- Kubernetes sandbox pods may reach object storage (for the presigned output upload) and, if the presign host
  is a DNS name, kube-dns. Use a ClusterIP endpoint and remove the DNS rule to close DNS as a covert channel.
- Allowlisted egress for experiments requires an egress gateway that enforces per-run allowlists; the backend
  labels pods but NetworkPolicy alone cannot enforce hostnames.
- Prompt-injection detection is heuristic; the structural controls (fencing, allowlists, human gates,
  deterministic decisions) are what bound the impact.
- Nothing in the lab constitutes a compliance certification; governance outputs are readiness evidence.
