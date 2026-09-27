"use client";
import Link from "next/link";
import { Activity, Boxes, Gauge, ShieldAlert, ShieldCheck, TriangleAlert } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Metric, ScoreDelta } from "@/components/dashboard/metric";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Card, CardBody, CardHeader, CardTitle, EmptyState, Progress, RiskBadge, StatusBadge, Button } from "@/components/ui/primitives";
import { RiskTrendChart, SeverityBar, TrustPostureRadar } from "@/components/charts/charts";
import { useOverview } from "@/lib/queries";
import { pct, timeAgo, titleCase } from "@/lib/utils";
import { DIMENSIONS } from "@/lib/format";

export default function OverviewPage() {
  const query = useOverview();
  return (
    <div>
      <PageHeader
        title="AI Trust Posture"
        description="Continuous assurance across fairness, truthfulness, safety, privacy, security and governance."
        actions={
          <Link href="/dashboard/audits/new">
            <Button icon={Gauge}>New Audit</Button>
          </Link>
        }
      />
      <QueryBoundary query={query}>
        {(data) => (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <Metric label="Active Systems" value={data.active_systems} icon={Boxes} />
              <Metric label="Audits Today" value={data.audits_today} icon={Gauge} />
              <Metric label="Open Findings" value={data.open_findings} icon={ShieldAlert} />
              <Metric label="Critical Risks" value={data.critical_risks} icon={TriangleAlert} tone={data.critical_risks ? "var(--color-critical)" : undefined} />
              <Metric label="Policy Coverage" value={pct(data.policy_coverage, 1)} icon={ShieldCheck} />
            </div>

            <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
              <Card>
                <CardHeader>
                  <CardTitle>AI Risk Overview</CardTitle>
                  <span className="text-xs capitalize text-[var(--color-text-muted)]">Posture: {titleCase(data.posture)}</span>
                </CardHeader>
                <CardBody className="grid gap-4 sm:grid-cols-[1fr_1.1fr] sm:items-center">
                  {Object.keys(data.dimensions).length ? (
                    <>
                      <TrustPostureRadar current={data.dimensions} previous={data.previous_dimensions} />
                      <div className="space-y-2.5">
                        {DIMENSIONS.map((d) => {
                          const v = data.dimensions[d] ?? 0;
                          return (
                            <div key={d}>
                              <div className="mb-1 flex items-center justify-between text-xs">
                                <span className="capitalize text-[var(--color-text-muted)]">{d}</span>
                                <span className="flex items-center gap-2 font-mono">
                                  {v.toFixed(0)}
                                  <ScoreDelta current={v} previous={data.previous_dimensions[d]} />
                                </span>
                              </div>
                              <Progress value={v} color={v >= 85 ? "var(--color-success)" : v >= 70 ? "var(--color-medium)" : "var(--color-high)"} />
                            </div>
                          );
                        })}
                      </div>
                    </>
                  ) : (
                    <div className="sm:col-span-2">
                      <EmptyState icon={Gauge} title="No audits yet" description="Run your first audit to populate your AI trust posture." action={<Link href="/dashboard/audits/new"><Button size="sm">Create Audit</Button></Link>} />
                    </div>
                  )}
                </CardBody>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Findings by Severity</CardTitle>
                </CardHeader>
                <CardBody>
                  {Object.keys(data.findings_by_severity).length ? <SeverityBar counts={data.findings_by_severity} /> : <EmptyState title="No open findings" description="Findings will appear here after an audit." />}
                </CardBody>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Risk Trend (30 days)</CardTitle>
                <span className="text-xs text-[var(--color-text-muted)]">{data.test_volume.toLocaleString()} tests</span>
              </CardHeader>
              <CardBody>{data.risk_trend.length ? <RiskTrendChart data={data.risk_trend} /> : <EmptyState icon={Activity} title="No trend data yet" description="Trend appears once you have audits over time." />}</CardBody>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Recent Audit Runs</CardTitle>
                  <Link href="/dashboard/audits" className="text-xs text-[var(--color-accent-bright)] hover:underline">View all</Link>
                </CardHeader>
                <CardBody className="space-y-1">
                  {data.recent_audits.length ? (
                    data.recent_audits.map((a) => (
                      <Link key={a.id} href={`/dashboard/audits/${a.id}`} className="flex items-center justify-between rounded-[var(--radius)] px-2 py-2 hover:bg-[var(--color-surface-2)]">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{a.name}</p>
                          <p className="text-xs text-[var(--color-text-subtle)]">{timeAgo(a.created_at)}</p>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-[var(--color-text-muted)]">{a.findings} findings</span>
                          <StatusBadge status={a.status} />
                        </div>
                      </Link>
                    ))
                  ) : (
                    <p className="px-2 py-6 text-sm text-[var(--color-text-subtle)]">No audits yet.</p>
                  )}
                </CardBody>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Recent Incidents</CardTitle>
                  <Link href="/dashboard/findings" className="text-xs text-[var(--color-accent-bright)] hover:underline">View all</Link>
                </CardHeader>
                <CardBody className="space-y-1">
                  {data.recent_incidents.length ? (
                    data.recent_incidents.map((f) => (
                      <Link key={f.id} href={`/dashboard/findings/${f.id}`} className="flex items-center justify-between rounded-[var(--radius)] px-2 py-2 hover:bg-[var(--color-surface-2)]">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">#{f.number} {f.title}</p>
                          <p className="text-xs text-[var(--color-text-subtle)]">{timeAgo(f.created_at)}</p>
                        </div>
                        <RiskBadge level={f.risk_level} />
                      </Link>
                    ))
                  ) : (
                    <p className="px-2 py-6 text-sm text-[var(--color-text-subtle)]">No incidents. Nice.</p>
                  )}
                </CardBody>
              </Card>
            </div>
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}
