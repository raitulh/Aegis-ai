"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Building2,
  Cpu,
  Database,
  Gauge,
  KeyRound,
  Mail,
  RefreshCw,
  Server,
  Settings2,
  ShieldAlert,
  Trophy,
  Upload,
  Users,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { AdminHeader, formatDuration, HealthPill, PanelHeader, SectionHeading, TileGrid } from "@/components/admin/admin-ui";
import type { AdminHealth, AdminStats } from "@/components/admin/types";
import { HBarList, LineChart } from "@/components/charts/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, Stat } from "@/components/ui/card";
import { ErrorState, Skeleton, SkeletonStats } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { useMe, useNow } from "@/lib/hooks";

const REFRESH_MS = 30_000;

/** The API returns only days with activity; fill the gaps with zeros for an honest chart. */
function fillDays(rows: { day: string; count: number }[], days = 30) {
  const map = new Map(rows.map((r) => [r.day, r.count]));
  const out: { x: string; y: number }[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    out.push({ x: key, y: map.get(key) ?? 0 });
  }
  return out;
}

const SIGNAL_STATE = {
  ok: { label: "Clear", text: "text-fg", dot: "bg-success" },
  warn: { label: "Review", text: "text-warning", dot: "bg-warning" },
  bad: { label: "Action needed", text: "text-danger", dot: "bg-danger" },
} as const;

function Signal({ label, value, tone, href, hint }: { label: string; value: ReactNode; tone: "ok" | "warn" | "bad"; href?: string; hint?: ReactNode }) {
  const s = SIGNAL_STATE[tone];
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-muted">{label}</p>
        {href ? <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-subtle transition-[color,transform] duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-strong" aria-hidden /> : null}
      </div>
      <div className="mt-auto pt-3">
        <p className={cn("tabular text-[1.65rem] font-semibold leading-none tracking-[-0.03em]", s.text)}>{value}</p>
        <p className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
          <span className="inline-flex items-center gap-1.5">
            <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} aria-hidden />
            {s.label}
          </span>
          {hint ? <span>· {hint}</span> : null}
        </p>
      </div>
    </>
  );
  const cls = "group relative flex h-full flex-col bg-surface p-4 transition-colors duration-200";
  return href ? (
    <Link href={href} className={cn(cls, "hover:bg-surface-2/80 focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]")}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

/** One system component: icon, name, a status pill and supporting facts. */
function SystemPanel({ icon, name, status, children }: { icon: ReactNode; name: string; status: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex h-full flex-col bg-surface p-5">
      <p className="flex items-center gap-2 text-eyebrow text-subtle">
        <span className="text-accent-strong [&_svg]:h-3.5 [&_svg]:w-3.5" aria-hidden>{icon}</span>
        {name}
      </p>
      <div className="mt-3">{status}</div>
      {children ? <div className="mt-4 flex-1 text-xs">{children}</div> : null}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-subtle">{label}</dt>
      <dd className="tabular mt-0.5 truncate text-[13px] font-medium text-fg">{value}</dd>
    </div>
  );
}

function healthSummary(h: AdminHealth) {
  const byStatus = h.jobs.by_status ?? {};
  const pendingJobs = (byStatus.queued ?? 0) + (byStatus.failed ?? 0);
  const backlog = pendingJobs > 0 && h.jobs.oldest_pending_age_seconds > 600;
  const errRate = h.api.error_rate ?? 0;
  return { byStatus, backlog, errRate };
}

function HealthSection({ h, isAdmin }: { h: AdminHealth; isAdmin: boolean }) {
  const { byStatus, backlog, errRate } = healthSummary(h);
  return (
    <div className="space-y-4">
      <TileGrid className="md:grid-cols-3" label="System components">
        <SystemPanel icon={<Database />} name="Database" status={<HealthPill ok={h.database === "ok"}>{h.database === "ok" ? "Reachable" : "Error"}</HealthPill>}>
          <p className="leading-relaxed text-muted">
            {h.database === "ok" ? "The API reached the database on the last check." : "The API could not reach the database on the last check."}
          </p>
        </SystemPanel>
        <SystemPanel
          icon={<Server />}
          name="API · this process"
          status={<HealthPill ok={errRate < 0.01 ? true : errRate < 0.05 ? "warn" : false}>{(errRate * 100).toFixed(2)}% server errors</HealthPill>}
        >
          <dl className="grid grid-cols-3 gap-3">
            <Fact label="p50" value={`${h.api.latency_ms_p50 ?? "—"} ms`} />
            <Fact label="p95" value={`${h.api.latency_ms_p95 ?? "—"} ms`} />
            <Fact label="p99" value={`${h.api.latency_ms_p99 ?? "—"} ms`} />
            <Fact label="Requests" value={formatNumber(h.api.requests)} />
            <Fact label="5xx" value={formatNumber(h.api.server_errors)} />
            <Fact label="Uptime" value={formatDuration(h.api.uptime_seconds)} />
          </dl>
        </SystemPanel>
        <SystemPanel
          icon={<Cpu />}
          name="Worker & jobs"
          status={<HealthPill ok={backlog ? "warn" : true}>{backlog ? `Backlog: oldest ${formatDuration(h.jobs.oldest_pending_age_seconds)}` : "Keeping up"}</HealthPill>}
        >
          <p className="text-muted" title={formatDateTime(h.worker_last_job_finished_at)}>
            Last job finished {h.worker_last_job_finished_at ? relativeTime(h.worker_last_job_finished_at) : "never"}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(byStatus).map(([k, v]) => (
              <Badge key={k} tone={k === "dead" ? "danger" : k === "failed" ? "warning" : "neutral"}>
                {titleCase(k)} <span className="tabular">{formatNumber(v)}</span>
              </Badge>
            ))}
            {h.jobs.dead_last_24h ? <Badge tone="danger">{h.jobs.dead_last_24h} dead in 24h</Badge> : null}
          </div>
        </SystemPanel>
      </TileGrid>

      <TileGrid className="grid-cols-2 lg:grid-cols-5" label="Signals">
        <Signal label="Stuck submissions (>30 min)" value={formatNumber(h.submissions.stuck_over_30m)} tone={h.submissions.stuck_over_30m ? "bad" : "ok"} href={isAdmin ? "/admin/jobs" : undefined} hint={`${formatNumber(h.submissions.failed_24h)} failed in 24h`} />
        <Signal label="Server errors (24h)" value={formatNumber(h.errors_24h)} tone={h.errors_24h ? "warn" : "ok"} href={isAdmin ? "/admin/jobs#errors" : undefined} />
        <Signal label="Failed emails (24h)" value={formatNumber(h.emails_failed_24h)} tone={h.emails_failed_24h ? "warn" : "ok"} href={isAdmin ? "/admin/emails?status=failed" : undefined} />
        <Signal label="Open reports" value={formatNumber(h.open_reports)} tone={h.open_reports ? "warn" : "ok"} href="/moderation" />
        <div className="col-span-2 lg:col-span-1">
          <Signal label="Suspicious sign-ins (24h)" value={formatNumber(h.suspicious_auth_events_24h)} tone={h.suspicious_auth_events_24h ? "warn" : "ok"} />
        </div>
      </TileGrid>
    </div>
  );
}

function QueuesSection({ h }: { h: AdminHealth }) {
  const { byStatus } = healthSummary(h);
  const jobRows = Object.entries(byStatus).map(([k, v]) => ({ label: titleCase(k), value: v }));
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Card>
        <PanelHeader icon={<Gauge />} title="Job queue" description="Background jobs by status, from the worker queue." />
        <CardBody>
          {jobRows.length ? <HBarList label="Jobs by status" data={jobRows} /> : <p className="py-6 text-center text-sm text-subtle">No jobs recorded yet.</p>}
          <dl className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border text-xs">
            <div className="bg-bg-elevated px-3 py-2.5">
              <dt className="text-subtle">Oldest pending</dt>
              <dd className="tabular mt-0.5 text-sm font-medium text-fg">{formatDuration(h.jobs.oldest_pending_age_seconds)}</dd>
            </div>
            <div className="bg-bg-elevated px-3 py-2.5">
              <dt className="text-subtle">Dead in 24h</dt>
              <dd className={cn("tabular mt-0.5 text-sm font-medium", h.jobs.dead_last_24h ? "text-danger" : "text-fg")}>{formatNumber(h.jobs.dead_last_24h)}</dd>
            </div>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <PanelHeader icon={<KeyRound />} title="Rate-limit incidents" description="Buckets that rejected requests since this process started." />
        <CardBody>
          {h.rate_limit_incidents.length ? (
            <Table>
              <THead><tr><TH>Bucket</TH><TH className="text-right">Count</TH><TH>Last</TH></tr></THead>
              <TBody>
                {h.rate_limit_incidents.slice(0, 12).map((r) => (
                  <TR key={r.bucket}>
                    <TD className="font-mono text-xs">{r.bucket}</TD>
                    <TD className="tabular text-right">{formatNumber(r.count)}</TD>
                    <TD className="whitespace-nowrap text-xs text-muted" title={formatDateTime(r.last_at)}>{relativeTime(r.last_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : (
            <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
              <span className="flex h-9 w-9 items-center justify-center rounded-full border border-border bg-success-soft text-success" aria-hidden>
                <KeyRound className="h-4 w-4" />
              </span>
              <p className="text-sm text-muted">No rate-limit incidents.</p>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function ConfigSection({ h }: { h: AdminHealth }) {
  return (
    <Card>
      <PanelHeader icon={<Settings2 />} title="Configuration" description="Effective server settings (read-only)." />
      <CardBody className="py-2">
        <dl className="grid grid-cols-1 gap-x-8 text-sm sm:grid-cols-2 xl:grid-cols-3">
          {Object.entries(h.config).map(([k, v]) => (
            <div key={k} className="flex min-w-0 items-center justify-between gap-3 border-b border-border py-2.5">
              <dt className="truncate text-muted">{titleCase(k)}</dt>
              <dd className="shrink-0 font-medium text-fg">
                {typeof v === "boolean" ? <Badge tone={v ? "success" : "neutral"}>{v ? "On" : "Off"}</Badge> : <span className="font-mono text-xs">{String(v)}</span>}
              </dd>
            </div>
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}

function ChartCard({ title, label, rows }: { title: string; label: string; rows: { day: string; count: number }[] }) {
  const data = fillDays(rows);
  const total = data.reduce((a, d) => a + d.y, 0);
  return (
    <Card>
      <PanelHeader
        title={title}
        description="Last 30 days (UTC)"
        action={<span className="tabular text-right text-xs text-subtle"><span className="block text-base font-semibold text-fg">{formatNumber(total)}</span>in 30 days</span>}
      />
      <CardBody>
        <LineChart label={label} data={data} yFormat={(v) => formatNumber(v)} xFormat={(x) => formatDate(`${x}T00:00:00Z`, { month: "short", day: "numeric", timeZone: "UTC" })} />
      </CardBody>
    </Card>
  );
}

export default function AdminOverviewPage() {
  const me = useMe().data;
  const isAdmin = Boolean(me?.platform_roles.includes("platform_admin"));
  const now = useNow(10_000);
  const health = useQuery({ queryKey: ["admin", "health"], queryFn: () => get<AdminHealth>("/admin/health"), refetchInterval: REFRESH_MS });
  const stats = useQuery({ queryKey: ["admin", "stats"], queryFn: () => get<AdminStats>("/admin/stats"), refetchInterval: REFRESH_MS });
  const updated = Math.max(health.dataUpdatedAt, stats.dataUpdatedAt);

  return (
    <div>
      <AdminHeader
        eyebrow="Monitor"
        icon={<Activity />}
        title="Overview"
        description="Platform health and activity. Refreshes every 30 seconds."
        actions={
          <div className="flex items-center gap-3">
            {updated ? <span className="tabular text-xs text-subtle" aria-live="polite">Updated {relativeTime(new Date(updated), now)}</span> : null}
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshCw className={`h-4 w-4 ${health.isFetching || stats.isFetching ? "animate-spin" : ""}`} />}
              onClick={() => { void health.refetch(); void stats.refetch(); }}
            >
              Refresh
            </Button>
          </div>
        }
      />
      <div className="space-y-10">
        <section aria-labelledby="health-heading">
          <SectionHeading id="health-heading" eyebrow="System" title="Health" description="Live checks from the API process, worker queue and recent activity." />
          {health.isPending ? (
            <div className="space-y-4" role="status" aria-label="Loading health">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-44 w-full rounded-[var(--radius-lg)]" />)}</div>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-28 w-full rounded-[var(--radius-lg)]" />)}</div>
            </div>
          ) : health.isError ? (
            <ErrorState error={health.error} onRetry={() => health.refetch()} />
          ) : (
            <HealthSection h={health.data} isAdmin={isAdmin} />
          )}
        </section>

        <section aria-labelledby="activity-heading">
          <SectionHeading id="activity-heading" eyebrow="Usage" title="Activity" description="Totals across the platform, with daily trends." />
          {stats.isPending ? (
            <div className="space-y-4">
              <SkeletonStats count={4} />
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" role="status" aria-label="Loading stats">{[0, 1].map((i) => <Skeleton key={i} className="h-64 w-full rounded-[var(--radius-lg)]" />)}</div>
            </div>
          ) : stats.isError ? (
            <ErrorState error={stats.error} onRetry={() => stats.refetch()} />
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 xl:grid-cols-4">
                <Stat label="Active users" value={formatNumber(stats.data.users.total)} icon={<Users className="h-4 w-4" />} hint={`+${formatNumber(stats.data.users.new_7d)} this week · ${formatNumber(stats.data.users.demo)} demo`} />
                <Stat label="Competitions" accent="cyan" value={formatNumber(stats.data.competitions.total)} icon={<Trophy className="h-4 w-4" />} hint={`${formatNumber(stats.data.competitions.published)} published`} />
                <Stat label="Submissions" accent="info" value={formatNumber(stats.data.submissions.total)} icon={<Upload className="h-4 w-4" />} hint={`${formatNumber(stats.data.submissions.last_24h)} in the last 24h`} />
                <Stat label="Organizations" accent="success" value={formatNumber(stats.data.organizations.total)} icon={<Building2 className="h-4 w-4" />} hint={`${formatNumber(stats.data.organizations.verified)} verified`} />
              </div>
              {stats.data.users.demo > 0 ? (
                <p className="flex items-center gap-1.5 px-1 text-xs text-subtle"><AlertTriangle className="h-3.5 w-3.5 text-warning" aria-hidden />Counts include synthetic demo data created by the seed script.</p>
              ) : null}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <ChartCard title="Sign-ups per day" label="Sign-ups per day, last 30 days" rows={stats.data.signups_by_day} />
                <ChartCard title="Submissions per day" label="Submissions per day, last 30 days" rows={stats.data.submissions_by_day} />
              </div>
            </div>
          )}
        </section>

        {health.data ? (
          <section aria-labelledby="queues-heading">
            <SectionHeading id="queues-heading" eyebrow="Throughput" title="Queues & limits" />
            <div className="space-y-4">
              <QueuesSection h={health.data} />
              <ConfigSection h={health.data} />
            </div>
          </section>
        ) : null}

        <nav aria-label="Shortcuts" className="flex flex-wrap gap-2 border-t border-border pt-6">
          <Link href="/moderation" className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 text-[13px] font-medium text-muted transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"><ShieldAlert className="h-4 w-4 text-warning" aria-hidden />Moderation queue</Link>
          {isAdmin ? <Link href="/admin/emails" className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 text-[13px] font-medium text-muted transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"><Mail className="h-4 w-4 text-accent-strong" aria-hidden />Email outbox</Link> : null}
        </nav>
      </div>
    </div>
  );
}
