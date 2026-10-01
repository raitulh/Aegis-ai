"use client";
import { Activity, Boxes, FileCheck2, Gauge, Hand, Radar, ShieldAlert, ShieldCheck, TriangleAlert } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { Metric, ScoreDelta } from "@/components/dashboard/metric";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Segmented } from "@/components/ui/forms";
import { Button, Card, CardBody, CardHeader, CardTitle, EmptyState, Progress, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { DIMENSIONS } from "@/lib/format";
import { readLocal, useClientValue, writeLocal } from "@/lib/hooks";
import { useCan, useFindings, useIntegrity, useOverview, useRuntimeOverview, useUsage } from "@/lib/queries";
import type { Overview } from "@/lib/types";
import { pct, timeAgo, titleCase } from "@/lib/utils";

const chartLoading = () => <div className="h-[220px] skeleton rounded-[var(--radius)]" />;
const TrustPostureRadar = dynamic(() => import("@/components/charts/charts").then((m) => m.TrustPostureRadar), { ssr: false, loading: chartLoading });
const RiskTrendChart = dynamic(() => import("@/components/charts/charts").then((m) => m.RiskTrendChart), { ssr: false, loading: chartLoading });
const SeverityBar = dynamic(() => import("@/components/charts/charts").then((m) => m.SeverityBar), { ssr: false, loading: chartLoading });
const RuntimeTimelineChart = dynamic(() => import("@/components/charts/charts").then((m) => m.RuntimeTimelineChart), { ssr: false, loading: chartLoading });

type View = "executive" | "engineer";
const VIEW_KEY = "aegis.overview.view";

export default function OverviewPage() {
  const can = useCan();
  const query = useOverview();
  const stored = useClientValue(() => readLocal(VIEW_KEY), null);
  const [chosen, setChosen] = useState<View | null>(null);
  const view: View = chosen ?? (stored === "engineer" ? "engineer" : "executive");

  return (
    <div>
      <PageHeader
        title="AI Trust Posture"
        description="Where your AI systems stand right now — from completed audits, runtime decisions and the evidence ledger."
        actions={
          <div className="flex items-center gap-2">
            <Segmented<View>
              label="Overview perspective"
              value={view}
              onChange={(v) => {
                setChosen(v);
                writeLocal(VIEW_KEY, v);
              }}
              options={[
                { value: "executive", label: "Executive" },
                { value: "engineer", label: "Engineer" },
              ]}
            />
            {can("audits:run") ? (
              <Link href="/dashboard/audits/new">
                <Button icon={Gauge}>New audit</Button>
              </Link>
            ) : null}
          </div>
        }
      />
      <QueryBoundary query={query}>
        {(data) => (
          <div className="space-y-5">
            <TopMetrics data={data} />
            {data.active_systems === 0 ? <FirstRun /> : view === "executive" ? <ExecutiveView data={data} /> : <EngineerView data={data} />}
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}

function TopMetrics({ data }: { data: Overview }) {
  const can = useCan();
  const runtime = useRuntimeOverview({ hours: 24 }, can("runtime:read"));
  const showRuntime = can("runtime:read") && runtime.data;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <Metric label="Active systems" value={data.active_systems} icon={Boxes} />
      <Metric label="Open findings" value={data.open_findings} icon={ShieldAlert} />
      <Metric label="Critical risks" value={data.critical_risks} icon={TriangleAlert} tone={data.critical_risks ? "var(--color-critical)" : undefined} />
      <Metric label="Policy coverage" value={pct(data.policy_coverage, 1)} icon={ShieldCheck} hint="Share of controls with a test result" />
      <Metric label="Runtime events (24h)" value={showRuntime ? runtime.data!.events.toLocaleString() : "—"} icon={Radar} hint={showRuntime ? `${(runtime.data!.decisions.flag + runtime.data!.decisions.require_approval + runtime.data!.decisions.block).toLocaleString()} matched a policy` : undefined} />
      <Metric label="Pending approvals" value={showRuntime ? runtime.data!.pending_approvals : "—"} icon={Hand} tone={showRuntime && runtime.data!.pending_approvals ? "var(--color-high)" : undefined} />
    </div>
  );
}

function FirstRun() {
  const can = useCan();
  const steps = [
    { title: "Register an AI system", text: "Connect a model provider or endpoint you want to assure.", href: "/dashboard/systems?new=1", show: can("systems:write") },
    { title: "Run a baseline audit", text: "Fairness, safety, privacy, injection and grounding tests with evidence.", href: "/dashboard/audits/new", show: can("audits:run") },
    { title: "Stream runtime events", text: "Instrument an agent with the SDK or MCP server and observe decisions.", href: "/dashboard/developers?tab=runtime", show: true },
    { title: "Publish a runtime policy", text: "Start from the policy library, simulate on real traffic, then publish.", href: "/dashboard/policies?tab=library", show: can("policies:write") },
  ].filter((s) => s.show);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Get started</CardTitle>
      </CardHeader>
      <CardBody>
        <ol className="grid gap-3 md:grid-cols-2">
          {steps.map((s, i) => (
            <li key={s.href}>
              <Link href={s.href} className="flex h-full gap-3 rounded-[var(--radius)] border border-[var(--color-border)] p-4 hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-2)]">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[var(--color-accent-dim)] font-mono text-xs text-[var(--color-accent-bright)]">{i + 1}</span>
                <span>
                  <span className="block text-sm font-medium">{s.title}</span>
                  <span className="block text-sm text-[var(--color-text-muted)]">{s.text}</span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}

function ExecutiveView({ data }: { data: Overview }) {
  const can = useCan();
  return (
    <>
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Risk posture by dimension</CardTitle>
            <span className="text-xs text-[var(--color-text-muted)]">Posture: {titleCase(data.posture)}</span>
          </CardHeader>
          <CardBody className="grid gap-4 sm:grid-cols-[1fr_1.1fr] sm:items-center">
            {Object.keys(data.dimensions).length ? (
              <>
                <TrustPostureRadar current={data.dimensions} previous={data.previous_dimensions} />
                <div className="space-y-2.5">
                  {DIMENSIONS.map((d) => {
                    const v = data.dimensions[d];
                    if (v === undefined) return null;
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
                  <p className="pt-1 text-[11px] text-[var(--color-text-subtle)]">Scores summarize the tests that ran. They are not a certification or a guarantee of behaviour.</p>
                </div>
              </>
            ) : (
              <div className="sm:col-span-2">
                <EmptyState icon={Gauge} title="No completed audits yet" description="Dimension scores appear after the first audit completes." />
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Open findings by severity</CardTitle>
            <Link href="/dashboard/findings?open_only=true" className="text-xs text-[var(--color-accent-bright)] hover:underline">
              View
            </Link>
          </CardHeader>
          <CardBody>{Object.values(data.findings_by_severity).some(Boolean) ? <SeverityBar counts={data.findings_by_severity} /> : <EmptyState title="No open findings" description="Open findings from audits, red-team runs and runtime policies appear here." />}</CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Risk trend (30 days)</CardTitle>
          <span className="text-xs text-[var(--color-text-muted)]">{data.test_volume.toLocaleString()} tests executed</span>
        </CardHeader>
        <CardBody>{data.risk_trend.length > 1 ? <RiskTrendChart data={data.risk_trend} /> : <EmptyState icon={Activity} title="Not enough history yet" description="The trend appears once audits have run on more than one day." />}</CardBody>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {can("evidence:read") ? <IntegrityCard /> : null}
        {can("usage:read") ? <UsageCard /> : null}
      </div>
    </>
  );
}

function EngineerView({ data }: { data: Overview }) {
  const can = useCan();
  const critical = useFindings({ open_only: true, sort: "risk", page_size: 6 });
  const runtime = useRuntimeOverview({ hours: 24 }, can("runtime:read"));
  return (
    <>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Highest-risk open findings</CardTitle>
            <Link href="/dashboard/findings?open_only=true" className="text-xs text-[var(--color-accent-bright)] hover:underline">
              All findings
            </Link>
          </CardHeader>
          <CardBody className="space-y-1">
            <QueryBoundary query={critical} skeleton={<div className="h-40 skeleton" />}>
              {(page) =>
                page.items.length ? (
                  <>
                    {page.items.map((f) => (
                      <Link key={f.id} href={`/dashboard/findings/${f.id}`} className="flex items-center justify-between gap-3 rounded-[var(--radius)] px-2 py-2 hover:bg-[var(--color-surface-2)]">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            #{f.number} {f.title}
                          </p>
                          <p className="text-xs text-[var(--color-text-subtle)]">
                            {titleCase(f.category)} · {titleCase(f.status)} · seen {timeAgo(f.last_seen_at ?? f.created_at)}
                          </p>
                        </div>
                        <SeverityBadge severity={f.severity} />
                      </Link>
                    ))}
                  </>
                ) : (
                  <p className="px-2 py-6 text-sm text-[var(--color-text-subtle)]">No open findings.</p>
                )
              }
            </QueryBoundary>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Runtime decisions (24h)</CardTitle>
            {can("runtime:read") ? (
              <Link href="/dashboard/runtime" className="text-xs text-[var(--color-accent-bright)] hover:underline">
                Runtime Guard
              </Link>
            ) : null}
          </CardHeader>
          <CardBody>
            {!can("runtime:read") ? (
              <p className="text-sm text-[var(--color-text-subtle)]">Your role cannot view runtime events.</p>
            ) : (
              <QueryBoundary query={runtime} skeleton={chartLoading()}>
                {(o) =>
                  o.events ? (
                    <RuntimeTimelineChart data={o.timeline} hourly />
                  ) : (
                    <EmptyState icon={Radar} title="No runtime events in the last 24 hours" description="Instrument an agent to see decisions here." action={<Link href="/dashboard/developers?tab=runtime"><Button size="sm" variant="secondary">Connect an agent</Button></Link>} />
                  )
                }
              </QueryBoundary>
            )}
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent audits</CardTitle>
            <Link href="/dashboard/audits" className="text-xs text-[var(--color-accent-bright)] hover:underline">
              View all
            </Link>
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
            <CardTitle>Recent high-risk findings</CardTitle>
          </CardHeader>
          <CardBody className="space-y-1">
            {data.recent_incidents.length ? (
              data.recent_incidents.map((f) => (
                <Link key={f.id} href={`/dashboard/findings/${f.id}`} className="flex items-center justify-between rounded-[var(--radius)] px-2 py-2 hover:bg-[var(--color-surface-2)]">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      #{f.number} {f.title}
                    </p>
                    <p className="text-xs text-[var(--color-text-subtle)]">{timeAgo(f.created_at)}</p>
                  </div>
                  <RiskBadge level={f.risk_level} />
                </Link>
              ))
            ) : (
              <p className="px-2 py-6 text-sm text-[var(--color-text-subtle)]">None recorded.</p>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}

function IntegrityCard() {
  const integrity = useIntegrity();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileCheck2 className="h-4 w-4" aria-hidden /> Evidence integrity
        </CardTitle>
        <Link href="/dashboard/evidence" className="text-xs text-[var(--color-accent-bright)] hover:underline">
          Details
        </Link>
      </CardHeader>
      <CardBody>
        <QueryBoundary query={integrity} skeleton={<div className="h-20 skeleton" />}>
          {(s) => (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <StatusBadge status={s.status} />
                <span className="text-[var(--color-text-muted)]">
                  {s.audits_verified}/{s.audits_checked} recent audit chains verified · {s.evidence_total.toLocaleString()} records
                </span>
              </div>
              <p className="text-xs text-[var(--color-text-subtle)]">Recomputed {timeAgo(s.checked_at)} from stored artifacts{s.signing_key_development ? " · signing with a development key" : ""}.</p>
            </div>
          )}
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}

function UsageCard() {
  const usage = useUsage();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Plan usage</CardTitle>
        <Link href="/dashboard/billing" className="text-xs text-[var(--color-accent-bright)] hover:underline">
          Usage & billing
        </Link>
      </CardHeader>
      <CardBody>
        <QueryBoundary query={usage} skeleton={<div className="h-20 skeleton" />}>
          {(u) => (
            <div className="space-y-2.5">
              {u.quotas
                .filter((q) => q.limit !== null)
                .slice(0, 4)
                .map((q) => (
                  <div key={q.metric}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="text-[var(--color-text-muted)]">{q.label}</span>
                      <span className="font-mono">
                        {q.used.toLocaleString()} / {q.limit!.toLocaleString()}
                      </span>
                    </div>
                    <Progress value={q.percent ?? 0} color={(q.percent ?? 0) >= 100 ? "var(--color-critical)" : (q.percent ?? 0) >= 80 ? "var(--color-high)" : "var(--color-accent)"} />
                  </div>
                ))}
              <p className="text-xs text-[var(--color-text-subtle)]">{u.plan.name} plan</p>
            </div>
          )}
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}
