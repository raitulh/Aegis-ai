"use client";
import { use } from "react";
import { Download, FileJson } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Button, Card, CardBody, CardHeader, CardTitle, RiskBadge, SeverityBadge } from "@/components/ui/primitives";
import { Logo } from "@/components/logo";
import { useAuditReport } from "@/lib/queries";

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useAuditReport(id);
  return (
    <div>
      <PageHeader title="AI Assurance Report" breadcrumbs={[{ label: "Reports", href: "/dashboard/reports" }, { label: id.slice(0, 8) }]} actions={query.data ? <div className="flex gap-2"><a href={`/bff/api/v1/reports/${query.data.id}/export?format=pdf`}><Button variant="secondary" size="sm" icon={Download}>PDF</Button></a><a href={`/bff/api/v1/reports/${query.data.id}/export?format=json`}><Button variant="ghost" size="sm" icon={FileJson}>JSON</Button></a></div> : null} />
      <QueryBoundary query={query} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
        {(report) => {
          const c = report.content;
          return (
            <div className="mx-auto max-w-3xl space-y-4">
              <Card>
                <CardBody>
                  <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-4">
                    <Logo />
                    <div className="text-right text-xs text-[var(--color-text-subtle)]">
                      <p>{c.system_description?.name}</p>
                      <p>{c.audit_metadata?.audit_id?.slice(0, 12)}</p>
                    </div>
                  </div>
                  <h2 className="mt-4 text-lg font-semibold">Executive Summary</h2>
                  <p className="mt-1 text-sm text-[var(--color-text-muted)]">{c.executive_summary?.headline}</p>
                  <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {Object.entries(c.executive_summary?.dimensions ?? {}).map(([k, v]) => (
                      <div key={k} className="rounded-[var(--radius)] bg-[var(--color-surface-2)] px-3 py-2">
                        <p className="text-xs capitalize text-[var(--color-text-subtle)]">{k}</p>
                        <p className="font-mono text-lg font-semibold">{Number(v).toFixed(0)}</p>
                      </div>
                    ))}
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader><CardTitle>Detailed Findings</CardTitle></CardHeader>
                <CardBody className="space-y-2">
                  {(c.detailed_findings ?? []).map((f: any) => (
                    <div key={f.number} className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--color-border)] px-3 py-2">
                      <div className="flex items-center gap-2"><SeverityBadge severity={f.severity} /><span className="text-sm">#{f.number} {f.title}</span></div>
                      <RiskBadge level={f.risk_level} />
                    </div>
                  ))}
                  {(c.detailed_findings ?? []).length === 0 ? <p className="text-sm text-[var(--color-text-subtle)]">No findings.</p> : null}
                </CardBody>
              </Card>

              <Card>
                <CardHeader><CardTitle>Limitations</CardTitle></CardHeader>
                <CardBody><ul className="space-y-1.5 text-xs text-[var(--color-text-muted)]">{(c.limitations ?? []).map((l: string, i: number) => <li key={i}>• {l}</li>)}</ul></CardBody>
              </Card>

              <p className="text-center font-mono text-[10px] text-[var(--color-text-subtle)]">Evidence integrity head: {c.evidence_integrity?.head_hash?.slice(0, 24) ?? "n/a"}…</p>
            </div>
          );
        }}
      </QueryBoundary>
    </div>
  );
}
