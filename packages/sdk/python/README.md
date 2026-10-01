# aegis-ai — Python SDK for Aegis

Typed client for the Aegis REST API: audits, findings, evidence verification and signed export, Runtime Guard, Policy Studio and continuous assurance.

```bash
pip install aegis-ai          # or, from a checkout: pip install ./packages/sdk/python
```

Requires Python ≥ 3.10. Create an API key in the console under **API & SDK → API keys**; the key's role and scopes decide what the SDK may do.

## Quick start

```python
import os
from aegis_ai import Aegis

aegis = Aegis(os.environ["AEGIS_API_KEY"], base_url=os.environ.get("AEGIS_BASE_URL", "http://localhost:8000"))

# Audit a system and wait for the result
audit = aegis.audit(system_id, ["fairness", "privacy", "safety"], intensity="standard")
print(audit["status"], audit["findings_count"])

# Findings observed for the system
for f in aegis.findings.list(system_id=system_id, open_only=True).items:
    print(f["number"], f["severity"], f["title"])

# Evidence: verify the chain, export the signed package, verify the package
print(aegis.evidence.verify(audit["id"])["status"])  # VERIFIED / TAMPERED / ...
package = aegis.evidence.export(audit["id"])  # zip bytes
print(aegis.evidence.verify_package(package)["status"])
```

## Runtime Guard

```python
with aegis.runtime.trace(system_id, agent="support-agent") as trace:
    decision = trace.check(
        "tool.call", tool="send_email", payload={"destination": "external", "data_classification": "confidential"}
    )
    if decision.requires_approval:
        approved = aegis.runtime.wait_for_approval(decision.approval_id, timeout=300)
    elif decision.allowed:
        send_email(...)
    trace.event("model.response", payload={"output": text})  # batched; flushed on exit / every 100 events

# One-off check and batch ingestion
decision = aegis.runtime.check(
    system_id=system_id, event_type="database.query", payload={"statement": "DELETE FROM orders"}
)
aegis.runtime.events([{"event_type": "tool.result", "system_id": system_id, "payload": {...}}])
```

`decision.allowed` is what the agent must honour (always true in observe and audit mode). Use an API key with the `runtime` scope.

## Policy Studio and continuous assurance

```python
policy = aegis.policy.create(template_key="prevent-sensitive-exfiltration")
print(aegis.policy.simulate(policy_id=policy["id"], version=1, days=7)["blocked"])

aegis.assurance.trigger(system_id=system_id, event_type="prompt_change", ref=git_sha)  # from CI/CD
print(aegis.assurance.regression(audit["id"])["regression"])
```

## Errors, retries and idempotency

All API errors raise subclasses of `AegisAPIError` with `status_code`, `code`, `message`, `request_id` and `details`: `AuthenticationError`, `PermissionDeniedError`, `PlanLimitError` (quota details in `details`), `NotFoundError`, `ConflictError`, `ValidationError`, `RateLimitError`. Network failures raise `AegisConnectionError`.

Transient failures (connection errors, 429, 500 and 502–504) are retried with backoff (`max_retries`, default 2). Mutating requests carry an `Idempotency-Key` that is reused across retries, so a retried "create audit" never starts two audits.

## Async

```python
from aegis_ai import AsyncAegis

async with AsyncAegis(api_key, base_url=url) as aegis:
    decision = await aegis.runtime_check(system_id=system_id, event_type="tool.call", tool="send_email")
    audit = await aegis.create_audit(system_id=system_id, categories=["privacy"])
```

## Testing your integration

Pass an `httpx.MockTransport` as `transport=` to `Aegis(...)` to unit-test code that uses the SDK without a server.
