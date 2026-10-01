"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Star, Upload, Users, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useCompetition } from "@/components/competition/context";
import { JoinAction } from "@/components/competition/join";
import { describeSubmitBlocker, directionLabel } from "@/components/competition/labels";
import { ProjectSubmissionCard } from "@/components/competition/project-submission";
import type { ScorePoint, SubmissionIssue } from "@/components/competition/types";
import { LineChart } from "@/components/charts/charts";
import { UserLink } from "@/components/domain/cards";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input } from "@/components/ui/form";
import { FileDrop } from "@/components/ui/misc";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { ApiError, get, idempotencyKey, post, upload } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatDate, formatDateTime, formatNumber, formatScore, relativeTime } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail, Page, SubmissionOut, TeamOut } from "@/lib/types";

const PAGE_SIZE = 20;
const PENDING = new Set(["queued", "validating", "scoring"]);

function nextUtcMidnight(): Date {
  const d = new Date();
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1));
}

// ----------------------------------------------------------------------------- upload

function UploadCard({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const qc = useQueryClient();
  const v = comp.viewer;
  const [file, setFile] = useState<File | null>(null);
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [dropKey, setDropKey] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const uploading = progress !== null;
  const remaining = v.submissions_remaining_today;
  const limitReached = remaining === 0;
  const blockers = v.submit_blockers;
  const disabled = !v.can_submit || limitReached;

  async function submit() {
    if (!file) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setProgress(0);
    const form = new FormData();
    form.append("file", file);
    if (description.trim()) form.append("description", description.trim());
    try {
      const sub = await upload<SubmissionOut>(`/competitions/${encodeURIComponent(slug)}/submissions`, form, {
        onProgress: setProgress,
        headers: { "Idempotency-Key": key },
        signal: controller.signal,
      });
      toast.success(`${sub.filename} received — validation and scoring have started.`);
      setFile(null);
      setKey("");
      setDescription("");
      setDropKey((k) => k + 1);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["competitions", slug, "submissions"] }),
        qc.invalidateQueries({ queryKey: qk.competition(slug) }),
      ]);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "upload_failed", "Upload failed. Please try again.", null, null);
      if (err.code !== "aborted") setError(err);
      else toast.message("Upload canceled.");
    } finally {
      setProgress(null);
      abortRef.current = null;
    }
  }

  const issues = (error?.details?.errors as SubmissionIssue[] | undefined) ?? [];
  const errBlockers = (error?.details?.blockers as string[] | undefined) ?? [];
  const resetsAt = typeof error?.details?.resets_at === "string" ? (error.details.resets_at as string) : null;
  const ev = comp.evaluation;

  return (
    <Card>
      <CardHeader
        title="New submission"
        description={ev ? <>CSV with columns <code className="font-mono">{ev.id_column}</code> and <code className="font-mono">{ev.target_column}</code> · scored by {ev.metric_label} ({directionLabel(ev.direction).toLowerCase()})</> : "Upload a predictions CSV."}
      />
      <CardBody className="space-y-4">
        {blockers.length ? (
          <InlineNotice tone="warning" title="You can't submit right now">
            <ul className="list-disc pl-5">
              {blockers.map((b) => (
                <li key={b}>
                  {describeSubmitBlocker(b, comp)}{" "}
                  {b === "team_required" || b === "team_too_small" ? (
                    <Link href={`/competitions/${slug}/team`} className="font-medium underline">Go to team</Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </InlineNotice>
        ) : limitReached ? (
          <InlineNotice tone="warning" title="Daily limit reached">
            You&apos;ve used all {comp.daily_submission_limit} submissions for today. The limit resets at {formatDateTime(nextUtcMidnight())} ({relativeTime(nextUtcMidnight())}).
          </InlineNotice>
        ) : null}

        <FileDrop
          key={dropKey}
          accept=".csv"
          maxBytes={comp.max_submission_mb * 1024 * 1024}
          disabled={disabled || uploading}
          progress={progress}
          hint={`CSV up to ${comp.max_submission_mb} MB`}
          onFile={(f) => {
            setFile(f);
            setKey(idempotencyKey());
            setError(null);
          }}
        />
        {file ? <p className="text-xs text-subtle">Selected: <span className="font-mono text-muted">{file.name}</span> · {formatBytes(file.size)}</p> : null}

        <Field label="Description" hint="Optional — what changed in this run? Visible to your team only." error={error?.fields.description}>
          {(p) => (
            <Input {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} placeholder="e.g. gradient boosting, 5-fold CV, tuned depth" disabled={disabled || uploading} />
          )}
        </Field>

        {error ? (
          issues.length || errBlockers.length ? (
            <div role="alert" className="rounded-[var(--radius-md)] border border-danger/40 bg-danger-soft px-3 py-2 text-sm text-danger">
              <p className="font-medium">{error.message}</p>
              <ul className="mt-1 list-disc pl-5">
                {issues.map((i, n) => <li key={n}>{i.row ? `Row ${i.row}: ` : ""}{i.message ?? i.code}</li>)}
                {errBlockers.map((b) => <li key={b}>{describeSubmitBlocker(b, comp)}</li>)}
              </ul>
            </div>
          ) : (
            <FormError message={resetsAt ? `${error.message} Next reset: ${formatDateTime(resetsAt)}.` : error.message} />
          )
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-subtle">
            {remaining !== null && remaining !== undefined ? (
              <>
                <span className="font-medium text-muted">{formatNumber(remaining)}</span> of {formatNumber(comp.daily_submission_limit)} left today · resets 00:00 UTC
              </>
            ) : (
              <>Daily limit: {formatNumber(comp.daily_submission_limit)}</>
            )}
            {comp.total_submission_limit ? <> · total limit {formatNumber(comp.total_submission_limit)}</> : null}
          </p>
          <div className="flex gap-2">
            {uploading ? (
              <Button variant="ghost" onClick={() => abortRef.current?.abort()} icon={<X className="h-4 w-4" aria-hidden />}>Cancel upload</Button>
            ) : null}
            <Button onClick={submit} disabled={!file || disabled} loading={uploading} icon={<Upload className="h-4 w-4" aria-hidden />}>
              {uploading ? `Uploading ${Math.round((progress ?? 0) * 100)}%` : "Upload submission"}
            </Button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

// ----------------------------------------------------------------------------- score history

function ScoreHistory({ comp, team, submissions }: { comp: CompetitionDetail; team: TeamOut | null | undefined; submissions: SubmissionOut[] }) {
  const points = ((team?.score_history ?? []) as unknown as ScorePoint[]).filter((p) => typeof p.score === "number") as (ScorePoint & { score: number })[];
  const maximize = comp.evaluation?.direction !== "minimize";
  const best = points.length ? points.reduce((b, p) => (maximize ? (p.score > b ? p.score : b) : p.score < b ? p.score : b), points[0].score) : null;
  const selected = submissions.filter((s) => s.is_final_selected).length;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat label="Best public score" value={<span className="font-mono text-xl">{formatScore(best, 4)}</span>} hint={directionLabel(comp.evaluation?.direction)} />
        <Stat label="Scored" value={formatNumber(points.length)} hint={`${formatNumber(team?.submission_count ?? 0)} submitted in total`} />
        <Stat label="Final picks" value={`${selected}/${comp.final_selection_limit}`} hint="Selected for final ranking" />
        <Stat
          label="Left today"
          value={comp.viewer.submissions_remaining_today ?? "—"}
          hint={`of ${formatNumber(comp.daily_submission_limit)} per day`}
        />
      </div>
      <Card>
        <CardHeader title="Public score over time" description={comp.evaluation?.metric_label} />
        <CardBody>
          {points.length >= 2 ? (
            <LineChart
              label={`${comp.evaluation?.metric_label ?? "Score"} of each scored submission over time`}
              data={points.map((p) => ({ x: p.submitted_at, y: p.score }))}
              height={180}
              yFormat={(v) => formatScore(v, 4)}
              xFormat={(x) => formatDate(String(x), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            />
          ) : (
            <p className="py-6 text-center text-sm text-subtle">Your chart appears after two scored submissions.</p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------------------- history table

function Issues({ sub }: { sub: SubmissionOut }) {
  const details = (sub.error_details ?? []) as SubmissionIssue[];
  if (!sub.error_message && !details.length) return null;
  return (
    <div className="mt-1.5 max-w-sm text-xs">
      {sub.error_message ? <p className="text-danger">{sub.error_message}</p> : null}
      {details.length ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-muted hover:text-fg">
            {details.length === 1 ? "1 issue" : `${formatNumber(details.length)} issues`} found
          </summary>
          <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto rounded-md border border-border bg-bg-elevated p-2 font-mono text-[11px] text-muted">
            {details.map((d, i) => (
              <li key={i}>
                {d.row !== undefined ? <span className="text-fg">row {d.row}: </span> : null}
                {d.message ?? d.code}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function SubmissionsTable({ comp, items }: { comp: CompetitionDetail; items: SubmissionOut[] }) {
  const slug = comp.slug;
  const listKey = ["competitions", slug, "submissions"] as const;
  const select = useApiMutation(
    ({ id, selected }: { id: string; selected: boolean }) =>
      post<SubmissionOut>(`/competitions/${encodeURIComponent(slug)}/submissions/${id}/select`, { selected }),
    { success: (s) => (s.is_final_selected ? "Selected as a final submission" : "Removed from final selection"), invalidate: [listKey] },
  );
  const cancel = useApiMutation((id: string) => post<SubmissionOut>(`/submissions/${id}/cancel`), {
    success: "Submission canceled",
    invalidate: [listKey, qk.competition(slug)],
  });
  const showPrivate = items.some((s) => s.private_score !== null && s.private_score !== undefined);
  const selectedCount = items.filter((s) => s.is_final_selected).length;
  const selectionOpen = comp.lifecycle === "published";
  const multiMember = (comp.viewer.team?.member_count ?? 1) > 1;

  return (
    <Table>
      <caption className="sr-only">Your team&apos;s submissions, newest first</caption>
      <THead>
        <tr>
          <TH>Submitted</TH>
          <TH>File</TH>
          <TH>Status</TH>
          <TH className="text-right">Public score</TH>
          {showPrivate ? <TH className="text-right">Private score</TH> : null}
          <TH className="text-center">Final</TH>
          <TH><span className="sr-only">Actions</span></TH>
        </tr>
      </THead>
      <TBody>
        {items.map((s) => {
          const selectable = selectionOpen && s.status === "scored" && !s.invalidated && (s.is_final_selected || selectedCount < comp.final_selection_limit);
          const whyNot = !selectionOpen
            ? "Final selections are locked"
            : s.status !== "scored"
              ? "Only scored submissions can be selected"
              : s.invalidated
                ? "Invalidated submissions can't be selected"
                : !s.is_final_selected && selectedCount >= comp.final_selection_limit
                  ? `You can select at most ${comp.final_selection_limit}`
                  : undefined;
          const busy = (select.isPending && select.variables?.id === s.id) || (cancel.isPending && cancel.variables === s.id);
          return (
            <TR key={s.id} className={cn(s.is_final_selected && "bg-accent-soft/60")}>
              <TD className="whitespace-nowrap">
                <time dateTime={s.submitted_at} title={formatDateTime(s.submitted_at)} className="text-fg">{relativeTime(s.submitted_at)}</time>
                <div className="text-xs text-subtle">{formatDateTime(s.submitted_at)}</div>
              </TD>
              <TD className="max-w-xs">
                <p className="truncate font-mono text-[13px] text-fg" title={s.filename}>{s.filename}</p>
                {s.description ? <p className="line-clamp-2 text-xs text-muted">{s.description}</p> : null}
                <p className="text-[11px] text-subtle">
                  {formatBytes(s.size_bytes)}
                  {s.row_count !== null && s.row_count !== undefined ? ` · ${formatNumber(s.row_count)} rows` : ""} · sha {s.sha256_prefix}
                  {s.evaluator_version ? ` · evaluator ${s.evaluator_version}` : ""}
                </p>
                {multiMember && s.submitter ? <UserLink user={s.submitter} size={16} className="mt-1 text-xs" /> : null}
              </TD>
              <TD>
                <div className="flex flex-wrap items-center gap-1">
                  <StatusBadge status={s.status} />
                  {s.invalidated ? <Badge tone="danger" icon={<Ban className="h-3 w-3" aria-hidden />}>Invalidated</Badge> : null}
                </div>
                {s.invalidated && s.invalidation_reason ? <p className="mt-1 max-w-xs text-xs text-danger">{s.invalidation_reason}</p> : null}
                {s.status === "failed" || s.status === "rejected" ? <Issues sub={s} /> : null}
              </TD>
              <TD className="text-right font-mono tabular-nums">
                <span className={cn(s.public_score === null || s.public_score === undefined ? "text-subtle" : "text-fg")}>{formatScore(s.public_score)}</span>
                {Object.keys(s.secondary_scores ?? {}).length ? (
                  <div className="mt-0.5 space-y-0.5 text-[11px] text-subtle">
                    {Object.entries(s.secondary_scores as Record<string, { public?: number | null; private?: number | null }>).map(([k, val]) => (
                      <div key={k}>{k} {formatScore(val?.public ?? null, 4)}</div>
                    ))}
                  </div>
                ) : null}
              </TD>
              {showPrivate ? <TD className="text-right font-mono tabular-nums text-fg">{formatScore(s.private_score)}</TD> : null}
              <TD className="text-center">
                <Button
                  size="sm"
                  variant={s.is_final_selected ? "primary" : "outline"}
                  aria-pressed={s.is_final_selected}
                  aria-label={s.is_final_selected ? `Unselect ${s.filename} as final` : `Select ${s.filename} as final`}
                  title={whyNot}
                  disabled={!selectable || busy}
                  loading={select.isPending && select.variables?.id === s.id}
                  icon={<Star className={cn("h-3.5 w-3.5", s.is_final_selected && "fill-current")} aria-hidden />}
                  onClick={() => select.mutate({ id: s.id, selected: !s.is_final_selected })}
                >
                  <span className="hidden xl:inline">{s.is_final_selected ? "Final" : "Select"}</span>
                </Button>
              </TD>
              <TD className="text-right">
                {s.status === "queued" ? (
                  <ConfirmDialog
                    trigger={<Button size="sm" variant="ghost" disabled={busy}>Cancel</Button>}
                    title="Cancel this submission?"
                    description="It won't be scored and won't count toward your daily limit."
                    confirmLabel="Cancel submission"
                    onConfirm={() => cancel.mutateAsync(s.id).then(() => undefined, () => undefined)}
                  />
                ) : null}
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}

// ----------------------------------------------------------------------------- page

function AutomaticSubmissions({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: qk.submissions(slug, { page }),
    queryFn: ({ signal }) => get<Page<SubmissionOut>>(`/competitions/${encodeURIComponent(slug)}/submissions`, { page, page_size: PAGE_SIZE }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some((s) => PENDING.has(s.status)) ? 3000 : false),
  });
  const team = useQuery({
    queryKey: qk.team(slug),
    queryFn: () => get<TeamOut | null>(`/competitions/${encodeURIComponent(slug)}/team`),
  });

  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const pendingCount = items.filter((s) => PENDING.has(s.status)).length;

  // When scoring finishes, refresh dependent views (score history, leaderboard, remaining quota).
  const hadPending = useRef(false);
  useEffect(() => {
    if (hadPending.current && pendingCount === 0) {
      qc.invalidateQueries({ queryKey: qk.team(slug) });
      qc.invalidateQueries({ queryKey: ["competitions", slug, "leaderboard"] });
      qc.invalidateQueries({ queryKey: qk.competition(slug) });
    }
    hadPending.current = pendingCount > 0;
  }, [pendingCount, qc, slug]);

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <UploadCard comp={comp} />
        <ScoreHistory comp={comp} team={team.data} submissions={items} />
      </div>

      <section aria-labelledby="history-heading">
        <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 id="history-heading" className="text-lg font-semibold text-fg">Submission history</h2>
            <p className="text-sm text-muted">
              Select up to {comp.final_selection_limit} final {comp.final_selection_limit === 1 ? "submission" : "submissions"} for the private leaderboard.
              If you select none, your top {comp.final_selection_limit} by public score are used.
            </p>
          </div>
          {pendingCount ? (
            <p className="text-sm text-info" role="status">{pendingCount} in progress · refreshing automatically</p>
          ) : null}
        </div>
        {query.isPending ? (
          <SkeletonRows rows={5} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Upload className="h-5 w-5" />}
            title="No submissions yet"
            description="Upload your first predictions file above. Invalid files are rejected with row-level feedback and don't use up your daily quota."
            action={<LinkButton href={`/competitions/${slug}/data`} variant="secondary">Get the data</LinkButton>}
          />
        ) : (
          <>
            <SubmissionsTable comp={comp} items={items} />
            <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
          </>
        )}
      </section>
    </div>
  );
}

export default function SubmissionsPage() {
  const comp = useCompetition();
  if (comp.scoring_mode === "none") {
    return <EmptyState title="No submissions for this event" description="This event is participation-only, so there's nothing to submit." />;
  }
  if (!comp.viewer.is_participant) {
    return (
      <EmptyState
        icon={<Users className="h-5 w-5" />}
        title="Join to submit"
        description="Only registered participants can submit. Join the competition to get started."
        action={<JoinAction comp={comp} />}
      />
    );
  }
  if (comp.scoring_mode === "judged") return <ProjectSubmissionCard comp={comp} />;
  return (
    <>
      {comp.status === "ended" ? (
        <div className="mb-6">
          <InlineNotice tone="info" title="The competition has ended">
            You can still review your submissions and change final selections until the organizers finalize results.
          </InlineNotice>
        </div>
      ) : null}
      {comp.evaluation === null ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Scoring isn't configured yet">
            <span className="inline-flex items-center gap-1"><AlertTriangle className="h-3.5 w-3.5" aria-hidden /> The organizers are still setting up evaluation.</span>
          </InlineNotice>
        </div>
      ) : null}
      <AutomaticSubmissions comp={comp} />
    </>
  );
}
