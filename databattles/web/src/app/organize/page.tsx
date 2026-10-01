"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Building2, CalendarClock, Gavel, Plus, Send, Trophy, Users } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, Stat } from "@/components/ui/card";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows, Spinner } from "@/components/ui/states";
import { publishChecksKey, type PublishCheck } from "@/components/organizer/shared";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useRequireAuth } from "@/lib/hooks";
import type { Schemas } from "@/lib/types";

type Row = Schemas["OrganizerCompetitionRow"];

interface Membership {
  org: Schemas["OrgMini"];
  role: string;
  status: string;
}

const FILTERS = [
  { key: "all", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "live", label: "Upcoming & live" },
  { key: "ended", label: "Awaiting results" },
  { key: "completed", label: "Completed" },
  { key: "archived", label: "Archived" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

function matches(row: Row, f: FilterKey): boolean {
  const s = row.card.status;
  if (f === "all") return s !== "archived";
  if (f === "live") return s === "upcoming" || s === "active";
  return s === f;
}

type Tone = "neutral" | "success" | "warning" | "danger" | "accent" | "info";

function nextAction(row: Row, checks: PublishCheck[] | undefined): { label: string; href: string; tone: Tone } {
  const slug = row.card.slug;
  const manage = `/competitions/${slug}/manage`;
  const organizer = row.roles.includes("organizer");
  if (!organizer) {
    if (row.roles.includes("judge")) return { label: "Open judging queue", href: `/judge/${slug}`, tone: "accent" };
    return { label: "View competition", href: `/competitions/${slug}`, tone: "neutral" };
  }
  switch (row.card.status) {
    case "draft": {
      if (!checks) return { label: "Finish setup and publish", href: manage, tone: "info" };
      const required = checks.filter((c) => c.required);
      const done = required.filter((c) => c.ok).length;
      return done === required.length
        ? { label: "Ready to publish", href: manage, tone: "success" }
        : { label: `${done}/${required.length} publish checks complete`, href: manage, tone: "warning" };
    }
    case "upcoming":
      return { label: `Opens ${relativeTime(row.card.starts_at)} — post a welcome announcement`, href: `${manage}/announcements`, tone: "info" };
    case "active":
      if (row.failed_submissions > 0) return { label: `Review ${formatNumber(row.failed_submissions)} failed submissions`, href: `${manage}/submissions?status=failed`, tone: "danger" };
      return { label: `Live — ends ${relativeTime(row.card.ends_at)}`, href: manage, tone: "success" };
    case "ended":
      if (row.pending_submissions > 0) return { label: `${formatNumber(row.pending_submissions)} submissions still scoring`, href: `${manage}/submissions`, tone: "warning" };
      if (row.card.scoring_mode === "judged") return { label: "Review judging, then finalize", href: `${manage}/judging`, tone: "accent" };
      return { label: "Ready to finalize results", href: `${manage}/results`, tone: "accent" };
    case "completed":
      return { label: "Issue or review certificates", href: `${manage}/results`, tone: "neutral" };
    default:
      return { label: "Archived — read only", href: manage, tone: "neutral" };
  }
}

const toneClass: Record<Tone, string> = {
  neutral: "border-border text-muted",
  success: "border-success/40 text-success",
  warning: "border-warning/40 text-warning",
  danger: "border-danger/40 text-danger",
  accent: "border-accent/40 text-accent-strong",
  info: "border-info/40 text-info",
};

function CompetitionRow({ row, checks }: { row: Row; checks?: PublishCheck[] }) {
  const c = row.card;
  const action = nextAction(row, checks);
  const organizer = row.roles.includes("organizer");
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={c.status} />
            {c.is_demo ? <DemoBadge /> : null}
            {row.roles.map((r) => (
              <Badge key={r} tone="outline">{titleCase(r)}</Badge>
            ))}
            <Badge tone="outline">{titleCase(c.visibility)}</Badge>
          </div>
          <h2 className="mt-2 truncate text-base font-semibold text-fg">
            <Link href={organizer ? `/competitions/${c.slug}/manage` : `/competitions/${c.slug}`} className="hover:text-accent-strong">
              {c.title}
            </Link>
          </h2>
          <p className="mt-0.5 text-xs text-subtle">
            {titleCase(c.event_type)} · {c.scoring_mode === "automatic" ? (c.metric ?? "automatic scoring") : titleCase(c.scoring_mode)}
            {c.host ? <> · hosted by {c.host.name}</> : null}
          </p>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-subtle">Participants</dt>
              <dd className="tabular-nums text-fg">{formatNumber(c.participant_count)} <span className="text-xs text-subtle">· {formatNumber(c.team_count)} teams</span></dd>
            </div>
            <div>
              <dt className="text-xs text-subtle">Submissions (24h)</dt>
              <dd className="tabular-nums text-fg">{formatNumber(row.submissions_24h)}</dd>
            </div>
            <div>
              <dt className="text-xs text-subtle">Queue / failed</dt>
              <dd className="tabular-nums">
                <span className={row.pending_submissions ? "text-warning" : "text-fg"}>{formatNumber(row.pending_submissions)}</span>
                <span className="text-subtle"> / </span>
                <span className={row.failed_submissions ? "font-semibold text-danger" : "text-fg"}>{formatNumber(row.failed_submissions)}</span>
                {row.failed_submissions ? <span className="sr-only"> failed submissions need attention</span> : null}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-subtle">{c.status === "upcoming" || c.status === "draft" ? "Starts" : "Ends"}</dt>
              <dd className="text-fg" title={formatDateTime(c.status === "upcoming" || c.status === "draft" ? c.starts_at : c.ends_at)}>
                {c.status === "upcoming" || c.status === "draft" ? (c.starts_at ? relativeTime(c.starts_at) : "Not set") : c.ends_at ? relativeTime(c.ends_at) : "Not set"}
              </dd>
            </div>
          </dl>
          {row.last_submission_at ? <p className="mt-2 text-xs text-subtle">Last submission {relativeTime(row.last_submission_at)}</p> : null}
        </div>
        <div className="flex shrink-0 flex-col gap-2 lg:w-72 lg:items-end">
          <Link
            href={action.href}
            className={cn("inline-flex items-center justify-between gap-2 rounded-[var(--radius-md)] border px-3 py-2 text-sm font-medium transition-colors hover:bg-surface-2", toneClass[action.tone])}
          >
            <span>{action.label}</span>
            <ArrowRight className="h-4 w-4 shrink-0" aria-hidden />
          </Link>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            {organizer ? <LinkButton href={`/competitions/${c.slug}/manage`} size="sm" variant="secondary">Manage</LinkButton> : null}
            {row.roles.includes("judge") ? <LinkButton href={`/judge/${c.slug}`} size="sm" variant="ghost" icon={<Gavel className="h-4 w-4" />}>Judge</LinkButton> : null}
            <LinkButton href={`/competitions/${c.slug}`} size="sm" variant="ghost">Public page</LinkButton>
          </div>
        </div>
      </div>
    </Card>
  );
}

export default function OrganizePage() {
  const me = useRequireAuth();
  const [filter, setFilter] = useState<FilterKey>("all");
  const rows = useQuery({
    queryKey: ["competitions", "organizing"],
    queryFn: () => get<Row[]>("/competitions/organizing"),
    enabled: !!me.data,
  });
  const memberships = useQuery({
    queryKey: ["orgs", "me", "memberships"],
    queryFn: () => get<Membership[]>("/orgs/me/memberships"),
    enabled: !!me.data,
  });
  const drafts = useMemo(() => (rows.data ?? []).filter((r) => r.card.status === "draft" && r.roles.includes("organizer")).slice(0, 20), [rows.data]);
  const checks = useQueries({
    queries: drafts.map((r) => ({
      queryKey: publishChecksKey(r.card.slug),
      queryFn: () => get<PublishCheck[]>(`/competitions/${r.card.slug}/publish-checks`),
      staleTime: 60_000,
    })),
  });
  const checksBySlug = useMemo(() => {
    const out: Record<string, PublishCheck[] | undefined> = {};
    drafts.forEach((r, i) => (out[r.card.slug] = checks[i]?.data));
    return out;
  }, [drafts, checks]);

  if (me.isPending || !me.data) return <Spinner />;

  const hosts = (memberships.data ?? []).filter((m) => m.status === "active" && ["owner", "admin", "manager"].includes(m.role));
  const canCreate = hosts.length > 0 || hasRole(me.data, "platform_admin");
  const list = rows.data ?? [];
  const counts = Object.fromEntries(FILTERS.map((f) => [f.key, list.filter((r) => matches(r, f.key)).length])) as Record<FilterKey, number>;
  const attention = list.filter((r) => r.failed_submissions > 0 || (r.card.status === "ended" && r.roles.includes("organizer"))).length;
  const visible = list.filter((r) => matches(r, filter));

  return (
    <Container>
      <PageHeader
        eyebrow="Organizer tools"
        title="Your competitions"
        description="Competitions and events you organize, judge or oversee through your organizations — with health signals and the next step for each."
        actions={
          canCreate ? (
            <LinkButton href="/organize/new" icon={<Plus className="h-4 w-4" />}>Create competition</LinkButton>
          ) : (
            <LinkButton href="/orgs/new" variant="secondary" icon={<Building2 className="h-4 w-4" />}>Create an organization</LinkButton>
          )
        }
      />

      {!me.data.email_verified ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Verify your email to host competitions" action={<LinkButton href="/verify-email" size="sm" variant="secondary">Verify email</LinkButton>}>
            Creating a competition requires a verified email address.
          </InlineNotice>
        </div>
      ) : null}

      {memberships.isSuccess && !canCreate ? (
        <div className="mb-6">
          <InlineNotice
            tone="info"
            title="Competitions are hosted by organizations"
            action={<LinkButton href="/orgs/new" size="sm" variant="secondary">Create an organization</LinkButton>}
          >
            You need to be an owner, admin or manager of a university, club or community organization to host. Create a community
            organization, or ask an admin of your university or club to give you the manager role.
          </InlineNotice>
        </div>
      ) : null}

      {rows.isPending ? (
        <SkeletonRows rows={5} />
      ) : rows.isError ? (
        <ErrorState error={rows.error} onRetry={() => rows.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          icon={<Trophy className="h-5 w-5" />}
          title="You're not organizing anything yet"
          description="Create a competition hosted by an organization you manage. You'll set up scoring, the schedule and content in a guided wizard, then publish when the checklist is complete."
          action={
            canCreate ? (
              <LinkButton href="/organize/new" icon={<Plus className="h-4 w-4" />}>Create competition</LinkButton>
            ) : (
              <LinkButton href="/orgs/new" icon={<Building2 className="h-4 w-4" />}>Create an organization</LinkButton>
            )
          }
        />
      ) : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Competitions" value={formatNumber(list.filter((r) => r.card.status !== "archived").length)} icon={<Trophy className="h-4 w-4" />} />
            <Stat label="Upcoming & live" value={formatNumber(counts.live)} icon={<CalendarClock className="h-4 w-4" />} />
            <Stat label="Drafts" value={formatNumber(counts.draft)} icon={<Send className="h-4 w-4" />} />
            <Stat
              label="Need attention"
              value={formatNumber(attention)}
              hint="Failed submissions or results to finalize"
              icon={<AlertTriangle className={cn("h-4 w-4", attention ? "text-warning" : "")} />}
            />
          </div>
          <div className="mb-4 flex gap-1 overflow-x-auto border-b border-border" role="group" aria-label="Filter competitions">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
                className={cn(
                  "-mb-px shrink-0 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                  filter === f.key ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
                )}
              >
                {f.label}
                <span className="ml-1.5 rounded-full bg-surface-3 px-1.5 text-xs text-subtle">{counts[f.key]}</span>
              </button>
            ))}
          </div>
          {visible.length === 0 ? (
            <EmptyState icon={<Users className="h-5 w-5" />} title="Nothing in this view" description="Try another filter." />
          ) : (
            <div className="grid gap-3">
              {visible.map((r) => (
                <CompetitionRow key={r.card.id} row={r} checks={checksBySlug[r.card.slug]} />
              ))}
            </div>
          )}
        </>
      )}

      <section className="mt-10 grid gap-4 rounded-[var(--radius-lg)] border border-border bg-surface p-5 sm:grid-cols-3" aria-labelledby="organizer-guide">
        <h2 id="organizer-guide" className="sr-only">How hosting works</h2>
        <div>
          <p className="text-sm font-semibold text-fg">1. Host through an organization</p>
          <p className="mt-1 text-sm text-muted">
            Competitions belong to an organization you manage. No organization yet? <Link href="/orgs/new" className="text-accent-strong hover:underline">Create one</Link>.
          </p>
        </div>
        <div>
          <p className="text-sm font-semibold text-fg">2. Configure and publish</p>
          <p className="mt-1 text-sm text-muted">Set the schedule, scoring and content, upload hidden ground truth, then publish once the checklist is green.</p>
        </div>
        <div>
          <p className="text-sm font-semibold text-fg">3. Run, finalize, certify</p>
          <p className="mt-1 text-sm text-muted">Monitor submissions, post announcements, finalize a versioned results snapshot and issue verifiable certificates.</p>
        </div>
      </section>
    </Container>
  );
}
