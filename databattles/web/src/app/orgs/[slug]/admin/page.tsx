"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, BadgeCheck, GraduationCap, ShieldAlert, Trophy, UserPlus, Users } from "lucide-react";
import Link from "next/link";

import { UserLink } from "@/components/domain/cards";
import { BarChart, HBarList } from "@/components/charts/charts";
import { useAdminOrg } from "@/components/orgs/org-admin-context";
import type { OrgAdminDashboard } from "@/components/orgs/types";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState, InlineNotice, QueryState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDate, formatNumber, titleCase } from "@/lib/format";

function monthLabel(m: string) {
  return formatDate(`${m}-01T00:00:00`, { month: "short", year: "2-digit" });
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading dashboard">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
      </div>
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

export default function OrgAdminDashboardPage() {
  const org = useAdminOrg();
  const dash = useQuery({
    queryKey: ["orgs", org.slug, "admin-dashboard"],
    queryFn: () => get<OrgAdminDashboard>(`/orgs/${org.slug}/admin/dashboard`),
  });

  return (
    <div>
      <h1 className="text-xl font-semibold text-fg">Dashboard</h1>
      <p className="mt-1 text-sm text-muted">Aggregate activity of your active members.</p>
      <div className="mt-6">
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

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <Stat label="Active members" value={formatNumber(d.members.active)} icon={<Users className="h-4 w-4" />} />
                <Stat
                  label="Verified"
                  value={formatNumber(d.members.verified)}
                  icon={<BadgeCheck className="h-4 w-4" />}
                  hint={d.members.active ? `${Math.round((d.members.verified / d.members.active) * 100)}% of active` : undefined}
                />
                <Stat
                  label="Pending requests"
                  value={
                    d.members.pending_requests ? (
                      <Link href={`/orgs/${org.slug}/admin/members?status=pending`} className="hover:text-accent-strong">{formatNumber(d.members.pending_requests)}</Link>
                    ) : "0"
                  }
                  icon={<UserPlus className="h-4 w-4" />}
                />
                <Stat label="Submitted in last 30 days" value={formatNumber(d.members.active_last_30d)} icon={<Activity className="h-4 w-4" />} hint="Members with ≥1 submission" />
                <Stat label="Course completions" value={formatNumber(d.course_completions)} icon={<GraduationCap className="h-4 w-4" />} />
              </div>

              <Card>
                <CardHeader title="New members by month" description="Active members who joined in the last 12 months." />
                <CardBody>
                  <BarChart label="New members by month" data={d.joins_by_month.map((j) => ({ label: monthLabel(j.month), value: j.count }))} />
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="Hosted competitions"
                  action={<LinkButton href="/organize" size="sm" variant="ghost">Organizer tools</LinkButton>}
                />
                {d.hosted_competitions.length ? (
                  <div className="p-4">
                    <Table>
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
                            <TD>
                              <Link href={`/competitions/${c.slug}`} className="font-medium text-fg hover:text-accent-strong">{c.title}</Link>
                              {c.visibility !== "public" ? <Badge tone="outline" className="ml-2">{c.visibility === "university" ? "Members only" : titleCase(c.visibility)}</Badge> : null}
                            </TD>
                            <TD><StatusBadge status={c.status} /></TD>
                            <TD className="text-right tabular-nums">{formatNumber(c.participants)}</TD>
                            <TD className="text-right tabular-nums">{formatNumber(c.member_participants)}</TD>
                            <TD className="text-right tabular-nums">{formatNumber(c.teams)}</TD>
                            <TD className="text-right tabular-nums">{formatNumber(c.submissions)}</TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  </div>
                ) : (
                  <CardBody>
                    <EmptyState icon={<Trophy className="h-5 w-5" />} title="No hosted competitions yet" description="Host one from the organizer tools and pick this organization as the host." action={<LinkButton href="/organize">Host a competition</LinkButton>} />
                  </CardBody>
                )}
              </Card>

              <div className="grid gap-6 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Where members compete" description="Public competitions with the most of your members." />
                  <CardBody>
                    {d.member_participation.length ? (
                      <HBarList label="Members per competition" data={d.member_participation.map((p) => ({ label: p.title, value: p.members }))} />
                    ) : (
                      <p className="py-6 text-center text-sm text-subtle">No member participation yet.</p>
                    )}
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="Departments" description="Active members per department." />
                  <CardBody>
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

              <Card>
                <CardHeader title="Top performers" description="Members with top-10 finishes in public competitions." />
                <CardBody className="space-y-4">
                  <p className="flex items-start gap-2 text-xs text-muted">
                    <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> {d.privacy_note}
                  </p>
                  {d.top_performers.length ? (
                    <Table>
                      <THead>
                        <tr>
                          <TH>Member</TH>
                          <TH className="text-right">Top-10 finishes</TH>
                          <TH className="text-right">Best rank</TH>
                        </tr>
                      </THead>
                      <TBody>
                        {d.top_performers.map((p) => (
                          <TR key={p.user.id}>
                            <TD><UserLink user={p.user} /></TD>
                            <TD className="text-right tabular-nums">{formatNumber(p.top10_finishes)}</TD>
                            <TD className="text-right tabular-nums">{p.best_rank ? `#${p.best_rank}` : "—"}</TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  ) : (
                    <p className="py-4 text-center text-sm text-subtle">No top-10 finishes from members who share their university on results yet.</p>
                  )}
                </CardBody>
              </Card>
            </div>
          )}
        </QueryState>
      </div>
    </div>
  );
}
