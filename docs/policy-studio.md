# Policy Studio (runtime policies)

Runtime policies decide what agents may do. They are written in a small YAML DSL, compiled deterministically, versioned immutably, and only take effect once a version is **published** and the policy is **assigned**.

## The DSL

```yaml
name: Prevent sensitive data exfiltration
description: Confidential data must not leave the organization without a human decision.
params:
  internal_domains: [acme.example]
rules:
  - id: restricted-external-block
    when:
      event: [network.request, tool.call, mcp.tool.call]
      destination: external
      data_classification: restricted
    action: block
    severity: critical
    message: Restricted data cannot be sent to an external destination.
  - id: destructive-sql
    when:
      event: database.query
      statement: [delete, drop, truncate, alter]
      approved: false
    action: require_approval
    severity: high
    message: Destructive database statements require approval.
```

`when` keys (all must match; omitted keys match anything):

| Key | Meaning |
| --- | --- |
| `event` | event type or list; `*` globs (`file.*`) |
| `tool`, `agent`, `environment`, `source` | value or list; globs allowed |
| `destination` | `external` or `internal` (relative to `params.internal_domains`; private/loopback hosts are internal) |
| `data_classification` | minimum level: public < internal < confidential < restricted |
| `contains_pii`, `contains_secret`, `approved` | booleans from deterministic detectors |
| `statement` | SQL statement kinds |
| `conditions` | list of `{field, op, value}` over `payload.*`, `signals.*` or envelope fields. Ops: `eq ne in not_in contains not_contains starts_with ends_with matches gt gte lt lte exists` |

`action`: `allow | flag | require_approval | block`. When several rules (across all assigned policies) match, the **most restrictive** wins: block > require_approval > flag > allow. Limits: 100 rules per policy, 64 KiB source, regexes ≤ 200 characters (validated at compile time). Unknown keys are rejected — a typo is an error, not a silently ignored rule.

## Lifecycle

1. **Create** — blank, or from the library (`GET /runtime-policies/templates`). Creates version 1 as a draft.
2. **Edit & validate** — the editor validates as you type (`POST /runtime-policies/validate`); nothing is recorded.
3. **Test** — evaluate the draft against one sample event (`POST /runtime-policies/test`).
4. **Simulate** — replay up to 20,000 of your recorded runtime events from the last 1–90 days through the draft or any stored version (`POST /runtime-policies/simulate`). The result reports allowed / flagged / approval / blocked counts, matches by rule and by system, affected workflows (sessions), decisions that would change versus what was recorded, and sample events. Every number is counted from real events; simulations are audit-logged.
5. **Save version** — versions are immutable and checksummed; saving an identical source is rejected.
6. **Diff** — unified text diff plus added / removed / changed rule ids between any two versions.
7. **Publish** — makes one version live (permission `policies:publish`); the previous published version becomes superseded.
8. **Rollback** — re-publish an earlier version; history is preserved.
9. **Assign** — to the whole workspace, an environment, or a single system. Disable/enable a policy without losing its history.

Every publish, rollback, assignment and simulation is written to the audit log.

## Templates

The library ships starter policies: sensitive-data exfiltration, secret leakage, human approval for irreversible actions, tool allow-list, filesystem boundaries, production network egress, MCP tool governance and PII in model output. A template is a technical control, not a compliance attestation; adopting one creates a policy you own and should adapt.

## Permissions

`policies:read` (view, validate, test, simulate), `policies:write` (create, edit, clone), `policies:publish` (publish, rollback, enable/disable, assign).
