"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Building2, Database, Mail, RefreshCw, ShieldAlert, Trophy, Upload, Users } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { AdminHeader, formatDuration, HealthPill } from "@/components/admin/admin-ui";
import type { AdminHealth, AdminStats } from "@/components/admin/types";
import { LineChart } from "@/components/charts/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
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

function Signal({ label, value, tone, href, hint }: { label: string; value: ReactNode; tone: "ok" | "warn" | "bad"; href?: string; hint?: ReactNode }) {
  const color = tone === "ok" ? "text-fg" : tone === "warn" ? "text-warning" : "text-danger";
  const body = (
    <div className="h-full rounded-[var(--radius-lg)] border border-border bg-surface p-4 transition-colors hover:border-border-strong">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className={`mt-2 text-2xl font-semibold tabular-nums ${color}`}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-subtle">{hint}</p> : null}
    </div>
  );
  return href ? <Link href={href} className="block rounded-[var(--radius-lg)]">{body}</Link> : body;
}

function HealthSection({ h, isAdmin }: { h: AdminHealth; isAdmin: boolean }) {
  const byStatus = h.jobs.by_status ?? {};
  const pendingJobs = (byStatus.queued ?? 0) + (byStatus.failed ?? 0);
  const backlog = pendingJobs > 0 && h.jobs.oldest_pending_age_seconds > 600;
  const errRate = h.api.error_rate ?? 0;
  return (
    <>
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <CardBody>
            <p className="flex items-center gap-2 text-xs font-medium text-muted"><Database className="h-4 w-4" aria-hidden />Database</p>
            <div className="mt-3"><HealthPill ok={h.database === "ok"}>{h.database === "ok" ? "Reachable" : "Error"}</HealthPill></div>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <p className="text-xs font-medium text-muted">API (this process, since start)</p>
            <div className="mt-3"><HealthPill ok={errRate < 0.01 ? true : errRate < 0.05 ? "warn" : false}>{(errRate * 100).toFixed(2)}% server errors</HealthPill></div>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
              <div><dt className="text-subtle">p50</dt><dd className="font-medium tabular-nums text-fg">{h.api.latency_ms_p50 ?? "—"} ms</dd></div>
              <div><dt className="text-subtle">p95</dt><dd className="font-medium tabular-nums text-fg">{h.api.latency_ms_p95 ?? "—"} ms</dd></div>
              <div><dt className="text-subtle">Requests</dt><dd className="font-medium tabular-nums text-fg">{formatNumber(h.api.requests)}</dd></div>
            </dl>
            <p className="mt-2 text-xs text-subtle">Uptime {formatDuration(h.api.uptime_seconds)}</p>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <p className="text-xs font-medium text-muted">Worker & jobs</p>
            <div className="mt-3">
              <HealthPill ok={backlog ? "warn" : true}>{backlog ? `Backlog: oldest ${formatDuration(h.jobs.oldest_pending_age_seconds)}` : "Keeping up"}</HealthPill>
            </div>
            <p className="mt-2 text-xs text-muted" title={formatDateTime(h.worker_last_job_finished_at)}>
              Last job finished {h.worker_last_job_finished_at ? relativeTime(h.worker_last_job_finished_at) : "never"}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(byStatus).map(([k, v]) => (
                <Badge key={k} tone={k === "dead" ? "danger" : k === "failed" ? "warning" : "neutral"}>{titleCase(k)} {formatNumber(v)}</Badge>
              ))}
              {h.jobs.dead_last_24h ? <Badge tone="danger">{h.jobs.dead_last_24h} dead in 24h</Badge> : null}
            </div>
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Signal label="Stuck submissions (>30 min)" value={formatNumber(h.submissions.stuck_over_30m)} tone={h.submissions.stuck_over_30m ? "bad" : "ok"} href={isAdmin ? "/admin/jobs" : undefined} hint={`${formatNumber(h.submissions.failed_24h)} failed in 24h`} />
        <Signal label="Server errors (24h)" value={formatNumber(h.errors_24h)} tone={h.errors_24h ? "warn" : "ok"} href={isAdmin ? "/admin/jobs#errors" : undefined} />
        <Signal label="Failed emails (24h)" value={formatNumber(h.emails_failed_24h)} tone={h.emails_failed_24h ? "warn" : "ok"} href={isAdmin ? "/admin/emails?status=failed" : undefined} />
        <Signal label="Open reports" value={formatNumber(h.open_reports)} tone={h.open_reports ? "warn" : "ok"} href="/moderation" />
        <Signal label="Suspicious sign-ins (24h)" value={formatNumber(h.suspicious_auth_events_24h)} tone={h.suspicious_auth_events_24h ? "warn" : "ok"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Rate-limit incidents" description="Buckets that rejected requests since this process started." />
          <CardBody>
            {h.rate_limit_incidents.length ? (
              <Table>
                <THead><tr><TH>Bucket</TH><TH className="text-right">Count</TH><TH>Last</TH></tr></THead>
                <TBody>
                  {h.rate_limit_incidents.slice(0, 12).map((r) => (
                    <TR key={r.bucket}>
                      <TD className="font-mono text-xs">{r.bucket}</TD>
                      <TD className="text-right tabular-nums">{formatNumber(r.count)}</TD>
                      <TD className="text-xs text-muted" title={formatDateTime(r.last_at)}>{relativeTime(r.last_at)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            ) : (
              <p className="py-6 text-center text-sm text-subtle">No rate-limit incidents.</p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Configuration" description="Effective server settings (read-only)." />
          <CardBody>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              {Object.entries(h.config).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-3 border-b border-border py-1.5 last:border-0">
                  <dt className="text-muted">{titleCase(k)}</dt>
                  <dd className="font-medium text-fg">
                    {typeof v === "boolean" ? <Badge tone={v ? "success" : "neutral"}>{v ? "On" : "Off"}</Badge> : <span className="font-mono text-xs">{String(v)}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          </CardBody>
        </Card>
      </div>
    </>
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
        title="Overview"
        description="Platform health and activity. Refreshes every 30 seconds."
        actions={
          <div className="flex items-center gap-3">
            {updated ? <span className="text-xs text-subtle" aria-live="polite">Updated {relativeTime(new Date(updated), now)}</span> : null}
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
      <div className="space-y-6">
        {health.isPending ? (
          <div className="grid gap-3 md:grid-cols-3" role="status" aria-label="Loading health">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-36 w-full" />)}</div>
        ) : health.isError ? (
          <ErrorState error={health.error} onRetry={() => health.refetch()} />
        ) : (
          <HealthSection h={health.data} isAdmin={isAdmin} />
        )}

        <section aria-labelledby="activity-heading" className="space-y-4">
          <h2 id="activity-heading" className="text-base font-semibold text-fg">Activity</h2>
          {stats.isPending ? (
            <div className="grid gap-3 sm:grid-cols-4" role="status" aria-label="Loading stats">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 w-full" />)}</div>
          ) : stats.isError ? (
            <ErrorState error={stats.error} onRetry={() => stats.refetch()} />
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Active users" value={formatNumber(stats.data.users.total)} icon={<Users className="h-4 w-4" />} hint={`+${formatNumber(stats.data.users.new_7d)} this week · ${formatNumber(stats.data.users.demo)} demo`} />
                <Stat label="Competitions" value={formatNumber(stats.data.competitions.total)} icon={<Trophy className="h-4 w-4" />} hint={`${formatNumber(stats.data.competitions.published)} published`} />
                <Stat label="Submissions" value={formatNumber(stats.data.submissions.total)} icon={<Upload className="h-4 w-4" />} hint={`${formatNumber(stats.data.submissions.last_24h)} in the last 24h`} />
                <Stat label="Organizations" value={formatNumber(stats.data.organizations.total)} icon={<Building2 className="h-4 w-4" />} hint={`${formatNumber(stats.data.organizations.verified)} verified`} />
              </div>
              {stats.data.users.demo > 0 ? (
                <p className="flex items-center gap-1.5 text-xs text-subtle"><AlertTriangle className="h-3.5 w-3.5" aria-hidden />Counts include synthetic demo data created by the seed script.</p>
              ) : null}
              <div className="grid gap-6 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Sign-ups per day" description="Last 30 days (UTC)" />
                  <CardBody>
                    <LineChart label="Sign-ups per day, last 30 days" data={fillDays(stats.data.signups_by_day)} yFormat={(v) => formatNumber(v)} xFormat={(x) => formatDate(`${x}T00:00:00Z`, { month: "short", day: "numeric", timeZone: "UTC" })} />
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="Submissions per day" description="Last 30 days (UTC)" />
                  <CardBody>
                    <LineChart label="Submissions per day, last 30 days" data={fillDays(stats.data.submissions_by_day)} yFormat={(v) => formatNumber(v)} xFormat={(x) => formatDate(`${x}T00:00:00Z`, { month: "short", day: "numeric", timeZone: "UTC" })} />
                  </CardBody>
                </Card>
              </div>
            </>
          )}
        </section>

        <div className="flex flex-wrap gap-3 text-sm">
          <Link href="/moderation" className="inline-flex items-center gap-1.5 text-accent-strong hover:underline"><ShieldAlert className="h-4 w-4" aria-hidden />Moderation queue</Link>
          {isAdmin ? <Link href="/admin/emails" className="inline-flex items-center gap-1.5 text-accent-strong hover:underline"><Mail className="h-4 w-4" aria-hidden />Email outbox</Link> : null}
        </div>
      </div>
    </div>
  );
}
