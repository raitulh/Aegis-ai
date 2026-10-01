"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowUpToLine, Crown, History, Info, LocateFixed, Medal, Upload } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect } from "react";

import { ck, useCompetition } from "@/components/competition/context";
import { directionLabel } from "@/components/competition/labels";
import type { Snapshot } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { AvatarStack } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, formatScore, relativeTime, titleCase } from "@/lib/format";
import { qk } from "@/lib/query";
import type { CompetitionDetail, LeaderboardOut, LeaderboardRow } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 50;
const DEFAULTS = { page: "1", around: "" };

function RankCell({ rank }: { rank: number | null | undefined }) {
  if (rank === null || rank === undefined) return <span className="text-subtle">—</span>;
  if (rank <= 3) {
    const tone = rank === 1 ? "text-warning" : rank === 2 ? "text-muted" : "text-accent-strong";
    return (
      <span className="inline-flex items-center gap-1.5 font-semibold tabular-nums">
        {rank === 1 ? <Crown className={cn("h-4 w-4", tone)} aria-hidden /> : <Medal className={cn("h-4 w-4", tone)} aria-hidden />}
        {rank}
      </span>
    );
  }
  return <span className="tabular-nums text-fg">{rank}</span>;
}

function TeamCell({ row }: { row: LeaderboardRow }) {
  const members = row.members ?? [];
  const universities = Array.from(new Set(members.map((m) => m.university).filter(Boolean))) as string[];
  if (row.is_solo && members.length === 1) {
    return (
      <div className="min-w-0">
        <UserLink user={members[0]} size={24} />
        {universities.length ? <p className="mt-0.5 truncate pl-8 text-xs text-subtle">{universities[0]}</p> : null}
      </div>
    );
  }
  return (
    <div className="flex min-w-0 items-center gap-3">
      {members.length ? <AvatarStack people={members} max={4} size={24} /> : null}
      <div className="min-w-0">
        <p className="truncate font-medium text-fg">{row.team_name ?? "Team"}</p>
        {members.length ? (
          <p className="truncate text-xs text-subtle">
            {members.map((m, i) => (
              <span key={m.id}>
                {i ? ", " : ""}
                <Link href={`/u/${m.handle}`} className="hover:text-fg">{m.display_name}</Link>
              </span>
            ))}
            {universities.length ? <> · {universities.join(", ")}</> : null}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function labelTone(label: string): "warning" | "accent" | "info" | "neutral" {
  if (label === "Winner") return "warning";
  if (label === "Top 3") return "accent";
  if (label === "Top 10") return "info";
  return "neutral";
}

function LeaderboardTable({ lb, scoreLabel, showPublic }: { lb: LeaderboardOut; scoreLabel: string; showPublic: boolean }) {
  const scoresHidden = lb.visibility === "ranks_only" && !lb.is_final;
  const judged = lb.metric === "judged_score";
  return (
    <Table>
      <caption className="sr-only">
        Leaderboard ranked by {scoreLabel.toLowerCase()} ({directionLabel(lb.direction).toLowerCase()})
      </caption>
      <THead>
        <tr>
          <TH className="w-16">Rank</TH>
          <TH>{judged ? "Team" : "Team / participant"}</TH>
          {!scoresHidden ? <TH className="text-right">{scoreLabel}</TH> : null}
          {showPublic ? <TH className="hidden text-right md:table-cell">Public score</TH> : null}
          {!judged ? <TH className="hidden text-right sm:table-cell">Entries</TH> : <TH className="hidden text-right sm:table-cell">Judges</TH>}
          {!judged ? <TH className="hidden md:table-cell">Last scored</TH> : null}
          {lb.is_final ? <TH>Result</TH> : null}
        </tr>
      </THead>
      <TBody>
        {lb.rows.map((row) => (
          <TR
            key={row.team_id}
            id={row.is_viewer ? "lb-viewer-row" : undefined}
            aria-current={row.is_viewer ? "true" : undefined}
            className={cn(row.is_viewer && "bg-accent-soft hover:bg-accent-soft")}
          >
            <TD><RankCell rank={row.rank} /></TD>
            <TD className="max-w-[18rem] sm:max-w-md">
              <div className="flex items-center gap-2">
                <TeamCell row={row} />
                {row.is_viewer ? <Badge tone="accent" className="shrink-0">You</Badge> : null}
              </div>
            </TD>
            {!scoresHidden ? <TD className="text-right font-mono tabular-nums text-fg">{formatScore(row.score)}</TD> : null}
            {showPublic ? <TD className="hidden text-right font-mono tabular-nums text-muted md:table-cell">{formatScore(row.public_score)}</TD> : null}
            <TD className="hidden text-right tabular-nums text-muted sm:table-cell">{formatNumber(judged ? row.judge_count : row.entries)}</TD>
            {!judged ? (
              <TD className="hidden text-muted md:table-cell">
                {row.submitted_at ? <time dateTime={row.submitted_at} title={formatDateTime(row.submitted_at)}>{relativeTime(row.submitted_at)}</time> : "—"}
              </TD>
            ) : null}
            {lb.is_final ? <TD>{row.label ? <Badge tone={labelTone(row.label)}>{row.label}</Badge> : null}</TD> : null}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function SnapshotHistory({ slug }: { slug: string }) {
  const query = useQuery({
    queryKey: ck.resultsHistory(slug),
    queryFn: () => get<Snapshot[]>(`/competitions/${encodeURIComponent(slug)}/results/history`),
  });
  if (query.isPending || query.isError || !query.data.length) return null;
  return (
    <Card>
      <CardHeader
        title={<span className="flex items-center gap-2"><History className="h-4 w-4 text-muted" aria-hidden /> Results history</span>}
        description="Final results are frozen as snapshots. Corrections create a new version with a recorded reason; earlier versions are never modified."
      />
      <CardBody>
        <ol className="space-y-3">
          {query.data.map((s) => (
            <li key={s.version} className="flex flex-col gap-1 rounded-[var(--radius-md)] border border-border px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 font-medium text-fg">
                  Version {s.version}
                  <Badge tone={s.kind === "correction" ? "warning" : "accent"}>{titleCase(s.kind)}</Badge>
                  {s.is_current ? <Badge tone="success">Current</Badge> : null}
                </p>
                {s.reason ? <p className="mt-1 text-sm text-muted">Reason: {s.reason}</p> : null}
                <p className="mt-1 text-xs text-subtle">
                  {formatNumber(s.team_count)} teams ranked · rules v{s.rules_version}
                  {s.evaluator_version ? ` · evaluator ${s.evaluator_version}` : ""} · source {s.source}
                </p>
              </div>
              <time dateTime={s.created_at} className="shrink-0 text-xs text-subtle">{formatDateTime(s.created_at)}</time>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}

function RankingExplainer({ comp, lb }: { comp: CompetitionDetail; lb: LeaderboardOut }) {
  if (lb.metric === "judged_score") {
    return (
      <InlineNotice tone="info" title="How judged results work">
        Teams are ranked by the weighted average of judges&apos; rubric scores. Results appear here once the organizers finalize judging.
      </InlineNotice>
    );
  }
  return (
    <InlineNotice tone="info" title={lb.is_final ? "These are the final (private) results" : "This is the public leaderboard"}>
      {lb.is_final ? (
        <>
          Final ranks use the <strong>private</strong> part of the test set, which nobody saw during the competition. Each team&apos;s score comes from
          its selected final submissions (or its top {comp.final_selection_limit} by public score if none were selected). Ties go to the earlier submission.
        </>
      ) : (
        <>
          Ranks here use each team&apos;s best score on the <strong>public</strong> part of the test set. Final results are computed on a hidden
          private part using your selected final submissions — so ranks can change at the end. Don&apos;t over-fit to this board.
        </>
      )}
    </InlineNotice>
  );
}

function Leaderboard() {
  const comp = useCompetition();
  const slug = comp.slug;
  const [state, set] = useUrlState(DEFAULTS);
  const page = Math.max(1, Number.parseInt(state.page || "1", 10) || 1);
  const aroundMe = state.around === "me";
  const params = { page: aroundMe ? 1 : page, page_size: PAGE_SIZE, around_me: aroundMe || undefined };
  const live = comp.status === "active" && comp.lifecycle === "published";
  const query = useQuery({
    queryKey: qk.leaderboard(slug, params),
    queryFn: ({ signal }) => get<LeaderboardOut>(`/competitions/${encodeURIComponent(slug)}/leaderboard`, params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: live ? 60_000 : false,
  });

  const lb = query.data;
  const viewerOnPage = Boolean(lb?.rows.some((r) => r.is_viewer));
  useEffect(() => {
    if (aroundMe && viewerOnPage) document.getElementById("lb-viewer-row")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [aroundMe, viewerOnPage, query.dataUpdatedAt]);

  if (comp.scoring_mode === "none") {
    return <EmptyState title="No leaderboard" description="This event isn't scored, so there is no leaderboard." />;
  }
  if (query.isPending) return <SkeletonRows rows={8} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  const data = query.data;
  const judged = data.metric === "judged_score";
  const metricLabel = judged ? "Judged score" : comp.evaluation?.metric_label ?? data.metric ?? "Score";
  const scoreLabel = data.is_final ? (judged ? "Final score" : "Private score") : metricLabel;
  const showPublic = data.is_final && !judged && data.rows.some((r) => r.public_score !== null && r.public_score !== undefined);
  const vrow = data.viewer_row;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold text-fg">
            {data.is_final ? "Final results" : "Public leaderboard"}
            {data.is_final ? <Badge tone="success">Final · snapshot v{data.snapshot_version}</Badge> : <Badge tone="info">Live</Badge>}
          </h2>
          <p className="mt-1 text-sm text-muted">
            {judged ? "Judged by rubric" : <>Metric: <span className="font-medium text-fg">{metricLabel}</span> · {directionLabel(data.direction)}</>}
            {data.is_final && data.finalized_at ? <> · finalized {formatDateTime(data.finalized_at)}</> : null}
          </p>
          <p className="mt-0.5 text-xs text-subtle">
            Leaderboard rules v{data.rules_version}
            {data.evaluator_version && !judged ? ` · evaluator ${data.evaluator_version}` : ""} · {formatNumber(data.total)} {data.total === 1 ? "team" : "teams"} ranked
            {query.isFetching ? " · updating…" : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {vrow && !data.hidden_reason ? (
            aroundMe ? (
              <Button variant="secondary" size="sm" icon={<ArrowUpToLine className="h-4 w-4" aria-hidden />} onClick={() => set({ around: "", page: "1" })}>
                Back to top
              </Button>
            ) : (
              <Button variant="secondary" size="sm" icon={<LocateFixed className="h-4 w-4" aria-hidden />} onClick={() => set({ around: "me" })}>
                Jump to my team
              </Button>
            )
          ) : null}
          {comp.viewer.is_participant && comp.viewer.can_submit ? (
            <LinkButton href={`/competitions/${slug}/submissions`} size="sm" icon={<Upload className="h-4 w-4" aria-hidden />}>Submit</LinkButton>
          ) : null}
        </div>
      </div>

      {vrow ? (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-[var(--radius-lg)] border border-accent/40 bg-accent-soft px-4 py-3 text-sm">
          <span className="font-medium text-fg">Your {vrow.is_solo ? "entry" : `team · ${vrow.team_name ?? ""}`}</span>
          <span>
            Rank <strong className="tabular-nums text-fg">{vrow.rank ?? "hidden"}</strong>
            {vrow.rank ? <span className="text-muted"> of {formatNumber(data.total)}</span> : null}
          </span>
          {vrow.score !== null && vrow.score !== undefined ? (
            <span>
              {scoreLabel} <strong className="font-mono tabular-nums text-fg">{formatScore(vrow.score)}</strong>
            </span>
          ) : null}
          {vrow.label && data.is_final ? <Badge tone={labelTone(vrow.label)}>{vrow.label}</Badge> : null}
        </div>
      ) : null}

      <RankingExplainer comp={comp} lb={data} />

      {data.visibility === "ranks_only" && !data.is_final ? (
        <InlineNotice tone="warning" title="Scores are hidden">The organizers show only ranks until the final results are published.</InlineNotice>
      ) : null}

      {data.hidden_reason ? (
        <EmptyState icon={<Info className="h-5 w-5" />} title="Leaderboard not available yet" description={data.hidden_reason} />
      ) : data.rows.length === 0 ? (
        <EmptyState
          title={data.is_final ? "No ranked teams" : "No scored submissions yet"}
          description={data.is_final ? "No team had a valid scored submission." : "Be the first on the board — upload predictions to get a public score."}
          action={
            comp.viewer.is_participant && comp.viewer.can_submit ? (
              <LinkButton href={`/competitions/${slug}/submissions`}>Make a submission</LinkButton>
            ) : undefined
          }
        />
      ) : (
        <div className={cn(query.isPlaceholderData && "opacity-60 transition-opacity")}>
          <LeaderboardTable lb={data} scoreLabel={scoreLabel} showPublic={showPublic} />
          {aroundMe ? (
            <p className="mt-3 text-center text-xs text-subtle">Showing the teams around your position.</p>
          ) : (
            <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => set({ page: String(p) })} />
          )}
        </div>
      )}

      <SnapshotHistory slug={slug} />
    </div>
  );
}

export default function LeaderboardPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <Leaderboard />
    </Suspense>
  );
}
