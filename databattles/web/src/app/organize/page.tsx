"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Building2, CalendarClock, ExternalLink, Gavel, LayoutGrid, Plus, Send, Settings2, Trophy, Users } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";

import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/extras";
import { Cover } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton, SkeletonRows, SkeletonStats } from "@/components/ui/states";
import { publishChecksKey, type PublishCheck } from "@/components/organizer/shared";
import { Tile, TileGrid } from "@/components/organizer/ui";
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

const toneDot: Record<Tone, string> = {
  neutral: "bg-border-strong",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  accent: "bg-accent",
  info: "bg-info",
};

function RowMetric({ label, children, title }: { label: string; children: ReactNode; title?: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-subtle">{label}</dt>
      <dd className="tabular mt-1 truncate text-sm text-fg" title={title}>{children}</dd>
    </div>
  );
}

function CompetitionRow({ row, checks }: { row: Row; checks?: PublishCheck[] }) {
  const c = row.card;
  const action = nextAction(row, checks);
  const organizer = row.roles.includes("organizer");
  const startsView = c.status === "upcoming" || c.status === "draft";
  return (
    <li className="relative grid gap-4 px-4 py-4 transition-colors duration-200 hover:bg-surface-2/40 sm:px-5 lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-center lg:gap-6">
      <div className="min-w-0">
        <div className="flex min-w-0 items-start gap-4">
          <Cover style={c.cover_style} className="hidden h-[3.25rem] w-[4.5rem] shrink-0 rounded-[var(--radius-md)] border border-border sm:block" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge status={c.status} />
              {c.is_demo ? <DemoBadge /> : null}
              {row.roles.map((r) => (
                <Badge key={r} tone="outline">{titleCase(r)}</Badge>
              ))}
              <Badge tone="outline">{titleCase(c.visibility)}</Badge>
            </div>
            <h2 className="mt-1.5 line-clamp-2 text-[15px] font-semibold tracking-[-0.01em] text-fg sm:truncate">
              <Link
                href={organizer ? `/competitions/${c.slug}/manage` : `/competitions/${c.slug}`}
                className="rounded-sm transition-colors hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
              >
                {c.title}
              </Link>
            </h2>
            <p className="mt-0.5 truncate text-xs text-subtle">
              {titleCase(c.event_type)} · {c.scoring_mode === "automatic" ? (c.metric ?? "automatic scoring") : titleCase(c.scoring_mode)}
              {c.host ? <> · hosted by {c.host.name}</> : null}
            </p>
          </div>
        </div>
        <dl className="mt-3.5 grid grid-cols-2 gap-x-6 gap-y-3 sm:ml-[5.5rem] sm:grid-cols-4">
          <RowMetric label="Participants">
            {formatNumber(c.participant_count)} <span className="text-xs text-subtle">· {formatNumber(c.team_count)} teams</span>
          </RowMetric>
          <RowMetric label="Submissions 24h">{formatNumber(row.submissions_24h)}</RowMetric>
          <div className="min-w-0">
            <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-subtle">Queue / failed</dt>
            <dd className="tabular mt-1 text-sm">
              <span className={row.pending_submissions ? "text-warning" : "text-fg"}>{formatNumber(row.pending_submissions)}</span>
              <span className="text-subtle"> / </span>
              <span className={row.failed_submissions ? "font-semibold text-danger" : "text-fg"}>{formatNumber(row.failed_submissions)}</span>
              {row.failed_submissions ? <span className="sr-only"> failed submissions need attention</span> : null}
            </dd>
          </div>
          <RowMetric label={startsView ? "Starts" : "Ends"} title={formatDateTime(startsView ? c.starts_at : c.ends_at)}>
            {startsView ? (c.starts_at ? relativeTime(c.starts_at) : "Not set") : c.ends_at ? relativeTime(c.ends_at) : "Not set"}
          </RowMetric>
        </dl>
        {row.last_submission_at ? <p className="mt-2.5 text-xs text-subtle sm:ml-[5.5rem]">Last submission {relativeTime(row.last_submission_at)}</p> : null}
      </div>
      <div className="flex min-w-0 flex-col gap-2 lg:items-stretch">
        <Link
          href={action.href}
          className={cn(
            "group/next inline-flex min-h-10 items-center justify-between gap-2 rounded-[var(--radius-md)] border bg-bg-elevated/60 px-3 py-2 text-sm font-medium transition-colors duration-200 hover:bg-surface-2",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
            toneClass[action.tone],
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", toneDot[action.tone])} aria-hidden />
            <span className="min-w-0">{action.label}</span>
          </span>
          <ArrowRight className="h-4 w-4 shrink-0 transition-transform duration-200 group-hover/next:translate-x-0.5" aria-hidden />
        </Link>
        <div className="flex flex-wrap gap-1.5 lg:justify-end">
          {organizer ? <LinkButton href={`/competitions/${c.slug}/manage`} size="sm" variant="secondary" icon={<Settings2 className="h-3.5 w-3.5" />}>Manage</LinkButton> : null}
          {row.roles.includes("judge") ? <LinkButton href={`/judge/${c.slug}`} size="sm" variant="ghost" icon={<Gavel className="h-4 w-4" />}>Judge</LinkButton> : null}
          <LinkButton href={`/competitions/${c.slug}`} size="sm" variant="ghost" icon={<ExternalLink className="h-3.5 w-3.5" />}>Public page</LinkButton>
        </div>
      </div>
    </li>
  );
}

function OrganizeSkeleton() {
  return (
    <Container>
      <div className="space-y-6 pb-16 pt-12" role="status" aria-label="Loading your competitions">
        <div className="space-y-3">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-9 w-72 max-w-full" />
          <Skeleton className="h-4 w-[30rem] max-w-full" />
        </div>
        <SkeletonStats count={4} />
        <SkeletonRows rows={4} />
      </div>
    </Container>
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

  if (me.isPending || !me.data) return <OrganizeSkeleton />;

  const hosts = (memberships.data ?? []).filter((m) => m.status === "active" && ["owner", "admin", "manager"].includes(m.role));
  const canCreate = hosts.length > 0 || hasRole(me.data, "platform_admin");
  const list = rows.data ?? [];
  const counts = Object.fromEntries(FILTERS.map((f) => [f.key, list.filter((r) => matches(r, f.key)).length])) as Record<FilterKey, number>;
  const attention = list.filter((r) => r.failed_submissions > 0 || (r.card.status === "ended" && r.roles.includes("organizer"))).length;
  const visible = list.filter((r) => matches(r, filter));

  return (
    <Container className="pb-16">
      <PageHeader
        eyebrow="Organizer tools"
        icon={<LayoutGrid />}
        title="Your competitions"
        description="Competitions and events you organize, judge or oversee through your organizations — with health signals and the next step for each."
        meta={
          memberships.isSuccess ? (
            <span className="inline-flex items-center gap-1.5">
              <Building2 className="h-3.5 w-3.5" aria-hidden />
              {hosts.length
                ? `You can host with ${hosts.length} ${hosts.length === 1 ? "organization" : "organizations"}`
                : hasRole(me.data, "platform_admin")
                  ? "Platform admin — you can host platform events"
                  : "No hosting organization yet"}
            </span>
          ) : undefined
        }
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
        <div className="space-y-6" role="status" aria-label="Loading your competitions">
          <SkeletonStats count={4} />
          <SkeletonRows rows={5} />
        </div>
      ) : rows.isError ? (
        <ErrorState error={rows.error} onRetry={() => rows.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          icon={<Trophy />}
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
          <TileGrid cols={4} className="mb-8 animate-rise [animation-delay:80ms]">
            <Tile label="Competitions" value={formatNumber(list.filter((r) => r.card.status !== "archived").length)} icon={<Trophy />} hint="Excluding archived" />
            <Tile label="Upcoming & live" value={formatNumber(counts.live)} icon={<CalendarClock />} accent="success" />
            <Tile label="Drafts" value={formatNumber(counts.draft)} icon={<Send />} accent="info" />
            <Tile
              label="Need attention"
              value={formatNumber(attention)}
              hint="Failed submissions or results to finalize"
              icon={<AlertTriangle />}
              accent={attention ? "warning" : "muted"}
            />
          </TileGrid>

          <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            {/* 36px touch targets on phones; the right-edge fade hints that the row scrolls, and the trailing
                padding keeps the last segment clear of the fade once scrolled to the end. */}
            <SegmentedControl
              label="Filter competitions"
              value={filter}
              onChange={setFilter}
              options={FILTERS.map((f) => ({ value: f.key, label: f.label, count: counts[f.key] }))}
              className="pr-7 [mask-image:linear-gradient(to_right,black_calc(100%_-_28px),transparent)] sm:pr-0.5 sm:[mask-image:none] [&>button]:h-9 sm:[&>button]:h-8"
            />
            <p className="tabular px-1 text-xs text-subtle" aria-live="polite">
              {/* "All" excludes archived competitions, so the total does too; the Archived view counts itself. */}
              {filter === "archived"
                ? `Showing ${formatNumber(visible.length)} archived`
                : `Showing ${formatNumber(visible.length)} of ${formatNumber(counts.all)}`}
            </p>
          </div>
          {visible.length === 0 ? (
            <EmptyState
              icon={<Users />}
              title="Nothing in this view"
              description="Try another filter."
              action={filter !== "all" ? <Button variant="secondary" onClick={() => setFilter("all")}>Show all</Button> : undefined}
            />
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card" aria-label="Competitions">
              {visible.map((r) => (
                <CompetitionRow key={r.card.id} row={r} checks={checksBySlug[r.card.slug]} />
              ))}
            </ul>
          )}
        </>
      )}

      <section className="mt-12" aria-labelledby="organizer-guide">
        <h2 id="organizer-guide" className="mb-4 text-eyebrow text-subtle">How hosting works</h2>
        <ol className="grid gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-3">
          <li className="bg-surface p-5">
            <p className="tabular font-mono text-[11px] text-accent-strong">01</p>
            <p className="mt-2 text-sm font-semibold text-fg">Host through an organization</p>
            <p className="mt-1 text-sm leading-relaxed text-muted">
              Competitions belong to an organization you manage. No organization yet? <Link href="/orgs/new" className="text-accent-strong hover:underline">Create one</Link>.
            </p>
          </li>
          <li className="bg-surface p-5">
            <p className="tabular font-mono text-[11px] text-accent-strong">02</p>
            <p className="mt-2 text-sm font-semibold text-fg">Configure and publish</p>
            <p className="mt-1 text-sm leading-relaxed text-muted">Set the schedule, scoring and content, upload hidden ground truth, then publish once the checklist is green.</p>
          </li>
          <li className="bg-surface p-5">
            <p className="tabular font-mono text-[11px] text-accent-strong">03</p>
            <p className="mt-2 text-sm font-semibold text-fg">Run, finalize, certify</p>
            <p className="mt-1 text-sm leading-relaxed text-muted">Monitor submissions, post announcements, finalize a versioned results snapshot and issue verifiable certificates.</p>
          </li>
        </ol>
      </section>
    </Container>
  );
}
