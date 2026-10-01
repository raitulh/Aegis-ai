import { ContentPage } from "@/components/marketing/content-page";

export const metadata = { title: "Developers", description: "The Aegis REST API, Python SDK and MCP server." };

const block = "overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4 font-mono text-xs";

export default function Page() {
  return (
    <ContentPage eyebrow="Developers" title="API, SDK & MCP" lede="A versioned REST API with a consistent error envelope and idempotent writes, a typed Python SDK, and an MCP server so agents can check actions and query assurance data.">
      <section className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-text)]">Python SDK</h2>
        <pre className={block}>{`pip install aegis-ai

from aegis_ai import Aegis
aegis = Aegis(api_key, base_url="https://aegis.example.com")

audit = aegis.audit(system_id, ["fairness", "privacy"])   # waits for the result
print(aegis.evidence.verify(audit["id"])["status"])       # VERIFIED / TAMPERED / ...`}</pre>
      </section>
      <section id="runtime" className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-text)]">Runtime Guard</h2>
        <pre className={block}>{`with aegis.runtime.trace(system_id, agent="billing-agent") as trace:
    decision = trace.check("tool.call", tool="issue_refund", payload={"amount": 1200})
    if decision.requires_approval:
        approved = aegis.runtime.wait_for_approval(decision.approval_id)
    elif decision.allowed:
        issue_refund(...)`}</pre>
        <p className="text-sm">In observe and audit mode every check answers “allowed” and is recorded; in enforce mode honour the decision. Payload fields that look like secrets or personal data are redacted before storage.</p>
      </section>
      <section className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-text)]">MCP server</h2>
        <pre className={block}>{`pip install aegis-mcp
AEGIS_API_KEY=aeg_live_... AEGIS_BASE_URL=https://aegis.example.com aegis-mcp`}</pre>
        <p className="text-sm">Tools include runtime checks, posture and findings, evidence verification, change reporting and policy simulation. The server has no privileges of its own — every call is made with your API key&apos;s role and scopes.</p>
      </section>
      <section className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-text)]">HTTP API</h2>
        <pre className={block}>{`curl -X POST "$AEGIS_BASE_URL/api/v1/assurance/triggers" \\
  -H "Authorization: Bearer $AEGIS_API_KEY" -H "Content-Type: application/json" \\
  -d '{"system_id":"…","event_type":"deployment","ref":"'$GIT_SHA'"}'`}</pre>
        <p className="text-sm">The OpenAPI reference is served by the API at <code className="font-mono">/docs</code>. Errors use one envelope with a request id; mutating calls accept an <code className="font-mono">Idempotency-Key</code> header.</p>
      </section>
    </ContentPage>
  );
}
