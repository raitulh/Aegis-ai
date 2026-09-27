"use client";
import { Activity, AlertTriangle, Fingerprint, ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Card, CardBody, CardHeader, CardTitle, EmptyState, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { Metric } from "@/components/dashboard/metric";
import { RiskTrendChart } from "@/components/charts/charts";
import { useMonitoring } from "@/lib/queries";
import { formatDateTime } from "@/lib/utils";

export default function MonitoringPage() {
  const query = useMonitoring({ days: 14 });
  return (
    <div>
      <PageHeader title="Continuous Monitoring" description="Sampled production evaluation for hallucination, PII and safety — with model-version change events." />
      <QueryBoundary query={query} skeleton={<div className="grid gap-3 sm:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-24 skeleton rounded-[var(--radius-lg)]" />)}</div>}>
        {(m) => (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Metric label="Requests (14d)" value={m.requests.toLocaleString()} icon={Activity} />
              <Metric label="Evaluated" value={m.evaluated.toLocaleString()} />
              <Metric label="PII incidents" value={m.pii_incidents} icon={Fingerprint} tone={m.pii_incidents ? "var(--color-critical)" : undefined} />
              <Metric label="Safety alerts" value={m.safety_alerts} icon={ShieldAlert} tone={m.safety_alerts ? "var(--color-high)" : undefined} />
            </div>
            <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
              <Card>
                <CardHeader><CardTitle>Risk Trend</CardTitle></CardHeader>
                <CardBody>{m.risk_trend.length ? <RiskTrendChart data={m.risk_trend} /> : <EmptyState icon={Activity} title="No trend data" description="Monitoring trend appears as production events accrue." />}</CardBody>
              </Card>
              <Card>
                <CardHeader><CardTitle>System Timeline</CardTitle></CardHeader>
                <CardBody className="space-y-2">
                  {m.model_events.length ? m.model_events.map((e, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent)]" />
                      <span className="text-[var(--color-text-subtle)]">{e.date}</span>
                      <span className="text-[var(--color-text-muted)]">{e.title}</span>
                    </div>
                  )) : <p className="text-sm text-[var(--color-text-subtle)]">No system change events.</p>}
                </CardBody>
              </Card>
            </div>
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-[var(--color-high)]" /> Recent Alerts</CardTitle></CardHeader>
              <CardBody className="space-y-2">
                {m.recent_alerts.length ? m.recent_alerts.map((a) => (
                  <div key={a.id} className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <SeverityBadge severity={a.severity} />
                      <div>
                        <p className="text-sm font-medium">{a.title}</p>
                        <p className="text-xs text-[var(--color-text-subtle)]">{a.action} · {formatDateTime(a.triggered_at)}</p>
                      </div>
                    </div>
                    <StatusBadge status={a.status} />
                  </div>
                )) : <EmptyState title="No alerts" description="Alerts appear when monitors detect threshold breaches." />}
              </CardBody>
            </Card>
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}
