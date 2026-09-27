"use client";
import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";
import {Check, CircleDot, FileBarChart, Loader2, ShieldAlert, TriangleAlert} from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, Progress, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { TabPanel, TabTrigger, Tabs, TabsList } from "@/components/ui/overlays";
import {streamAudit, type AuditStreamEvent} from "@/lib/api";
import { useAudit, useAuditEvidence, useAuditMatrix, useFindings, useInvalidate } from "@/lib/queries";
import {cn, titleCase} from "@/lib/utils";

const STAGES = ["setup", "test_generation", "inference", "evaluation", "evidence", "policy_mapping", "risk_scoring", "report_generation"];
const TERMINAL = ["completed", "partially_completed", "failed", "cancelled"];

export default function AuditDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [live, setLive] = useState<AuditStreamEvent[]>([]);
  const [streamProgress, setStreamProgress] = useState<{ progress: number; stage?: string | null } | null>(null);
  const query = useAudit(id, false);
  const invalidate = useInvalidate();
  const audit = query.data;
  const running = audit ? !TERMINAL.includes(audit.status) : false;
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!audit || TERMINAL.includes(audit.status)) return;
    const stop = streamAudit(
      id,
      (ev) => {
        setLive((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev].sort((a, b) => a.seq - b.seq)));
        setStreamProgress({ progress: ev.progress, stage: ev.stage });
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      },
      () => {
        query.refetch();
        invalidate("findings");
        invalidate("audits");
      },
    );
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, audit?.status]);

  return (
    <div>
      <QueryBoundary query={query} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
        {(a) => (
          <>
            <PageHeader
              title={a.name}
              breadcrumbs={[{ label: "Audits", href: "/dashboard/audits" }, { label: `#${a.id.slice(0, 8)}` }]}
              actions={
                <div className="flex items-center gap-2">
                  <StatusBadge status={a.status} />
                  {TERMINAL.includes(a.status) ? (
                    <Link href={`/dashboard/reports/${a.id}`}>
                      <Button variant="secondary" size="sm" icon={FileBarChart}>
                        Report
                      </Button>
                    </Link>
                  ) : null}
                </div>
              }
            />
            {running ? <LiveView audit={a} events={live} progress={streamProgress?.progress ?? a.progress} stage={streamProgress?.stage ?? a.stage} scrollRef={scrollRef} /> : <CompletedView auditId={id} audit={a} />}
          </>
        )}
      </QueryBoundary>
    </div>
  );
}

function LiveView({ audit, events, progress, stage, scrollRef }: { audit: any; events: AuditStreamEvent[]; progress: number; stage?: string | null; scrollRef: React.RefObject<HTMLDivElement | null> }) {
  const currentStageIdx = STAGES.indexOf(stage ?? "setup");
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
      <Card>
        <CardBody className="flex flex-col items-center py-10">
          <div className="relative grid h-40 w-40 place-items-center">
            <svg className="absolute inset-0 -rotate-90" viewBox="0 0 100 100">
              <circle cx="50" cy="50" r="44" fill="none" stroke="var(--color-surface-3)" strokeWidth="6" />
              <circle cx="50" cy="50" r="44" fill="none" stroke="var(--color-accent)" strokeWidth="6" strokeLinecap="round" strokeDasharray={`${(progress / 100) * 276} 276`} className="transition-all duration-500" />
            </svg>
            <div className="text-center">
              <p className="font-mono text-3xl font-semibold">{progress}%</p>
              <p className="text-xs text-[var(--color-text-subtle)]">{titleCase(stage ?? "setup")}</p>
            </div>
          </div>
          <div className="mt-6 flex items-center gap-2 text-sm font-medium">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent)]" />
            Auditing {audit.name}…
          </div>
          <div className="mt-6 w-full space-y-1.5">
            {STAGES.map((s, i) => (
              <div key={s} className="flex items-center gap-2 text-xs">
                {i < currentStageIdx ? <Check className="h-3.5 w-3.5 text-[var(--color-success)]" /> : i === currentStageIdx ? <CircleDot className="h-3.5 w-3.5 animate-pulse text-[var(--color-accent)]" /> : <CircleDot className="h-3.5 w-3.5 text-[var(--color-surface-3)]" />}
                <span className={i <= currentStageIdx ? "text-[var(--color-text)]" : "text-[var(--color-text-subtle)]"}>{titleCase(s)}</span>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Live Event Stream</CardTitle>
          <span className="text-xs text-[var(--color-text-subtle)]">{events.length} events</span>
        </CardHeader>
        <div ref={scrollRef} className="max-h-[440px] space-y-1.5 overflow-y-auto p-5 font-mono text-xs">
          {events.length === 0 ? <p className="text-[var(--color-text-subtle)]">Connecting to audit stream…</p> : null}
          {events.map((e) => (
            <div key={e.seq} className="flex items-start gap-2">
              {e.level === "success" ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-success)]" /> : e.level === "warning" ? <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-medium)]" /> : e.level === "error" ? <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-critical)]" /> : <CircleDot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-subtle)]" />}
              <span className={cn(e.level === "warning" && "text-[var(--color-medium)]", e.level === "error" && "text-[var(--color-critical)]", e.level === "info" && "text-[var(--color-text-muted)]")}>{e.message}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function CompletedView({ auditId, audit }: { auditId: string; audit: any }) {
  const [tab, setTab] = useState("overview");
  const matrix = useAuditMatrix(auditId);
  const evidence = useAuditEvidence(auditId);
  const findings = useFindings({ page_size: 100 });
  const auditFindings = findings.data?.items.filter((f) => audit && f.system_id === audit.system_id) ?? [];
  const dims = audit.summary?.dimensions ?? {};

  return (
    <div>
      {audit.status === "partially_completed" && audit.missing_categories?.length ? (
        <div className="mb-4 flex items-start gap-2 rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-medium)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-medium)_8%,transparent)] px-4 py-3 text-sm">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-medium)]" />
          <div>
            <p className="font-medium">Partially completed</p>
            <p className="text-[var(--color-text-muted)]">{audit.missing_categories.map((m: any) => `${titleCase(m.category)}: ${m.reason}`).join(" · ")}</p>
          </div>
        </div>
      ) : null}

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Tests executed" value={audit.test_count} />
        <StatCard label="Findings" value={audit.findings_count} />
        <StatCard label="High risk" value={audit.summary?.high_risk ?? 0} tone={audit.summary?.high_risk ? "var(--color-high)" : undefined} />
        <StatCard label="Evidence" value={audit.evidence_count} />
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabTrigger value="overview">Overview</TabTrigger>
          <TabTrigger value="tests">Tests</TabTrigger>
          <TabTrigger value="findings">Findings</TabTrigger>
          <TabTrigger value="evidence">Evidence</TabTrigger>
        </TabsList>

        <TabPanel value="overview" className="pt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Dimension Scores</CardTitle></CardHeader>
              <CardBody className="space-y-3">
                {Object.keys(dims).length ? Object.entries(dims).map(([k, v]) => (
                  <div key={k}>
                    <div className="mb-1 flex justify-between text-xs"><span className="capitalize text-[var(--color-text-muted)]">{k}</span><span className="font-mono">{Number(v).toFixed(0)}</span></div>
                    <Progress value={Number(v)} color={Number(v) >= 85 ? "var(--color-success)" : Number(v) >= 70 ? "var(--color-medium)" : "var(--color-high)"} />
                  </div>
                )) : <p className="text-sm text-[var(--color-text-subtle)]">No scored dimensions.</p>}
              </CardBody>
            </Card>
            <Card>
              <CardHeader><CardTitle>Reproducibility Manifest</CardTitle></CardHeader>
              <CardBody>
                <dl className="space-y-1.5 text-xs">
                  {Object.entries(audit.manifest ?? {}).filter(([k]) => !["evaluator_versions", "thresholds"].includes(k)).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3"><dt className="text-[var(--color-text-subtle)]">{titleCase(k)}</dt><dd className="truncate font-mono text-[var(--color-text-muted)]">{String(v)}</dd></div>
                  ))}
                </dl>
              </CardBody>
            </Card>
          </div>
        </TabPanel>

        <TabPanel value="tests" className="pt-4">
          <QueryBoundary query={matrix} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
            {(rows) => (
              <DataTable>
                <THead><TR><TH>Category</TH><TH>Test type</TH><TH>Samples</TH><TH>Failures</TH><TH>Confidence</TH><TH>Severity</TH></TR></THead>
                <tbody>
                  {rows.map((r, i) => (
                    <TR key={i}>
                      <TD className="font-medium">{titleCase(r.category)}</TD>
                      <TD className="text-[var(--color-text-muted)]">{titleCase(r.test_type)}</TD>
                      <TD className="font-mono">{r.samples}</TD>
                      <TD className="font-mono"><span style={{ color: r.failures ? "var(--color-high)" : undefined }}>{r.failures}</span></TD>
                      <TD className="font-mono text-[var(--color-text-muted)]">{r.confidence != null ? `${(r.confidence * 100).toFixed(0)}%` : "—"}</TD>
                      <TD>{r.failures ? <SeverityBadge severity={r.severity} /> : <Badge tone="success">Pass</Badge>}</TD>
                    </TR>
                  ))}
                </tbody>
              </DataTable>
            )}
          </QueryBoundary>
        </TabPanel>

        <TabPanel value="findings" className="pt-4">
          {auditFindings.length ? (
            <div className="space-y-2">
              {auditFindings.map((f) => (
                <Link key={f.id} href={`/dashboard/findings/${f.id}`} className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 hover:border-[var(--color-border-strong)]">
                  <div className="flex items-center gap-3">
                    <SeverityBadge severity={f.severity} />
                    <div>
                      <p className="text-sm font-medium">#{f.number} {f.title}</p>
                      <p className="text-xs text-[var(--color-text-subtle)]">{titleCase(f.category)} · {f.occurrences}/{f.sample_size} tests{f.control_ref ? ` · ${f.control_ref}` : ""}</p>
                    </div>
                  </div>
                  <RiskBadge level={f.risk_level} />
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState title="No findings" description="This audit did not surface any findings." />
          )}
        </TabPanel>

        <TabPanel value="evidence" className="pt-4">
          <QueryBoundary query={evidence} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
            {(items) => (
              <DataTable>
                <THead><TR><TH>#</TH><TH>Kind</TH><TH>Title</TH><TH>Confidence</TH><TH>Hash</TH></TR></THead>
                <tbody>
                  {items.map((e) => (
                    <TR key={e.id} onClick={() => (window.location.href = `/dashboard/evidence/${e.id}`)}>
                      <TD className="font-mono text-xs">{e.seq}</TD>
                      <TD><Badge>{titleCase(e.kind)}</Badge></TD>
                      <TD className="font-medium">{e.title}</TD>
                      <TD className="text-[var(--color-text-muted)] capitalize">{e.confidence_level}</TD>
                      <TD className="font-mono text-xs text-[var(--color-text-subtle)]">{e.content_hash.slice(0, 12)}…</TD>
                    </TR>
                  ))}
                </tbody>
              </DataTable>
            )}
          </QueryBoundary>
        </TabPanel>
      </Tabs>
    </div>
  );
}

function StatCard({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <p className="text-xs text-[var(--color-text-muted)]">{label}</p>
      <p className="mt-1 font-mono text-2xl font-semibold" style={{ color: tone }}>{value}</p>
    </div>
  );
}
