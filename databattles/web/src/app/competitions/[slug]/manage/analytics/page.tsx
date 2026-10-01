"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3, Eye, ShieldCheck, Upload, Users, UsersRound } from "lucide-react";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";

import { BarChart, HBarList, Histogram, LineChart } from "@/components/charts/charts";
import { ManageHeading } from "@/components/organizer/shared";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { EmptyState, QueryState, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDate, formatNumber, titleCase } from "@/lib/format";

interface Analytics {
  competition: { slug: string; title: string; metric: string | null; direction: string };
  funnel: { views: number; participants: number; members_who_submitted: number; teams: number; teams_with_scored_submission: number };
  joins_by_day: { day: string; count: number }[];
  submissions_by_day: { day: string; count: number }[];
  submission_status: Record<string, number>;
  team_sizes: { size: number; teams: number }[];
  score_histogram: { start: number; end: number; count: number }[];
  universities: { name: string; participants: number }[];
  top_errors: { code: string; count: number }[];
  privacy_note: string;
}

/** Fills missing days with zero so the time axis is continuous. */
function fillDays(rows: { day: string; count: number }[]): { day: string; count: number }[] {
  if (rows.length < 2) return rows;
  const map = new Map(rows.map((r) => [r.day, r.count]));
  const start = new Date(`${rows[0].day}T00:00:00Z`);
  const end = new Date(`${rows[rows.length - 1].day}T00:00:00Z`);
  const out: { day: string; count: number }[] = [];
  for (let d = start; d <= end && out.length < 400; d = new Date(d.getTime() + 86_400_000)) {
    const key = d.toISOString().slice(0, 10);
    out.push({ day: key, count: map.get(key) ?? 0 });
  }
  return out;
}

const dayLabel = (d: string | number) => formatDate(`${d}T00:00:00Z`, { month: "short", day: "numeric", timeZone: "UTC" });
const pct = (a: number, b: number) => (b > 0 ? `${Math.round((a / b) * 100)}%` : "—");

function ChartCard({ title, description, children, empty }: { title: string; description?: string; children: ReactNode; empty?: boolean }) {
  return (
    <Card>
      <CardHeader title={title} description={description} />
      <CardBody>{empty ? <p className="py-8 text-center text-sm text-subtle">No data yet.</p> : children}</CardBody>
    </Card>
  );
}

export default function ManageAnalyticsPage() {
  const { slug } = useParams<{ slug: string }>();
  const query = useQuery({
    queryKey: ["competitions", slug, "analytics"],
    queryFn: () => get<Analytics>(`/competitions/${slug}/analytics`),
    staleTime: 60_000,
  });

  return (
    <div>
      <ManageHeading title="Analytics" description="Aggregate engagement and submission health. Individual participants are never identifiable here." />
      <QueryState query={query} loading={<SkeletonCards count={6} />}>
        {(a) => {
          const f = a.funnel;
          const joins = fillDays(a.joins_by_day);
          const subs = fillDays(a.submissions_by_day);
          const statuses = Object.entries(a.submission_status).sort((x, y) => y[1] - x[1]);
          const totalSubs = statuses.reduce((n, [, v]) => n + v, 0);
          if (f.views === 0 && f.participants === 0 && totalSubs === 0) {
            return (
              <EmptyState
                icon={<BarChart3 className="h-5 w-5" />}
                title="No activity yet"
                description="Views, joins and submissions will show up here once the competition is published and people start taking part."
              />
            );
          }
          return (
            <div className="space-y-6">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Stat label="Page views" value={formatNumber(f.views)} icon={<Eye className="h-4 w-4" />} />
                <Stat label="Participants" value={formatNumber(f.participants)} hint={`${pct(f.participants, f.views)} of views`} icon={<Users className="h-4 w-4" />} />
                <Stat label="Members who submitted" value={formatNumber(f.members_who_submitted)} hint={`${pct(f.members_who_submitted, f.participants)} of participants`} icon={<Upload className="h-4 w-4" />} />
                <Stat label="Teams" value={formatNumber(f.teams)} icon={<UsersRound className="h-4 w-4" />} />
                <Stat label="Teams with a scored entry" value={formatNumber(f.teams_with_scored_submission)} hint={`${pct(f.teams_with_scored_submission, f.teams)} of teams`} icon={<ShieldCheck className="h-4 w-4" />} />
              </div>

              <ChartCard title="Participation funnel" description="From page view to a scored submission.">
                <HBarList
                  label="Participation funnel"
                  data={[
                    { label: "Viewed the page", value: f.views },
                    { label: "Joined", value: f.participants },
                    { label: "Submitted (members)", value: f.members_who_submitted },
                    { label: "Teams with a scored entry", value: f.teams_with_scored_submission },
                  ]}
                />
              </ChartCard>

              <div className="grid gap-6 lg:grid-cols-2">
                <ChartCard title="Joins per day" description="Dates in UTC." empty={joins.length === 0}>
                  <LineChart label="New participants per day" data={joins.map((d) => ({ x: d.day, y: d.count }))} xFormat={dayLabel} yFormat={(v) => formatNumber(v)} />
                </ChartCard>
                <ChartCard title="Submissions per day" description="Dates in UTC." empty={subs.length === 0}>
                  <BarChart label="Submissions per day" data={subs.map((d) => ({ label: dayLabel(d.day), value: d.count }))} />
                </ChartCard>
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <ChartCard title="Submission outcomes" description={`${formatNumber(totalSubs)} submissions in total.`} empty={statuses.length === 0}>
                  <HBarList label="Submissions by status" data={statuses.map(([k, v]) => ({ label: titleCase(k), value: v }))} valueFormat={(v) => `${formatNumber(v)} (${pct(v, totalSubs)})`} />
                </ChartCard>
                <ChartCard title="Most common errors" description="Validation and scoring failures by error code." empty={a.top_errors.length === 0}>
                  <HBarList label="Top submission errors" data={a.top_errors.map((e) => ({ label: e.code, value: e.count }))} />
                </ChartCard>
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <ChartCard
                  title="Best public score per team"
                  description={`${a.competition.metric ?? "Score"} — ${a.competition.direction === "maximize" ? "higher is better" : "lower is better"}. Public split only; private scores are never included.`}
                  empty={a.score_histogram.length === 0}
                >
                  <Histogram label="Distribution of each team's best public score" bins={a.score_histogram} />
                </ChartCard>
                <ChartCard title="Team sizes" empty={a.team_sizes.length === 0}>
                  <BarChart label="Teams by number of members" data={a.team_sizes.map((t) => ({ label: `${t.size} member${t.size === 1 ? "" : "s"}`, value: t.teams }))} />
                </ChartCard>
              </div>

              <ChartCard title="Universities" description="Participants by self-reported or verified university." empty={a.universities.length === 0}>
                <HBarList label="Participants by university" data={a.universities.map((u) => ({ label: u.name, value: u.participants }))} />
              </ChartCard>

              <p className="flex items-center gap-2 text-xs text-subtle">
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> {a.privacy_note}
              </p>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
