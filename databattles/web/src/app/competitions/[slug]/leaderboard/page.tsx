"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowUpToLine, Crown, Gauge, GitCommitVertical, History, Info, ListOrdered, LocateFixed, Scale, Upload, Users } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect } from "react";

import { Block, PanelLabel } from "@/components/competition/block";
import { ck, useCompetition } from "@/components/competition/context";
import { directionLabel } from "@/components/competition/labels";
import type { Snapshot } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { Avatar, AvatarStack } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { RankBadge } from "@/components/ui/extras";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, Skeleton } from "@/components/ui/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, formatScore, relativeTime, titleCase } from "@/lib/format";
import { qk } from "@/lib/query";
import type { CompetitionDetail, LeaderboardOut, LeaderboardRow } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 50;
const DEFAULTS = { page: "1", around: "" };

function TeamCell({ row }: { row: LeaderboardRow }) {
  const members = row.members ?? [];
  const universities = Array.from(new Set(members.map((m) => m.university).filter(Boolean))) as string[];
  if (row.is_solo && members.length === 1) {
    return (
      <div className="min-w-0">
        <UserLink user={members[0]} size={24} className="font-medium" />
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

function rowName(row: LeaderboardRow): string {
  if (row.is_solo && row.members?.length === 1) return row.members[0].display_name;
  return row.team_name ?? "Team";
}

/** Top three of the current page as a podium (2 · 1 · 3). Purely a view of rows already in the table. */
function Podium({ rows, scoreLabel, scoresHidden, isFinal }: { rows: LeaderboardRow[]; scoreLabel: string; scoresHidden: boolean; isFinal: boolean }) {
  const top = rows.filter((r) => r.rank === 1 || r.rank === 2 || r.rank === 3).sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)).slice(0, 3);
  if (top.length < 2) return null;
  const order = top.length === 3 ? [top[1], top[0], top[2]] : top;
  return (
    <ol aria-label="Top of the leaderboard" className={cn("hidden items-end gap-3 sm:grid", top.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
      {order.map((r, i) => {
        const first = r.rank === 1;
        const members = r.members ?? [];
        const universities = Array.from(new Set(members.map((m) => m.university).filter(Boolean))) as string[];
        return (
          <li
            key={r.team_id}
            className={cn(
              "relative min-w-0 animate-rise overflow-hidden rounded-[var(--radius-xl)] border bg-surface surface-sheen p-4 shadow-card",
              first ? "border-[color-mix(in_oklab,var(--warning)_35%,var(--border))] pb-6 pt-6 shadow-elevated" : "border-border",
              r.is_viewer && "ring-1 ring-accent",
            )}
            style={{ animationDelay: `${i * 70}ms` }}
          >
            {first ? (
              <div aria-hidden className="pointer-events-none absolute inset-x-0 -top-16 h-32" style={{ background: "radial-gradient(closest-side, var(--warning-soft), transparent)" }} />
            ) : null}
            <div className="relative flex items-start justify-between gap-3">
              <RankBadge rank={r.rank} size="lg" />
              <div className="flex flex-wrap justify-end gap-1">
                {first ? <Crown className="h-4 w-4 text-warning" aria-hidden /> : null}
                {r.is_viewer ? <Badge tone="accent">You</Badge> : null}
                {isFinal && r.label ? <Badge tone={labelTone(r.label)}>{r.label}</Badge> : null}
              </div>
            </div>
            <div className="relative mt-4 flex min-w-0 items-center gap-2.5">
              {members.length > 1 ? <AvatarStack people={members} max={3} size={26} /> : members[0] ? <Avatar name={members[0].display_name} src={members[0].avatar_url} size={26} /> : null}
              <div className="min-w-0">
                <p className="truncate font-semibold tracking-[-0.01em] text-fg">{rowName(r)}</p>
                {universities.length ? <p className="truncate text-xs text-subtle">{universities.join(", ")}</p> : null}
              </div>
            </div>
            {!scoresHidden ? (
              <div className="relative mt-4 border-t border-border pt-3">
                <p className="text-eyebrow text-subtle">{scoreLabel}</p>
                <p className={cn("tabular mt-1 font-mono font-semibold tracking-[-0.02em] text-fg", first ? "text-2xl" : "text-xl")}>{formatScore(r.score)}</p>
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
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
          <TH className="w-12 px-3 sm:w-16 sm:px-4">Rank</TH>
          <TH className="px-3 sm:px-4">{judged ? "Team" : "Team / participant"}</TH>
          {!scoresHidden ? <TH className="px-3 text-right sm:px-4">{scoreLabel}</TH> : null}
          {showPublic ? <TH className="hidden text-right md:table-cell">Public score</TH> : null}
          {!judged ? <TH className="hidden text-right sm:table-cell">Entries</TH> : <TH className="hidden text-right sm:table-cell">Judges</TH>}
          {!judged ? <TH className="hidden md:table-cell">Last scored</TH> : null}
          {lb.is_final ? <TH className="hidden sm:table-cell">Result</TH> : null}
        </tr>
      </THead>
      <TBody>
        {lb.rows.map((row) => {
          const podium = row.rank === 1 || row.rank === 2 || row.rank === 3;
          return (
            <TR
              key={row.team_id}
              id={row.is_viewer ? "lb-viewer-row" : undefined}
              aria-current={row.is_viewer ? "true" : undefined}
              className={cn(
                row.is_viewer && "bg-accent-soft shadow-[inset_3px_0_0_var(--accent)] hover:bg-accent-soft",
              )}
            >
              <TD className="px-3 py-2 sm:px-4">
                <RankBadge rank={row.rank} size="sm" />
              </TD>
              <TD className="max-w-[11rem] px-3 py-2 sm:max-w-md sm:px-4">
                <div className="flex items-center gap-2">
                  <TeamCell row={row} />
                  {row.is_viewer ? <Badge tone="accent" className="shrink-0">You</Badge> : null}
                </div>
                {lb.is_final && row.label ? (
                  <div className="mt-1 sm:hidden"><Badge tone={labelTone(row.label)}>{row.label}</Badge></div>
                ) : null}
              </TD>
              {!scoresHidden ? (
                <TD className={cn("tabular whitespace-nowrap px-3 py-2 text-right font-mono text-[13px] text-fg sm:px-4", podium && "font-semibold")}>{formatScore(row.score)}</TD>
              ) : null}
              {showPublic ? <TD className="tabular hidden whitespace-nowrap py-2 text-right font-mono text-[13px] text-muted md:table-cell">{formatScore(row.public_score)}</TD> : null}
              <TD className="tabular hidden py-2 text-right text-muted sm:table-cell">{formatNumber(judged ? row.judge_count : row.entries)}</TD>
              {!judged ? (
                <TD className="hidden whitespace-nowrap py-2 text-xs text-muted md:table-cell">
                  {row.submitted_at ? <time dateTime={row.submitted_at} title={formatDateTime(row.submitted_at)}>{relativeTime(row.submitted_at)}</time> : "—"}
                </TD>
              ) : null}
              {lb.is_final ? <TD className="hidden py-2 sm:table-cell">{row.label ? <Badge tone={labelTone(row.label)}>{row.label}</Badge> : null}</TD> : null}
            </TR>
          );
        })}
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
    <Block
      id="results-history"
      eyebrow="Audit trail"
      title="Results history"
      icon={<History />}
      description="Final results are frozen as snapshots. Corrections create a new version with a recorded reason; earlier versions are never modified."
    >
      <ol className="relative space-y-3 pl-6">
        <span aria-hidden className="absolute bottom-3 left-[7px] top-3 w-px bg-border-strong" />
        {query.data.map((s) => (
          <li key={s.version} className="relative">
            <span
              aria-hidden
              className={cn(
                "absolute -left-6 top-4 flex h-[15px] w-[15px] items-center justify-center rounded-full border-2",
                s.is_current ? "border-transparent bg-accent text-accent-fg" : "border-border-strong bg-bg",
              )}
            >
              <GitCommitVertical className="h-2.5 w-2.5" />
            </span>
            <div className="flex flex-col gap-1 rounded-[var(--radius-lg)] border border-border bg-surface px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 font-medium text-fg">
                  Version <span className="tabular">{s.version}</span>
                  <Badge tone={s.kind === "correction" ? "warning" : "accent"}>{titleCase(s.kind)}</Badge>
                  {s.is_current ? <Badge tone="success">Current</Badge> : null}
                </p>
                {s.reason ? <p className="mt-1 text-sm text-muted">Reason: {s.reason}</p> : null}
                <p className="mt-1 text-xs text-subtle">
                  <span className="tabular">{formatNumber(s.team_count)}</span> teams ranked · rules v{s.rules_version}
                  {s.evaluator_version ? <> · evaluator <span className="font-mono">{s.evaluator_version}</span></> : ""} · source {s.source}
                </p>
              </div>
              <time dateTime={s.created_at} className="tabular shrink-0 text-xs text-subtle">{formatDateTime(s.created_at)}</time>
            </div>
          </li>
        ))}
      </ol>
    </Block>
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

function LeaderboardSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading leaderboard">
      <div>
        <Skeleton className="h-2.5 w-28" />
        <Skeleton className="mt-3 h-6 w-56" />
      </div>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-surface px-4 py-3.5">
            <Skeleton className="h-2.5 w-16" />
            <Skeleton className="mt-2.5 h-4 w-24" />
          </div>
        ))}
      </div>
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 border-b border-border px-4 py-3 last:border-b-0" style={{ opacity: 1 - i * 0.08 }}>
            <Skeleton className="h-6 w-6 rounded-full" />
            <Skeleton className="h-6 w-6 rounded-full" />
            <Skeleton className="h-3.5 flex-1" />
            <Skeleton className="h-3.5 w-20" />
          </div>
        ))}
      </div>
    </div>
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
    return <EmptyState icon={<ListOrdered />} title="No leaderboard" description="This event isn't scored, so there is no leaderboard." />;
  }
  if (query.isPending) return <LeaderboardSkeleton />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  const data = query.data;
  const judged = data.metric === "judged_score";
  const metricLabel = judged ? "Judged score" : comp.evaluation?.metric_label ?? data.metric ?? "Score";
  const scoreLabel = data.is_final ? (judged ? "Final score" : "Private score") : metricLabel;
  const showPublic = data.is_final && !judged && data.rows.some((r) => r.public_score !== null && r.public_score !== undefined);
  const vrow = data.viewer_row;
  const scoresHidden = data.visibility === "ranks_only" && !data.is_final;

  return (
    <div className="space-y-8">
      {/* Board header */}
      <div className="space-y-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <PanelLabel className="text-accent-strong">
              <ListOrdered aria-hidden /> {judged ? "Judged ranking" : data.is_final ? "Private split" : "Public split"}
            </PanelLabel>
            <h2 className="mt-1.5 flex flex-wrap items-center gap-2 text-xl font-semibold tracking-[-0.02em] text-fg sm:text-2xl">
              {data.is_final ? "Final results" : "Public leaderboard"}
              {data.is_final ? (
                <Badge tone="success">Final · snapshot v{data.snapshot_version}</Badge>
              ) : live ? (
                <Badge tone="success" icon={<span className="h-1.5 w-1.5 rounded-full bg-current live-dot" aria-hidden />}>Live</Badge>
              ) : (
                <Badge tone="info">Public</Badge>
              )}
            </h2>
            {/* Freshness is only claimed for boards that actually auto-refresh; others would read "updated now" forever. */}
            {(data.is_final && data.finalized_at) || (live && !data.is_final) ? (
              <p className="mt-1 text-xs text-subtle">
                {data.is_final && data.finalized_at ? <>Finalized {formatDateTime(data.finalized_at)}</> : null}
                {live && !data.is_final ? (
                  <>Refreshes every minute · {query.isFetching ? "updating…" : <>updated {relativeTime(new Date(query.dataUpdatedAt))}</>}</>
                ) : null}
              </p>
            ) : null}
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

        <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-4 [&>div]:bg-surface">
          <div className="min-w-0 px-4 py-3">
            <dt className="flex items-center gap-1.5 text-eyebrow text-subtle"><Gauge className="h-3.5 w-3.5" aria-hidden /> Metric</dt>
            <dd className="mt-1 truncate text-sm font-medium text-fg">{judged ? "Judged by rubric" : metricLabel}</dd>
          </div>
          <div className="min-w-0 px-4 py-3">
            <dt className="flex items-center gap-1.5 text-eyebrow text-subtle"><Scale className="h-3.5 w-3.5" aria-hidden /> Direction</dt>
            <dd className="mt-1 truncate text-sm font-medium text-fg">{directionLabel(data.direction)}</dd>
          </div>
          <div className="min-w-0 px-4 py-3">
            <dt className="flex items-center gap-1.5 text-eyebrow text-subtle"><Users className="h-3.5 w-3.5" aria-hidden /> Ranked</dt>
            <dd className="mt-1 truncate text-sm font-medium text-fg">
              <span className="tabular">{formatNumber(data.total)}</span> {data.total === 1 ? "team" : "teams"}
            </dd>
          </div>
          <div className="min-w-0 px-4 py-3">
            <dt className="flex items-center gap-1.5 text-eyebrow text-subtle"><GitCommitVertical className="h-3.5 w-3.5" aria-hidden /> Rules</dt>
            <dd className="mt-1 truncate text-sm font-medium text-fg">
              v{data.rules_version}
              {data.evaluator_version && !judged ? <span className="font-mono text-xs font-normal text-subtle"> · {data.evaluator_version}</span> : null}
            </dd>
          </div>
        </dl>
      </div>

      {vrow ? (
        <div className="relative flex flex-col gap-4 overflow-hidden rounded-[var(--radius-lg)] border border-[color-mix(in_oklab,var(--accent)_40%,var(--border))] bg-accent-soft px-4 py-3.5 sm:flex-row sm:items-center sm:gap-6">
          <div aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-brand" />
          <div className="flex min-w-0 items-center gap-3">
            <RankBadge rank={vrow.rank} size="lg" />
            <div className="min-w-0">
              <PanelLabel className="text-accent-strong">Your {vrow.is_solo ? "entry" : "team"}</PanelLabel>
              <p className="truncate font-semibold text-fg">{vrow.is_solo ? rowName(vrow) : vrow.team_name ?? ""}</p>
            </div>
          </div>
          <dl className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm sm:ml-auto">
            <div>
              <dt className="text-eyebrow text-subtle">Rank</dt>
              <dd className="mt-0.5 text-fg">
                <strong className="tabular">{vrow.rank ?? "hidden"}</strong>
                {vrow.rank ? <span className="tabular text-muted"> of {formatNumber(data.total)}</span> : null}
              </dd>
            </div>
            {vrow.score !== null && vrow.score !== undefined ? (
              <div>
                <dt className="text-eyebrow text-subtle">{scoreLabel}</dt>
                <dd className="tabular mt-0.5 font-mono font-semibold text-fg">{formatScore(vrow.score)}</dd>
              </div>
            ) : null}
            {vrow.label && data.is_final ? (
              <div>
                <dt className="sr-only">Result</dt>
                <dd><Badge tone={labelTone(vrow.label)} className="animate-pop">{vrow.label}</Badge></dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : null}

      <RankingExplainer comp={comp} lb={data} />

      {data.visibility === "ranks_only" && !data.is_final ? (
        <InlineNotice tone="warning" title="Scores are hidden">The organizers show only ranks until the final results are published.</InlineNotice>
      ) : null}

      {data.hidden_reason ? (
        <EmptyState icon={<Info />} title="Leaderboard not available yet" description={data.hidden_reason} />
      ) : data.rows.length === 0 ? (
        <EmptyState
          icon={<ListOrdered />}
          title={data.is_final ? "No ranked teams" : "No scored submissions yet"}
          description={data.is_final ? "No team had a valid scored submission." : "Be the first on the board — upload predictions to get a public score."}
          action={
            comp.viewer.is_participant && comp.viewer.can_submit ? (
              <LinkButton href={`/competitions/${slug}/submissions`}>Make a submission</LinkButton>
            ) : undefined
          }
        />
      ) : (
        <div className={cn("space-y-4", query.isPlaceholderData && "opacity-60 transition-opacity")}>
          {!aroundMe && page === 1 ? <Podium rows={data.rows} scoreLabel={scoreLabel} scoresHidden={scoresHidden} isFinal={data.is_final} /> : null}
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
    <Suspense fallback={<LeaderboardSkeleton />}>
      <Leaderboard />
    </Suspense>
  );
}
