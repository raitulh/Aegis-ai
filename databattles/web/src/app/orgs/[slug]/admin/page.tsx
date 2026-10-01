"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, BadgeCheck, Building2, GraduationCap, ShieldAlert, Trophy, UserPlus, Users } from "lucide-react";
import Link from "next/link";

import { UserLink } from "@/components/domain/cards";
import { BarChart, HBarList } from "@/components/charts/charts";
import { useAdminOrg } from "@/components/orgs/org-admin-context";
import { AdminPageHeader, Metric, MetricGrid } from "@/components/orgs/org-visuals";
import type { OrgAdminDashboard } from "@/components/orgs/types";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { RankBadge } from "@/components/ui/extras";
import { ProgressBar } from "@/components/ui/misc";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState, InlineNotice, QueryState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDate, formatNumber, titleCase } from "@/lib/format";
import { useNow } from "@/lib/hooks";

function monthLabel(m: string) {
  return formatDate(`${m}-01T00:00:00`, { month: "short", year: "2-digit" });
}

/**
 * The API returns only months in which someone joined; months without joins are zero. Fill the
 * trailing 12-month window (extended back if the data starts earlier) so the chart has a time axis.
 */
function monthWindow(data: { month: string; count: number }[], now: Date) {
  const key = (d: Date) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  let start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 11, 1));
  for (const j of data) {
    const [y, m] = j.month.split("-").map(Number);
    if (y && m) {
      const d = new Date(Date.UTC(y, m - 1, 1));
      if (d < start) start = d;
    }
  }
  const counts = new Map(data.map((j) => [j.month, j.count]));
  const out: { month: string; count: number }[] = [];
  for (let d = start; d <= now; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
    out.push({ month: key(d), count: counts.get(key(d)) ?? 0 });
  }
  return out;
}

function pct(part: number, whole: number) {
  return whole ? Math.round((part / whole) * 100) : 0;
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading dashboard">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-surface p-5">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="mt-4 h-7 w-14" />
          </div>
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
        <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
      </div>
      <Skeleton className="h-48 w-full rounded-[var(--radius-lg)]" />
    </div>
  );
}

export default function OrgAdminDashboardPage() {
  const org = useAdminOrg();
  const now = useNow(60 * 60_000);
  const dash = useQuery({
    queryKey: ["orgs", org.slug, "admin-dashboard"],
    queryFn: () => get<OrgAdminDashboard>(`/orgs/${org.slug}/admin/dashboard`),
  });

  return (
    <div className="space-y-6">
      <AdminPageHeader eyebrow="Overview" title="Dashboard" description="Aggregate activity of your active members." />
      <QueryState query={dash} loading={<DashboardSkeleton />}>
        {(d) => (
          <div className="space-y-6">
            {d.members.pending_requests > 0 && org.viewer.can_manage ? (
              <InlineNotice
                tone="warning"
                title={`${formatNumber(d.members.pending_requests)} membership request${d.members.pending_requests === 1 ? "" : "s"} waiting`}
                action={<LinkButton href={`/orgs/${org.slug}/admin/members?status=pending`} size="sm" variant="secondary">Review</LinkButton>}
              />
            ) : null}

            <MetricGrid cols="grid-cols-2 md:grid-cols-3 xl:grid-cols-5">
              <Metric label="Active members" value={formatNumber(d.members.active)} icon={<Users />} />
              <Metric
                label="Verified"
                value={formatNumber(d.members.verified)}
                icon={<BadgeCheck />}
                hint={
                  d.members.active ? (
                    <span className="block">
                      <span className="tabular">{Math.round((d.members.verified / d.members.active) * 100)}% of active</span>
                      <ProgressBar className="mt-2" value={pct(d.members.verified, d.members.active)} label="Verified share of active members" />
                    </span>
                  ) : undefined
                }
              />
              <Metric
                label="Pending requests"
                value={
                  d.members.pending_requests ? (
                    <Link href={`/orgs/${org.slug}/admin/members?status=pending`} className="rounded-sm text-warning hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">
                      {formatNumber(d.members.pending_requests)}
                    </Link>
                  ) : "0"
                }
                icon={<UserPlus />}
                hint={d.members.pending_requests ? "Awaiting review" : "Nothing to review"}
              />
              <Metric
                label="Submitted · 30 days"
                value={formatNumber(d.members.active_last_30d)}
                icon={<Activity />}
                hint={<>Members with ≥1 submission{d.members.active ? <span className="tabular"> · {pct(d.members.active_last_30d, d.members.active)}% of active</span> : null}</>}
              />
              <Metric label="Course completions" value={formatNumber(d.course_completions)} icon={<GraduationCap />} />
            </MetricGrid>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <Card className="min-w-0">
                <CardHeader title="New members by month" description="Active members who joined in the last 12 months." icon={<UserPlus />} />
                <CardBody className="pt-5">
                  <BarChart label="New members by month" data={monthWindow(d.joins_by_month, now).map((j) => ({ label: monthLabel(j.month), value: j.count }))} />
                </CardBody>
              </Card>
              <Card className="min-w-0">
                <CardHeader title="Departments" description="Active members per department." icon={<Building2 />} />
                <CardBody className="pt-5">
                  {d.departments.length ? (
                    <HBarList label="Members per department" data={d.departments.map((p) => ({ label: p.name, value: p.members }))} />
                  ) : (
                    <p className="py-6 text-center text-sm text-subtle">
                      No departments.{" "}
                      {org.viewer.can_manage ? <Link href={`/orgs/${org.slug}/admin/departments`} className="text-accent-strong hover:underline">Add departments</Link> : null}
                    </p>
                  )}
                </CardBody>
              </Card>
            </div>

            <section aria-labelledby="hosted-heading" className="space-y-3">
              <div className="flex items-end justify-between gap-4">
                <div>
                  <h2 id="hosted-heading" className="text-base font-semibold tracking-[-0.015em] text-fg">Hosted competitions</h2>
                  <p className="mt-0.5 text-xs text-muted">Competitions this organization hosts, with member participation.</p>
                </div>
                <LinkButton href="/organize" size="sm" variant="ghost">Organizer tools</LinkButton>
              </div>
              {d.hosted_competitions.length ? (
                <Table className="relative">
                  <THead>
                    <tr>
                      <TH>Competition</TH>
                      <TH>Status</TH>
                      <TH className="text-right">Participants</TH>
                      <TH className="text-right">Members</TH>
                      <TH className="text-right">Teams</TH>
                      <TH className="text-right">Submissions</TH>
                    </tr>
                  </THead>
                  <TBody>
                    {d.hosted_competitions.map((c) => (
                      <TR key={c.slug}>
                        <TD className="min-w-56">
                          <Link href={`/competitions/${c.slug}`} className="font-medium text-fg hover:text-accent-strong">{c.title}</Link>
                          {c.visibility !== "public" ? <Badge tone="outline" className="ml-2">{c.visibility === "university" ? "Members only" : titleCase(c.visibility)}</Badge> : null}
                        </TD>
                        <TD><StatusBadge status={c.status} /></TD>
                        <TD className="tabular text-right">{formatNumber(c.participants)}</TD>
                        <TD className="tabular text-right">
                          {formatNumber(c.member_participants)}
                          {c.participants ? <span className="ml-1.5 text-xs text-subtle">{pct(c.member_participants, c.participants)}%</span> : null}
                        </TD>
                        <TD className="tabular text-right">{formatNumber(c.teams)}</TD>
                        <TD className="tabular text-right">{formatNumber(c.submissions)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              ) : (
                <EmptyState icon={<Trophy />} title="No hosted competitions yet" description="Host one from the organizer tools and pick this organization as the host." action={<LinkButton href="/organize">Host a competition</LinkButton>} />
              )}
            </section>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <Card className="min-w-0">
                <CardHeader title="Where members compete" description="Public competitions with the most of your members." icon={<Trophy />} />
                <CardBody className="pt-5">
                  {d.member_participation.length ? (
                    <HBarList label="Members per competition" data={d.member_participation.map((p) => ({ label: p.title, value: p.members }))} />
                  ) : (
                    <p className="py-6 text-center text-sm text-subtle">No member participation yet.</p>
                  )}
                </CardBody>
              </Card>
              <Card className="min-w-0 overflow-hidden">
                <CardHeader title="Top performers" description="Members with top-10 finishes in public competitions." icon={<BadgeCheck />} />
                <p className="flex items-start gap-2 border-b border-border bg-bg-elevated/40 px-5 py-2.5 text-xs leading-relaxed text-muted">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden /> {d.privacy_note}
                </p>
                {d.top_performers.length ? (
                  <div>
                    <div className="flex items-center gap-3 border-b border-border px-5 py-2 font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle" aria-hidden>
                      <span className="w-12 shrink-0">Best</span>
                      <span className="flex-1">Member</span>
                      <span className="shrink-0 text-right">Top-10 finishes</span>
                    </div>
                    <ul className="divide-y divide-border">
                      {d.top_performers.map((p) => (
                        <li key={p.user.id} className="flex items-center gap-3 px-5 py-3">
                          <span className="flex w-12 shrink-0 items-center" title={p.best_rank ? `Best rank #${p.best_rank}` : "No rank"}>
                            <span aria-hidden className="inline-flex">
                              <RankBadge rank={p.best_rank} size="sm" />
                            </span>
                            <span className="sr-only">Best rank {p.best_rank ? `#${p.best_rank}` : "—"}</span>
                          </span>
                          <UserLink user={p.user} className="min-w-0 flex-1" />
                          <span className="tabular shrink-0 text-right text-sm font-medium text-fg">
                            {formatNumber(p.top10_finishes)}
                            <span className="sr-only"> top-10 finishes</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <CardBody>
                    <p className="py-4 text-center text-sm text-subtle">No top-10 finishes from members who share their university on results yet.</p>
                  </CardBody>
                )}
              </Card>
            </div>
          </div>
        )}
      </QueryState>
    </div>
  );
}
