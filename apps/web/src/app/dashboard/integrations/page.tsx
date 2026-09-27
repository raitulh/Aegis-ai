"use client";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Badge, Card, CardBody } from "@/components/ui/primitives";
import { useIntegrations } from "@/lib/queries";
import { titleCase } from "@/lib/utils";

const DESC: Record<string, string> = {
  ollama: "Run local models (Qwen, Llama) — data never leaves your infrastructure.",
  gemini: "Google Gemini models for inference and model-assisted evaluation.",
  openai: "OpenAI models via API key.",
  anthropic: "Anthropic Claude models via API key.",
  webhook: "Receive signed events: audit.completed, finding.created, critical_risk.detected.",
  github: "Link findings and runs to your repositories.",
  slack: "Post critical alerts to a Slack channel.",
  email: "SMTP delivery for alerts and reports.",
};

const STATUS_TONE: Record<string, "success" | "warning" | "neutral"> = { connected: "success", error: "warning", not_configured: "neutral", disconnected: "neutral", unknown: "neutral" };

export default function IntegrationsPage() {
  const query = useIntegrations();
  return (
    <div>
      <PageHeader title="Integrations" description="Connect model providers and delivery channels. Ollama and Gemini have working implementations." />
      <QueryBoundary query={query} skeleton={<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-32 skeleton rounded-[var(--radius-lg)]" />)}</div>}>
        {(items) => (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((i) => (
              <Card key={i.kind}>
                <CardBody>
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{titleCase(i.name)}</span>
                    <Badge tone={STATUS_TONE[i.status] ?? "neutral"}>{titleCase(i.status)}</Badge>
                  </div>
                  <p className="mt-2 text-xs text-[var(--color-text-muted)]">{DESC[i.kind] ?? ""}</p>
                  <div className="mt-3 flex gap-2">
                    <button className="rounded-[var(--radius-sm)] border border-[var(--color-border-strong)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]" disabled>
                      {i.status === "connected" ? "Manage" : "Configure"}
                    </button>
                  </div>
                </CardBody>
              </Card>
            ))}
          </div>
        )}
      </QueryBoundary>
      <p className="mt-4 text-xs text-[var(--color-text-subtle)]">Provider connections are configured under a system's provider. Others show configuration support; Aegis never fakes a successful connection.</p>
    </div>
  );
}
