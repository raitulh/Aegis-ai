"use client";
import { useQuery } from "@tanstack/react-query";
import { Check, CircleDot, Download, FileBarChart, Flag, Loader2, RotateCcw, ShieldAlert, ShieldCheck, Square, TriangleAlert, Wifi, WifiOff } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { use, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Metric } from "@/components/dashboard/metric";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Hash, KeyValue, PlanLimitNotice } from "@/components/ui/display";
import { TabPanel, TabTrigger, Tabs, TabsList } from "@/components/ui/overlays";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, Progress, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { api, download, errorMessage, path } from "@/lib/api";
import { useAudit, useAuditEvidence, useAuditFindings, useAuditMatrix, useAuditRegression, useAuditVerify, useCan, useInvalidate, useSystem } from "@/lib/queries";
import { useAuditStream, type AuditStreamEvent, type StreamState } from "@/lib/stream";
import type { Audit } from "@/lib/types";
import { cn, formatDateTime, titleCase } from "@/lib/utils";

// Order matches the orchestrator's stage sequence.
const STAGES = ["setup", "test_generation", "inference", "evaluation", "evidence", "risk_scoring", "policy_mapping", "report_generation"];
const TERMINAL = ["completed", "partially_completed", "failed", "cancelled"];
const TABS = ["overview", "findings", "tests", "evidence", "activity"];

export default function AuditDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const can = useCan();
  const invalidate = useInvalidate();
  const query = useAudit(id);
  const audit = query.data;
  const running = audit ? !TERMINAL.includes(audit.status) : false;
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);

  const stream = useAuditStream(id, running, () => {
    void query.refetch();
    invalidate("findings");
    invalidate("audits");
    invalidate("audit", id);
  });

  async function cancel() {
    try {
      await api.post(path`/audits/${id}/cancel`);
      toast.success("Cancellation requested");
      void query.refetch();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function rerun() {
    try {
      const next = await api.post<Audit>(path`/audits/${id}/rerun`, undefined, { "Idempotency-Key": crypto.randomUUID() });
      invalidate("audits");
      router.push(`/dashboard/audits/${next.id}`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function exportPackage() {
    setExporting(true);
    setExportError(null);
    try {
      const headers = await download(path`/audits/${id}/evidence/export`, { method: "POST" });
      toast.success(`Signed package downloaded · root ${headers.get("x-aegis-root-hash")?.slice(0, 12) ?? ""}…`);
      invalidate("evidence");
    } catch (e) {
      setExportError(e);
      toast.error(errorMessage(e, "Export failed"));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <QueryBoundary query={query} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
        {(a) => {
          const done = ["completed", "partially_completed"].includes(a.status);
          return (
            <>
              <PageHeader
                title={a.name}
                breadcrumbs={[{ label: "Audits", href: "/dashboard/audits" }, { label: a.name }]}
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={a.status} />
                    {a.is_demo ? <Badge tone="warning">DEMO</Badge> : null}
                    {running && can("audits:cancel") ? (
                      <Button variant="secondary" size="sm" icon={Square} onClick={() => setConfirmCancel(true)}>
                        Cancel
                      </Button>
                    ) : null}
                    {!running && can("audits:run") ? (
                      <Button variant="secondary" size="sm" icon={RotateCcw} onClick={rerun}>
                        Re-run
                      </Button>
                    ) : null}
                    {done && can("evidence:export") ? (
                      <Button variant="secondary" size="sm" icon={Download} loading={exporting} onClick={exportPackage}>
                        Evidence package
                      </Button>
                    ) : null}
                    {done ? (
                      <Link href={`/dashboard/reports/${a.id}`}>
                        <Button size="sm" icon={FileBarChart}>
                          Report
                        </Button>
                      </Link>
                    ) : null}
                  </div>
                }
              />
              {exportError ? (
                <div className="mb-4">
                  <PlanLimitNotice error={exportError} />
                </div>
              ) : null}
              {running ? (
                <LiveView audit={a} events={stream.events} state={stream.state} />
              ) : a.status === "failed" || a.status === "cancelled" ? (
                <StoppedView audit={a} />
              ) : (
                <CompletedView audit={a} />
              )}
            </>
          );
        }}
      </QueryBoundary>
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Cancel this audit?"
        description="The worker stops at the next checkpoint and discards partial results. Evidence already written stays in the chain. You can re-run the audit later."
        confirmLabel="Cancel audit"
        onConfirm={cancel}
      />
    </div>
  );
}

const STREAM_LABEL: Record<StreamState, string> = { connecting: "Connecting…", live: "Live", reconnecting: "Reconnecting…", polling: "Polling (stream unavailable)", done: "Finished" };

function LiveView({ audit, events, state }: { audit: Audit; events: AuditStreamEvent[]; state: StreamState }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const last = events.at(-1);
  const progress = last?.progress ?? audit.progress;
  const stage = [...events].reverse().find((e) => e.stage)?.stage ?? audit.stage ?? "setup";
  const current = Math.max(0, STAGES.indexOf(stage));

  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
      <Card>
        <CardBody className="flex flex-col items-center py-8">
          <div className="relative grid h-40 w-40 place-items-center" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100} aria-label="Audit progress">
            <svg className="absolute inset-0 -rotate-90" viewBox="0 0 100 100" aria-hidden>
              <circle cx="50" cy="50" r="44" fill="none" stroke="var(--color-surface-3)" strokeWidth="6" />
              <circle cx="50" cy="50" r="44" fill="none" stroke="var(--color-accent)" strokeWidth="6" strokeLinecap="round" strokeDasharray={`${(progress / 100) * 276} 276`} className="transition-all duration-500 motion-reduce:transition-none" />
            </svg>
            <div className="text-center">
              <p className="font-mono text-3xl font-semibold">{progress}%</p>
              <p className="text-xs text-[var(--color-text-subtle)]">{titleCase(stage)}</p>
            </div>
          </div>
          <p className="mt-5 flex items-center gap-2 text-sm font-medium">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent)] motion-reduce:animate-none" aria-hidden />
            {audit.status === "queued" ? "Queued — waiting for a worker" : `Auditing · ${audit.tests_completed}/${audit.test_count || "?"} tests`}
          </p>
          <ol className="mt-6 w-full space-y-1.5" aria-label="Stages">
            {STAGES.map((s, i) => (
              <li key={s} className="flex items-center gap-2 text-xs">
                {i < current ? (
                  <Check className="h-3.5 w-3.5 text-[var(--color-success)]" aria-hidden />
                ) : (
                  <CircleDot className={cn("h-3.5 w-3.5", i === current ? "text-[var(--color-accent)]" : "text-[var(--color-surface-3)]")} aria-hidden />
                )}
                <span className={i <= current ? "text-[var(--color-text)]" : "text-[var(--color-text-subtle)]"}>
                  {titleCase(s)}
                  {i === current ? <span className="sr-only"> (in progress)</span> : null}
                </span>
              </li>
            ))}
          </ol>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Live event stream</CardTitle>
          <span className={cn("inline-flex items-center gap-1.5 text-xs", state === "live" ? "text-[var(--color-success)]" : "text-[var(--color-text-subtle)]")}>
            {state === "live" ? <Wifi className="h-3.5 w-3.5" aria-hidden /> : <WifiOff className="h-3.5 w-3.5" aria-hidden />}
            {STREAM_LABEL[state]} · {events.length} events
          </span>
        </CardHeader>
        <div ref={scrollRef} role="log" aria-live="polite" aria-relevant="additions" className="max-h-[460px] space-y-1.5 overflow-y-auto p-5 font-mono text-xs">
          {events.length === 0 ? <p className="text-[var(--color-text-subtle)]">Waiting for the first event…</p> : null}
          {events.map((e) => (
            <EventLine key={e.seq} e={e} />
          ))}
        </div>
      </Card>
    </div>
  );
}

function EventLine({ e }: { e: Pick<AuditStreamEvent, "level" | "message" | "seq"> & { created_at?: string | null } }) {
  const Icon = e.level === "success" ? Check : e.level === "warning" ? TriangleAlert : e.level === "error" ? ShieldAlert : CircleDot;
  return (
    <div className="flex items-start gap-2">
      <Icon
        className={cn(
          "mt-0.5 h-3.5 w-3.5 shrink-0",
          e.level === "success" ? "text-[var(--color-success)]" : e.level === "warning" ? "text-[var(--color-medium)]" : e.level === "error" ? "text-[var(--color-critical)]" : "text-[var(--color-text-subtle)]",
        )}
        aria-hidden
      />
      <span className={cn(e.level === "warning" && "text-[var(--color-medium)]", e.level === "error" && "text-[var(--color-critical)]", e.level === "info" && "text-[var(--color-text-muted)]")}>{e.message}</span>
    </div>
  );
}

function StoppedView({ audit }: { audit: Audit }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex items-start gap-3">
          <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-high)]" aria-hidden />
          <div className="space-y-1 text-sm">
            <p className="font-medium">{audit.status === "cancelled" ? "This audit was cancelled." : "This audit failed."}</p>
            {audit.error_message ? <p className="text-[var(--color-text-muted)]">{audit.error_message}</p> : null}
            {audit.error_code ? <p className="font-mono text-xs text-[var(--color-text-subtle)]">{audit.error_code}</p> : null}
            <p className="text-[var(--color-text-subtle)]">No findings were recorded from a partial run. Fix the cause (for example the provider connection) and re-run.</p>
          </div>
        </CardBody>
      </Card>
      <ActivityLog auditId={audit.id} />
    </div>
  );
}

function CompletedView({ audit }: { audit: Audit }) {
  const search = useSearchParams();
  const initial = TABS.includes(search.get("tab") ?? "") ? (search.get("tab") as string) : "overview";
  const [tab, setTab] = useState(initial);
  const dims = audit.summary?.dimensions ?? {};

  return (
    <div>
      {audit.status === "partially_completed" && audit.missing_categories?.length ? (
        <div role="status" className="mb-4 flex items-start gap-2 rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-medium)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-medium)_8%,transparent)] px-4 py-3 text-sm">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-medium)]" aria-hidden />
          <div>
            <p className="font-medium">Partially completed — some categories could not be tested</p>
            <p className="text-[var(--color-text-muted)]">{audit.missing_categories.map((m) => `${titleCase(m.category)}: ${m.reason}`).join(" · ")}</p>
          </div>
        </div>
      ) : null}

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Metric label="Tests executed" value={audit.test_count} />
        <Metric label="Findings" value={audit.findings_count} hint={audit.summary?.findings?.new !== undefined ? `${audit.summary.findings.new} first seen here` : undefined} />
        <Metric label="High risk" value={audit.summary?.high_risk ?? 0} tone={audit.summary?.high_risk ? "var(--color-high)" : undefined} />
        <Metric label="Evidence records" value={audit.evidence_count} />
        <Metric label="Posture" value={titleCase(audit.summary?.posture ?? "—")} />
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabTrigger value="overview">Overview</TabTrigger>
          <TabTrigger value="findings">Findings</TabTrigger>
          <TabTrigger value="tests">Tests</TabTrigger>
          <TabTrigger value="evidence">Evidence</TabTrigger>
          <TabTrigger value="activity">Activity</TabTrigger>
        </TabsList>

        <TabPanel value="overview" className="pt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Dimension scores</CardTitle>
              </CardHeader>
              <CardBody className="space-y-3">
                {Object.keys(dims).length ? (
                  Object.entries(dims).map(([k, v]) => (
                    <div key={k}>
                      <div className="mb-1 flex justify-between text-xs">
                        <span className="capitalize text-[var(--color-text-muted)]">{k}</span>
                        <span className="font-mono">{Number(v).toFixed(0)}</span>
                      </div>
                      <Progress value={Number(v)} color={Number(v) >= 85 ? "var(--color-success)" : Number(v) >= 70 ? "var(--color-medium)" : "var(--color-high)"} />
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-[var(--color-text-subtle)]">No scored dimensions for this audit.</p>
                )}
              </CardBody>
            </Card>
            <div className="space-y-4">
              <RegressionCard audit={audit} />
              <Card>
                <CardHeader>
                  <CardTitle>Run details</CardTitle>
                </CardHeader>
                <CardBody>
                  <KeyValue
                    items={[
                      ["Kind", titleCase(audit.kind)],
                      ["Intensity", titleCase(audit.intensity)],
                      ["Categories", audit.categories.map(titleCase).join(", ") || "—"],
                      ["Started", formatDateTime(audit.started_at)],
                      ["Completed", formatDateTime(audit.completed_at)],
                      ["Evidence head", <Hash key="h" value={audit.evidence_head_hash} />],
                    ]}
                  />
                </CardBody>
              </Card>
            </div>
          </div>
        </TabPanel>

        <TabPanel value="findings" className="pt-4">
          {tab === "findings" ? <AuditFindings auditId={audit.id} /> : null}
        </TabPanel>
        <TabPanel value="tests" className="pt-4">
          {tab === "tests" ? <TestMatrix auditId={audit.id} /> : null}
        </TabPanel>
        <TabPanel value="evidence" className="pt-4">
          {tab === "evidence" ? <AuditEvidence auditId={audit.id} /> : null}
        </TabPanel>
        <TabPanel value="activity" className="pt-4">
          {tab === "activity" ? <ActivityLog auditId={audit.id} /> : null}
        </TabPanel>
      </Tabs>
    </div>
  );
}

function RegressionCard({ audit }: { audit: Audit }) {
  const can = useCan();
  const invalidate = useInvalidate();
  const system = useSystem(audit.system_id);
  const regression = useAuditRegression(audit.id);
  const isBaseline = system.data?.baseline_audit_id === audit.id;

  async function setBaseline() {
    try {
      await api.put(path`/systems/${audit.system_id}/baseline`, { audit_id: audit.id });
      toast.success("Set as the known-good baseline for this system");
      invalidate("system", audit.system_id);
      invalidate("audit", audit.id);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Regression check</CardTitle>
        {isBaseline ? (
          <Badge tone="success">
            <Flag className="h-3 w-3" aria-hidden /> Baseline
          </Badge>
        ) : can("assurance:manage") ? (
          <Button variant="ghost" size="sm" icon={Flag} onClick={setBaseline}>
            Mark as baseline
          </Button>
        ) : null}
      </CardHeader>
      <CardBody>
        <QueryBoundary query={regression} skeleton={<div className="h-20 skeleton" />}>
          {(r) => {
            const entries = Object.entries(r.comparisons);
            if (!entries.length) return <p className="text-sm text-[var(--color-text-subtle)]">No earlier completed audit or baseline to compare with yet.</p>;
            return (
              <div className="space-y-3">
                <p className={cn("text-sm font-medium", r.regression ? "text-[var(--color-high)]" : "text-[var(--color-success)]")}>
                  {r.regression ? "Regression detected" : "No regression detected"}
                </p>
                {entries.map(([label, c]) => (
                  <div key={label} className="rounded-[var(--radius)] border border-[var(--color-border)] p-3 text-xs">
                    <p className="mb-1.5 font-medium">
                      vs {label} ·{" "}
                      <Link className="text-[var(--color-accent-bright)] hover:underline" href={`/dashboard/audits/${c.audit_id}`}>
                        open
                      </Link>
                    </p>
                    <p className="text-[var(--color-text-muted)]">
                      {c.new_findings} new · {c.resolved_findings} resolved · {c.severity_regressions} severity increases
                    </p>
                    {Object.keys(c.score_drops).length ? (
                      <p className="mt-1 text-[var(--color-high)]">
                        Score drops: {Object.entries(c.score_drops).map(([d, v]) => `${d} −${v}`).join(", ")}
                      </p>
                    ) : null}
                    {c.severe_new_findings.length ? (
                      <ul className="mt-1 space-y-0.5">
                        {c.severe_new_findings.slice(0, 5).map((f) => (
                          <li key={f.id}>
                            <Link className="hover:underline" href={`/dashboard/findings/${f.id}`}>
                              #{f.number} {f.title}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))}
              </div>
            );
          }}
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}

function AuditFindings({ auditId }: { auditId: string }) {
  const query = useAuditFindings(auditId);
  return (
    <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
      {(items) =>
        items.length === 0 ? (
          <EmptyState icon={ShieldCheck} title="No findings in this audit" description="No test in this run crossed a finding threshold. That is a result for these tests only, not a guarantee." />
        ) : (
          <DataTable caption="Findings observed in this audit">
            <THead>
              <tr>
                <TH>Finding</TH>
                <TH>Observed severity</TH>
                <TH>Risk</TH>
                <TH>Failures</TH>
                <TH>Current status</TH>
              </tr>
            </THead>
            <tbody>
              {items.map((f) => (
                <TR key={f.id} href={`/dashboard/findings/${f.id}`}>
                  <TD className="max-w-md">
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-xs text-[var(--color-text-subtle)]">#{f.number}</span>
                      <RowLink href={`/dashboard/findings/${f.id}`}>{f.title}</RowLink>
                      {f.first_detected_here ? <Badge tone="accent">New here</Badge> : null}
                    </div>
                    <p className="text-xs text-[var(--color-text-subtle)]">
                      {titleCase(f.category)}
                      {f.control_ref ? ` · ${f.control_ref}` : ""}
                    </p>
                  </TD>
                  <TD>
                    <SeverityBadge severity={f.observed_severity} />
                  </TD>
                  <TD>
                    <RiskBadge level={f.observed_risk_level ?? f.risk_level} />
                  </TD>
                  <TD className="font-mono text-xs">
                    {f.observed_occurrences}/{f.observed_sample_size}
                  </TD>
                  <TD>
                    <StatusBadge status={f.status} />
                  </TD>
                </TR>
              ))}
            </tbody>
          </DataTable>
        )
      }
    </QueryBoundary>
  );
}

function TestMatrix({ auditId }: { auditId: string }) {
  const matrix = useAuditMatrix(auditId);
  return (
    <QueryBoundary query={matrix} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
      {(rows) =>
        rows.length === 0 ? (
          <EmptyState title="No test results" description="This audit has no recorded test results." />
        ) : (
          <DataTable caption="Test matrix">
            <THead>
              <tr>
                <TH>Category</TH>
                <TH>Test type</TH>
                <TH>Samples</TH>
                <TH>Failures</TH>
                <TH>Errors</TH>
                <TH>Confidence</TH>
                <TH>Result</TH>
              </tr>
            </THead>
            <tbody>
              {rows.map((r, i) => (
                <TR key={`${r.category}-${r.test_type}-${i}`}>
                  <TD className="font-medium">{titleCase(r.category)}</TD>
                  <TD className="text-[var(--color-text-muted)]">{titleCase(r.test_type)}</TD>
                  <TD className="font-mono">{r.samples}</TD>
                  <TD className="font-mono">
                    <span style={{ color: r.failures ? "var(--color-high)" : undefined }}>{r.failures}</span>
                  </TD>
                  <TD className="font-mono text-[var(--color-text-muted)]">{r.errors}</TD>
                  <TD className="font-mono text-[var(--color-text-muted)]">{r.confidence != null ? `${(r.confidence * 100).toFixed(0)}%` : "—"}</TD>
                  <TD>{r.failures ? <SeverityBadge severity={r.severity} /> : r.errors ? <StatusBadge status="error" /> : <Badge tone="success">Pass</Badge>}</TD>
                </TR>
              ))}
            </tbody>
          </DataTable>
        )
      }
    </QueryBoundary>
  );
}

function AuditEvidence({ auditId }: { auditId: string }) {
  const verify = useAuditVerify(auditId);
  const evidence = useAuditEvidence(auditId);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Chain verification</CardTitle>
          <Button variant="ghost" size="sm" icon={RotateCcw} onClick={() => verify.refetch()} loading={verify.isFetching}>
            Re-verify
          </Button>
        </CardHeader>
        <CardBody>
          <QueryBoundary query={verify} skeleton={<div className="h-16 skeleton" />}>
            {(v) => (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <StatusBadge status={v.status} />
                  <span className="text-[var(--color-text-muted)]">
                    {v.records} records · {v.content_checked} content hashes recomputed{v.purged ? ` · ${v.purged} purged by retention` : ""}
                  </span>
                </div>
                <KeyValue
                  items={[
                    ["Computed head", <Hash key="c" value={v.head} length={16} />],
                    ["Recorded head", <Hash key="r" value={v.recorded_head} length={16} />],
                    ["Checked", formatDateTime(v.checked_at)],
                  ]}
                />
                {v.problems.length ? (
                  <ul className="space-y-1 text-xs text-[var(--color-critical)]">
                    {v.problems.slice(0, 20).map((p, i) => (
                      <li key={i}>
                        {p.index !== undefined ? `#${p.index} ` : ""}
                        <span className="font-mono">{p.check}</span>: {p.detail}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            )}
          </QueryBoundary>
        </CardBody>
      </Card>
      <QueryBoundary query={evidence} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No evidence records" description="This audit did not capture evidence." />
          ) : (
            <DataTable caption="Evidence captured by this audit">
              <THead>
                <tr>
                  <TH>Title</TH>
                  <TH>Seq</TH>
                  <TH>Kind</TH>
                  <TH>Confidence</TH>
                  <TH>Content hash</TH>
                </tr>
              </THead>
              <tbody>
                {items.map((e) => (
                  <TR key={e.id} href={`/dashboard/evidence/${e.id}`}>
                    <TD className="max-w-md">
                      <span className="line-clamp-1">
                        <RowLink href={`/dashboard/evidence/${e.id}`}>{e.title}</RowLink>
                      </span>
                    </TD>
                    <TD className="font-mono text-xs">{e.seq}</TD>
                    <TD>
                      <Badge>{titleCase(e.kind)}</Badge>
                    </TD>
                    <TD className="capitalize text-[var(--color-text-muted)]">{e.confidence_level}</TD>
                    <TD className="font-mono text-xs text-[var(--color-text-subtle)]">{e.content_hash.slice(0, 12)}…</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
    </div>
  );
}

function ActivityLog({ auditId }: { auditId: string }) {
  const query = useQuery({ queryKey: ["audit", auditId, "events"], queryFn: () => api.get<AuditStreamEvent[]>(path`/audits/${auditId}/events`, { after: 0 }) });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Activity</CardTitle>
      </CardHeader>
      <CardBody>
        <QueryBoundary query={query} skeleton={<div className="h-32 skeleton" />}>
          {(events) =>
            events.length === 0 ? (
              <p className="text-sm text-[var(--color-text-subtle)]">No events recorded.</p>
            ) : (
              <div className="max-h-[480px] space-y-1.5 overflow-y-auto font-mono text-xs">
                {events.map((e) => (
                  <EventLine key={e.seq} e={e} />
                ))}
              </div>
            )
          }
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}
