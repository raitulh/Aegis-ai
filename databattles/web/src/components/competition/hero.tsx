"use client";

import {
  BadgeCheck,
  CalendarCheck2,
  CalendarClock,
  CalendarRange,
  Gavel,
  Gauge,
  Lock,
  Mail,
  Settings2,
  Trophy,
  Upload,
  Users,
} from "lucide-react";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";

import { Avatar } from "@/components/ui/avatar";
import { Badge, DemoBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { MetaItem } from "@/components/ui/extras";
import { Countdown, Cover, ProgressBar } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { InlineNotice } from "@/components/ui/states";
import { GridPlane } from "@/components/visual/grid-plane";
import { cn } from "@/lib/cn";
import { compactNumber, formatDate, formatNumber, relativeTime } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import type { CompetitionDetail } from "@/lib/types";
import { DateTime } from "./datetime";
import { JoinAction } from "./join";
import { FORMAT_LABELS, VISIBILITY_LABELS, directionLabel, enumLabel } from "./labels";

/* ------------------------------------------------------------------ countdown panel */

/** The single most relevant moment for the competition right now: a live countdown, or when it ended. */
function TimerBlock({ comp }: { comp: CompetitionDetail }) {
  let label: string | null = null;
  let body: ReactNode = null;
  if (comp.status === "upcoming" && comp.starts_at) {
    label = "Starts in";
    body = <Countdown to={comp.starts_at} prefix="Starts in " variant="blocks" className="text-lg font-semibold text-fg" />;
  } else if (comp.status === "active" && comp.ends_at) {
    label = "Ends in";
    body = <Countdown to={comp.ends_at} prefix="Ends in " variant="blocks" className="text-lg font-semibold text-fg" />;
  } else if (comp.status === "completed" && comp.finalized_at) {
    label = "Results final";
    body = <p className="tabular text-xl font-semibold tracking-[-0.02em] text-fg">{formatDate(comp.finalized_at)}</p>;
  } else if (comp.ends_at) {
    label = "Ended";
    body = <p className="text-xl font-semibold tracking-[-0.02em] text-fg">{relativeTime(comp.ends_at)}</p>;
  }
  if (!label) return null;
  return (
    <div>
      <p className="flex items-center gap-1.5 text-eyebrow text-subtle">
        <CalendarClock className="h-3.5 w-3.5" aria-hidden />
        {label}
      </p>
      <div className="mt-2.5">{body}</div>
    </div>
  );
}

/** The running competition window (real start/end dates), or null when there is no progress to show. */
function activeWindow(comp: CompetitionDetail): { start: number; end: number } | null {
  const start = comp.starts_at ? new Date(comp.starts_at).getTime() : null;
  const end = comp.ends_at ? new Date(comp.ends_at).getTime() : null;
  if (comp.status !== "active" || !start || !end || end <= start) return null;
  return { start, end };
}

/** Share of the competition window that has elapsed — derived from the real start/end dates only. */
function WindowProgress({ comp }: { comp: CompetitionDetail }) {
  const now = useNow(60_000).getTime();
  const span = activeWindow(comp);
  if (!span) return null;
  const { start, end } = span;
  const pct = Math.max(0, Math.min(100, ((now - start) / (end - start)) * 100));
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">
        <span>Window</span>
        <span className="tabular">{Math.round(pct)}% elapsed</span>
      </div>
      <ProgressBar value={pct} label={`${Math.round(pct)}% of the competition window has elapsed`} />
      <div className="mt-1.5 flex justify-between text-[11px] text-subtle">
        <span className="tabular">{formatDate(comp.starts_at, { month: "short", day: "numeric" })}</span>
        <span className="tabular">{formatDate(comp.ends_at, { month: "short", day: "numeric" })}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ participant CTA */

function ParticipantPanel({ comp }: { comp: CompetitionDetail }) {
  const v = comp.viewer;
  const base = `/competitions/${comp.slug}`;
  return (
    <div className="flex flex-col items-start gap-3">
      <div className="flex items-center gap-2.5">
        <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-inset ring-[color-mix(in_oklab,var(--success)_30%,transparent)]">
          <BadgeCheck className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-fg">You&apos;re participating</p>
          <p className="text-xs text-muted">
            {v.team ? (
              v.team.is_solo ? (
                <>Competing individually</>
              ) : (
                <>
                  Team <Link href={`${base}/team`} className="font-medium text-fg hover:text-accent-strong">{v.team.name}</Link> ·{" "}
                  {formatNumber(v.team.member_count)} {v.team.member_count === 1 ? "member" : "members"}
                  {v.team.is_captain ? " · you're captain" : ""}
                </>
              )
            ) : (
              <>You don&apos;t have a team yet.</>
            )}
          </p>
        </div>
      </div>
      <div className="flex w-full flex-wrap gap-2 [&>a]:flex-1">
        {comp.scoring_mode === "automatic" && v.can_submit ? (
          <LinkButton href={`${base}/submissions`} icon={<Upload className="h-4 w-4" aria-hidden />}>Submit predictions</LinkButton>
        ) : null}
        {comp.scoring_mode === "judged" && v.submit_blockers.every((b) => b === "not_automatically_scored") ? (
          <LinkButton href={`${base}/submissions`} icon={<Upload className="h-4 w-4" aria-hidden />}>Submit project</LinkButton>
        ) : null}
        {!v.team && comp.team_max_size > 1 ? (
          <LinkButton href={`${base}/team`} variant={v.can_submit ? "secondary" : "primary"} icon={<Users className="h-4 w-4" aria-hidden />}>
            Create or join a team
          </LinkButton>
        ) : null}
      </div>
      {comp.scoring_mode === "automatic" && v.submissions_remaining_today !== null && v.submissions_remaining_today !== undefined && comp.status === "active" ? (
        <p className="text-xs text-subtle">
          <span className="tabular font-medium text-muted">{formatNumber(v.submissions_remaining_today)}</span> of{" "}
          <span className="tabular">{formatNumber(comp.daily_submission_limit)}</span> submissions left today (resets 00:00 UTC)
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ hero */

/** Wraps long values (dates) inside `MetaItem`, whose value cell truncates by default. */
function Wrap({ children }: { children: ReactNode }) {
  return <span className="block whitespace-normal leading-snug">{children}</span>;
}

export function CompetitionHero({ comp }: { comp: CompetitionDetail }) {
  const v = comp.viewer;
  const base = `/competitions/${comp.slug}`;
  const isOrganizer = v.roles.includes("organizer");
  const isJudge = v.roles.includes("judge");
  const ev = comp.evaluation;
  const facts: { key: string; node: ReactNode }[] = [
    {
      key: "people",
      node: (
        <MetaItem icon={<Users aria-hidden />} label="Participants">
          <span className="tabular">{compactNumber(comp.participant_count)}</span>
          {comp.team_max_size > 1 ? (
            <span className="block text-xs font-normal text-subtle">
              <span className="tabular">{compactNumber(comp.team_count)}</span> {comp.team_count === 1 ? "team" : "teams"}
            </span>
          ) : null}
        </MetaItem>
      ),
    },
    {
      key: "starts",
      node: (
        <MetaItem icon={<CalendarRange aria-hidden />} label="Starts">
          <Wrap><DateTime value={comp.starts_at} eventTimeZone={comp.timezone} stacked /></Wrap>
        </MetaItem>
      ),
    },
    {
      key: "ends",
      node: (
        <MetaItem icon={<CalendarCheck2 aria-hidden />} label="Ends">
          <Wrap><DateTime value={comp.ends_at} eventTimeZone={comp.timezone} stacked /></Wrap>
        </MetaItem>
      ),
    },
  ];
  if (comp.has_prize) {
    facts.push({
      key: "prize",
      node: (
        <MetaItem icon={<Trophy aria-hidden className="text-warning" />} label="Prizes">
          <Wrap>{comp.prize_summary ?? "See prizes"}</Wrap>
        </MetaItem>
      ),
    });
  }
  if (comp.scoring_mode === "automatic" && ev) {
    facts.push({
      key: "metric",
      node: (
        <MetaItem icon={<Gauge aria-hidden />} label="Evaluation">
          <Wrap>
            {ev.metric_label}
            <span className="block text-xs font-normal text-subtle">{directionLabel(ev.direction)}</span>
          </Wrap>
        </MetaItem>
      ),
    });
  } else if (comp.scoring_mode === "judged") {
    facts.push({ key: "metric", node: <MetaItem icon={<Gavel aria-hidden />} label="Evaluation">Judged by rubric</MetaItem> });
  }
  const cols = facts.length >= 5 ? "lg:grid-cols-5" : facts.length === 4 ? "lg:grid-cols-4" : "lg:grid-cols-3";
  // Only a running competition (countdown + window bar) has enough in the action panel to match the identity
  // panel's height; otherwise the panel keeps its natural height instead of stretching into empty glass.
  const stretchAside = activeWindow(comp) !== null;

  return (
    <section aria-labelledby="competition-title" className="relative isolate -mt-14 lg:-mt-[4.25rem]">
      {/* Cover band — sits under the floating header and dissolves into the page. */}
      <Cover
        style={comp.cover_style}
        className="h-[17rem] [mask-image:linear-gradient(to_bottom,black_35%,transparent)] sm:h-[20rem] lg:h-[23rem]"
      >
        <GridPlane animated={false} className="opacity-40" />
        <div aria-hidden className="absolute inset-0 noise" />
        <div aria-hidden className="absolute inset-0 bg-[radial-gradient(90%_80%_at_50%_0%,transparent,color-mix(in_oklab,var(--bg)_45%,transparent))]" />
        {/* Cover art is dark-based; in the light theme a token wash keeps the band from reading as a muddy stripe. */}
        <div aria-hidden className="absolute inset-0 hidden bg-[color-mix(in_oklab,var(--bg)_38%,transparent)] [[data-theme=light]_&]:block" />
      </Cover>

      <Container className="relative -mt-44 sm:-mt-48 lg:-mt-52">
        <div className={cn("grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21.5rem]", stretchAside ? "lg:items-stretch" : "lg:items-start")}>
          {/* Identity */}
          <div className="relative min-w-0 animate-rise overflow-hidden rounded-[var(--radius-2xl)] border border-border surface-glass p-5 shadow-elevated sm:p-7 lg:p-8">
            <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,color-mix(in_oklab,var(--accent)_60%,transparent),color-mix(in_oklab,var(--cyan)_50%,transparent),transparent)]" />
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge status={comp.status} />
              {comp.is_demo ? <DemoBadge /> : null}
              {comp.visibility !== "public" ? (
                <Badge tone="outline" icon={<Lock className="h-3 w-3" aria-hidden />}>{VISIBILITY_LABELS[comp.visibility] ?? comp.visibility}</Badge>
              ) : null}
              <Badge tone="outline">{enumLabel(comp.event_type)}</Badge>
              <Badge tone="outline">{enumLabel(comp.task_type)}</Badge>
              <Badge tone="outline">{enumLabel(comp.difficulty)}</Badge>
              {comp.format !== "online" ? <Badge tone="info">{FORMAT_LABELS[comp.format] ?? comp.format}</Badge> : null}
            </div>

            <h1 id="competition-title" className="mt-4 text-headline text-fg">{comp.title}</h1>
            {comp.summary ? <p className="mt-3 max-w-3xl text-[15px] leading-relaxed text-muted sm:text-base">{comp.summary}</p> : null}

            <div className="mt-5 flex items-center gap-2.5 text-sm text-muted">
              {comp.host ? (
                <>
                  <Avatar name={comp.host.name} src={comp.host.logo_url} size={22} className="rounded-md" />
                  <p className="min-w-0 leading-relaxed">
                    Hosted by{" "}
                    <Link href={`/orgs/${comp.host.slug}`} className="font-medium text-fg transition-colors hover:text-accent-strong">{comp.host.name}</Link>
                    {comp.host.verification_status === "verified" ? (
                      <span className="ml-2 inline-block align-middle"><VerifiedBadge title="Verified organization" /></span>
                    ) : null}
                  </p>
                </>
              ) : (
                <span>Community event</span>
              )}
            </div>

            <dl className={cn("mt-6 grid grid-cols-2 gap-x-6 gap-y-5 border-t border-border pt-6 sm:grid-cols-3", cols)}>
              {/* MetaItem is already the dt/dd group, so it must be a direct child of the <dl>. */}
              {facts.map((f) => <Fragment key={f.key}>{f.node}</Fragment>)}
            </dl>
          </div>

          {/* Action panel */}
          <aside
            aria-label="Take part"
            className="relative flex min-w-0 animate-rise flex-col gap-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border surface-glass border-gradient p-5 shadow-elevated [animation-delay:80ms] sm:p-6"
          >
            <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
            <div className="relative space-y-4">
              <TimerBlock comp={comp} />
              <WindowProgress comp={comp} />
            </div>
            <div className="relative mt-auto flex flex-col gap-3 border-t border-border pt-5">
              {v.is_participant ? <ParticipantPanel comp={comp} /> : <JoinAction comp={comp} size="lg" />}
              {isOrganizer ? (
                <LinkButton href={`${base}/manage`} variant="secondary" icon={<Settings2 className="h-4 w-4" aria-hidden />}>
                  Manage competition
                </LinkButton>
              ) : null}
              {isJudge ? (
                <Badge tone="info" icon={<Gavel className="h-3 w-3" aria-hidden />} className="self-start">You are a judge for this event</Badge>
              ) : null}
              {v.pending_invitations > 0 ? (
                <Link
                  href={`${base}/team`}
                  className="inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-accent-strong hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                >
                  <Mail className="h-4 w-4" aria-hidden />
                  {v.pending_invitations === 1 ? "You have a team invitation" : `You have ${v.pending_invitations} team invitations`}
                </Link>
              ) : null}
            </div>
          </aside>
        </div>

        {comp.frozen || comp.status === "draft" || comp.status === "completed" ? (
          <div className="mt-4 space-y-3">
            {comp.frozen ? (
              <InlineNotice tone="warning" title="This competition is temporarily paused">
                Registration and submissions are paused by the platform moderators.{comp.frozen_reason ? ` Reason: ${comp.frozen_reason}` : ""}
              </InlineNotice>
            ) : null}
            {comp.status === "draft" ? (
              <InlineNotice tone="info" title="Draft">Only staff can see this competition until it is published.</InlineNotice>
            ) : null}
            {comp.status === "completed" ? (
              <InlineNotice tone="success" title="Final results are published" action={<LinkButton href={`${base}/leaderboard`} size="sm" variant="secondary">View results</LinkButton>}>
                {comp.finalized_at ? <>Results were finalized {relativeTime(comp.finalized_at)}.</> : null}
              </InlineNotice>
            ) : null}
          </div>
        ) : null}
      </Container>
    </section>
  );
}

/** Loading placeholder shaped like the hero (cover band, identity panel, action panel). */
export function CompetitionHeroSkeleton() {
  return (
    <div role="status" aria-label="Loading competition" className="relative -mt-14 lg:-mt-[4.25rem]">
      <div className="skeleton h-[17rem] w-full rounded-none opacity-60 sm:h-[20rem] lg:h-[23rem]" />
      <Container className="relative -mt-44 sm:-mt-48 lg:-mt-52">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21.5rem]">
          <div className="rounded-[var(--radius-2xl)] border border-border bg-surface p-5 sm:p-7 lg:p-8">
            <div className="flex gap-1.5">
              {Array.from({ length: 4 }).map((_, i) => <div key={i} className="skeleton h-[22px] w-20 rounded-full" />)}
            </div>
            <div className="skeleton mt-5 h-10 w-3/4" />
            <div className="skeleton mt-4 h-4 w-1/2" />
            <div className="skeleton mt-6 h-5 w-56" />
            <div className="mt-6 grid grid-cols-2 gap-x-6 gap-y-5 border-t border-border pt-6 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i}>
                  <div className="skeleton h-2.5 w-16" />
                  <div className="skeleton mt-2.5 h-4 w-24" />
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-[var(--radius-2xl)] border border-border bg-surface p-5 sm:p-6">
            <div className="skeleton h-2.5 w-16" />
            <div className="mt-3 flex gap-1.5">
              {Array.from({ length: 3 }).map((_, i) => <div key={i} className="skeleton h-12 w-14 rounded-[var(--radius-md)]" />)}
            </div>
            <div className="skeleton mt-6 h-1.5 w-full rounded-full" />
            <div className="skeleton mt-8 h-11 w-full rounded-[var(--radius-md)]" />
          </div>
        </div>
        <div className="skeleton mt-8 h-11 w-full" />
      </Container>
    </div>
  );
}
