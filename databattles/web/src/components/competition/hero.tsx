"use client";

import { BadgeCheck, CalendarClock, Gavel, Lock, Mail, Settings2, Trophy, Upload, Users } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Countdown, Cover } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { InlineNotice } from "@/components/ui/states";
import { compactNumber, formatDate, formatNumber, relativeTime } from "@/lib/format";
import type { CompetitionDetail } from "@/lib/types";
import { DateTime } from "./datetime";
import { JoinAction } from "./join";
import { FORMAT_LABELS, VISIBILITY_LABELS, enumLabel } from "./labels";

function Fact({ label, children, icon }: { label: string; children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="flex items-center gap-1.5 text-xs text-subtle">
        {icon}
        {label}
      </dt>
      <dd className="mt-1 text-sm font-medium text-fg">{children}</dd>
    </div>
  );
}

function TimerFact({ comp }: { comp: CompetitionDetail }) {
  const icon = <CalendarClock className="h-3.5 w-3.5" aria-hidden />;
  if (comp.status === "upcoming" && comp.starts_at) {
    return <Fact label="Starts in" icon={icon}><Countdown to={comp.starts_at} className="text-base font-semibold" /></Fact>;
  }
  if (comp.status === "active" && comp.ends_at) {
    return <Fact label="Ends in" icon={icon}><Countdown to={comp.ends_at} className="text-base font-semibold" /></Fact>;
  }
  if (comp.status === "completed" && comp.finalized_at) {
    return <Fact label="Results final" icon={icon}>{formatDate(comp.finalized_at)}</Fact>;
  }
  if (comp.ends_at) {
    return <Fact label="Ended" icon={icon}>{relativeTime(comp.ends_at)}</Fact>;
  }
  return null;
}

function ParticipantPanel({ comp }: { comp: CompetitionDetail }) {
  const v = comp.viewer;
  const base = `/competitions/${comp.slug}`;
  return (
    <div className="flex flex-col items-start gap-3">
      <Badge tone="success" icon={<BadgeCheck className="h-3.5 w-3.5" aria-hidden />}>You&apos;re participating</Badge>
      <p className="text-sm text-muted">
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
      <div className="flex flex-wrap gap-2">
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
          {formatNumber(v.submissions_remaining_today)} of {formatNumber(comp.daily_submission_limit)} submissions left today (resets 00:00 UTC)
        </p>
      ) : null}
    </div>
  );
}

export function CompetitionHero({ comp }: { comp: CompetitionDetail }) {
  const v = comp.viewer;
  const base = `/competitions/${comp.slug}`;
  const isOrganizer = v.roles.includes("organizer");
  const isJudge = v.roles.includes("judge");
  return (
    <div>
      <Cover style={comp.cover_style} className="h-32 sm:h-44">
        <div className="absolute inset-0 bg-gradient-to-b from-transparent to-[var(--bg)]" aria-hidden />
      </Cover>
      <Container className="relative -mt-20 sm:-mt-24">
        <div className="rounded-[var(--radius-xl)] border border-border bg-surface/95 p-5 shadow-card backdrop-blur sm:p-7">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0 flex-1">
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
              <h1 className="mt-3 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{comp.title}</h1>
              {comp.summary ? <p className="mt-2 max-w-3xl text-sm text-muted sm:text-base">{comp.summary}</p> : null}
              {comp.host ? (
                <p className="mt-3 text-sm text-muted">
                  Hosted by{" "}
                  <Link href={`/orgs/${comp.host.slug}`} className="font-medium text-fg hover:text-accent-strong">{comp.host.name}</Link>
                  {comp.host.verification_status === "verified" ? (
                    <BadgeCheck className="ml-1 inline h-4 w-4 text-success" aria-label="Verified organization" />
                  ) : null}
                </p>
              ) : (
                <p className="mt-3 text-sm text-muted">Community event</p>
              )}

              <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-5">
                <TimerFact comp={comp} />
                <Fact label="Starts"><DateTime value={comp.starts_at} eventTimeZone={comp.timezone} /></Fact>
                <Fact label="Ends"><DateTime value={comp.ends_at} eventTimeZone={comp.timezone} /></Fact>
                <Fact label={comp.team_max_size > 1 ? "Participants · teams" : "Participants"} icon={<Users className="h-3.5 w-3.5" aria-hidden />}>
                  {compactNumber(comp.participant_count)}
                  {comp.team_max_size > 1 ? <span className="text-muted"> · {compactNumber(comp.team_count)}</span> : null}
                </Fact>
                {comp.has_prize ? (
                  <Fact label="Prizes" icon={<Trophy className="h-3.5 w-3.5" aria-hidden />}>{comp.prize_summary ?? "See prizes"}</Fact>
                ) : null}
              </dl>
            </div>

            <div className="flex w-full shrink-0 flex-col gap-3 lg:w-72">
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
                <Link href={`${base}/team`} className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-strong hover:underline">
                  <Mail className="h-4 w-4" aria-hidden />
                  {v.pending_invitations === 1 ? "You have a team invitation" : `You have ${v.pending_invitations} team invitations`}
                </Link>
              ) : null}
            </div>
          </div>
        </div>

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
      </Container>
    </div>
  );
}
