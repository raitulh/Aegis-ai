"use client";
import { use, useState } from "react";
import { CheckCircle2, Network, XCircle } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Badge, Card, CardBody, EmptyState } from "@/components/ui/primitives";
import { Sheet, SheetContent } from "@/components/ui/overlays";
import { TraceTimeline } from "@/components/dashboard/trace-timeline";
import { useSystem, useTrace, useTraces } from "@/lib/queries";
import {titleCase} from "@/lib/utils";

export default function AgentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const system = useSystem(id);
  const traces = useTraces(id);
  const [openTrace, setOpenTrace] = useState<string | null>(null);

  return (
    <div>
      <QueryBoundary query={system} skeleton={<div className="h-20 skeleton rounded-[var(--radius-lg)]" />}>
        {(s) => <PageHeader title={s.name} breadcrumbs={[{ label: "Agents", href: "/dashboard/agents" }, { label: "Traces" }]} description="Every trace shows the observable steps and each tool call's authorization decision." />}
      </QueryBoundary>
      <QueryBoundary query={traces} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState icon={Network} title="No traces yet" description="Run an audit against this agent to capture traces, or ingest traces via the SDK." />
          ) : (
            <div className="space-y-2">
              {page.items.map((t) => (
                <button key={t.id} onClick={() => setOpenTrace(t.id)} className="flex w-full items-center gap-3 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-left hover:border-[var(--color-border-strong)]">
                  {t.violation_count > 0 ? <XCircle className="h-5 w-5 shrink-0 text-[var(--color-critical)]" /> : <CheckCircle2 className="h-5 w-5 shrink-0 text-[var(--color-success)]" />}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{t.name ?? t.trace_id}</p>
                    <p className="truncate text-xs text-[var(--color-text-subtle)]">{t.input_summary}</p>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                    <span>{t.tool_call_count} tools</span>
                    {t.violation_count > 0 ? <Badge tone="warning">{t.violation_count} violation{t.violation_count > 1 ? "s" : ""}</Badge> : <Badge tone="success">clean</Badge>}
                  </div>
                </button>
              ))}
            </div>
          )
        }
      </QueryBoundary>

      <Sheet open={!!openTrace} onOpenChange={(o) => !o && setOpenTrace(null)}>
        <SheetContent title="Agent Trace" description="Observable execution and tool-action audit.">
          {openTrace ? <TraceDetail traceId={openTrace} /> : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function TraceDetail({ traceId }: { traceId: string }) {
  const query = useTrace(traceId);
  return (
    <div className="p-6">
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton" />}>
        {(trace) => (
          <div className="space-y-5">
            <div className="grid grid-cols-3 gap-2 text-center">
              <Metric label="Spans" value={trace.span_count} />
              <Metric label="Tool calls" value={trace.tool_call_count} />
              <Metric label="Violations" value={trace.violation_count} tone={trace.violation_count ? "var(--color-critical)" : "var(--color-success)"} />
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-subtle)]">Execution timeline</p>
              <TraceTimeline events={trace.events} />
            </div>
            {trace.tool_calls.length ? (
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-subtle)]">Tool-action audit</p>
                <div className="space-y-2">
                  {trace.tool_calls.map((c) => (
                    <Card key={c.id}>
                      <CardBody className="py-3">
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-sm font-medium">{c.tool_name}</span>
                          <Badge tone={c.allowed ? "success" : "warning"}>{c.allowed ? "Allowed" : "Blocked"}</Badge>
                        </div>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                          <Field label="Authorization" value={titleCase(c.authorization)} />
                          <Field label="Human approval" value={c.requires_human_approval ? (c.human_approved ? "Approved" : "Required, missing") : "Not required"} />
                          {c.sensitive_data_detected ? <Field label="Sensitive data" value={c.sensitive_types.join(", ")} tone="var(--color-high)" /> : null}
                          {c.control_ref ? <Field label="Control" value={c.control_ref} /> : null}
                        </div>
                        {c.violations.length ? <p className="mt-2 rounded-[var(--radius-sm)] bg-[color-mix(in_srgb,var(--color-critical)_10%,transparent)] px-2 py-1.5 text-xs text-[var(--color-critical)]">{c.violations.map((v) => v.message).join(" ")}</p> : null}
                      </CardBody>
                    </Card>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return <div className="rounded-[var(--radius)] bg-[var(--color-surface-2)] py-2"><p className="font-mono text-lg font-semibold" style={{ color: tone }}>{value}</p><p className="text-[10px] text-[var(--color-text-subtle)]">{label}</p></div>;
}
function Field({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div><span className="text-[var(--color-text-subtle)]">{label}: </span><span style={{ color: tone }} className="text-[var(--color-text-muted)]">{value}</span></div>;
}
