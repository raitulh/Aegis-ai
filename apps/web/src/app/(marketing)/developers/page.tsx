import { ContentPage } from "@/components/marketing/content-page";
export const metadata = { title: "Developers" };
export default function Page() {
  return (
    <ContentPage eyebrow="Developers" title="API, SDK & MCP" lede="A production REST API, a typed Python SDK, and an MCP server so agents can audit other agents.">
      <pre className="overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4 font-mono text-xs">{`pip install aegis-ai

from aegis_ai import Aegis
client = Aegis(api_key="aeg_live_...")
audit = client.audits.create(system_id=sid, categories=["fairness"])
print(client.audits.wait(audit["id"])["summary"]["dimensions"])`}</pre>
      <p>Every endpoint is documented with OpenAPI at <code className="font-mono text-[var(--color-accent-bright)]">/docs</code>. API keys are scoped by role and workspace.</p>
    </ContentPage>
  );
}
