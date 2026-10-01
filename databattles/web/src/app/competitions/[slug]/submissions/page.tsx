"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, FileSpreadsheet, History, LineChart as LineChartIcon, Loader2, Star, Upload, Users, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Block, PanelLabel } from "@/components/competition/block";
import { useCompetition } from "@/components/competition/context";
import { JoinAction } from "@/components/competition/join";
import { describeSubmitBlocker, directionLabel } from "@/components/competition/labels";
import { SubmissionPipeline } from "@/components/competition/pipeline";
import { ProjectSubmissionCard } from "@/components/competition/project-submission";
import type { ScorePoint, SubmissionIssue } from "@/components/competition/types";
import { LineChart } from "@/components/charts/charts";
import { UserLink } from "@/components/domain/cards";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input } from "@/components/ui/form";
import { FileDrop } from "@/components/ui/misc";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, Skeleton } from "@/components/ui/states";
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

// ----------------------------------------------------------------------------- quota

/** Today's real submission quota: used vs. the daily limit (segments for small limits, a bar otherwise). */
function QuotaMeter({ remaining, limit }: { remaining: number; limit: number }) {
  const used = Math.max(0, Math.min(limit, limit - remaining));
  return (
    <div className="flex items-center gap-2.5" aria-label={`${remaining} of ${limit} submissions left today`} role="img">
      {limit <= 12 ? (
        <span className="flex gap-1" aria-hidden>
          {Array.from({ length: limit }).map((_, i) => (
            <span key={i} className={cn("h-1.5 w-4 rounded-full transition-colors duration-500", i < used ? "bg-surface-3" : "bg-brand")} />
          ))}
        </span>
      ) : (
        <span className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-3" aria-hidden>
          <span className="block h-full rounded-full bg-brand" style={{ width: `${(remaining / limit) * 100}%` }} />
        </span>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- upload

function UploadCard({ comp, onUploaded }: { comp: CompetitionDetail; onUploaded?: (sub: SubmissionOut) => void }) {
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
      onUploaded?.(sub);
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
    <Card className="overflow-hidden">
      <CardHeader
        icon={<Upload />}
        title="New submission"
        description={ev ? <>CSV with columns <code className="font-mono text-fg">{ev.id_column}</code> and <code className="font-mono text-fg">{ev.target_column}</code> · scored by {ev.metric_label} ({directionLabel(ev.direction).toLowerCase()})</> : "Upload a predictions CSV."}
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

        <div className="relative">
          <FileDrop
            key={dropKey}
            accept=".csv"
            maxBytes={comp.max_submission_mb * 1024 * 1024}
            disabled={disabled || uploading}
            progress={progress}
            hint={`CSV up to ${comp.max_submission_mb} MB · validated before scoring`}
            onFile={(f) => {
              setFile(f);
              setKey(idempotencyKey());
              setError(null);
            }}
          />
        </div>
        {file ? (
          <div className="flex animate-slide-down items-center gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan-soft text-cyan">
              <FileSpreadsheet className="h-4 w-4" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-[13px] text-fg">
                <span className="sr-only">Selected: </span>
                {file.name}
              </p>
              <p className="tabular text-[11px] text-subtle">{formatBytes(file.size)} · ready to upload</p>
            </div>
          </div>
        ) : null}

        <Field label="Description" hint="Optional — what changed in this run? Visible to your team only." error={error?.fields.description}>
          {(p) => (
            <Input {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} placeholder="e.g. gradient boosting, 5-fold CV, tuned depth" disabled={disabled || uploading} />
          )}
        </Field>

        {error ? (
          issues.length || errBlockers.length ? (
            <div role="alert" className="animate-slide-down rounded-[var(--radius-md)] border border-danger/40 bg-danger-soft px-3 py-2.5 text-sm text-danger">
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
      </CardBody>
      <div className="flex flex-col gap-3 border-t border-border bg-bg-elevated/40 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-subtle">
          {remaining !== null && remaining !== undefined ? (
            <>
              <QuotaMeter remaining={remaining} limit={comp.daily_submission_limit} />
              <span>
                <span className="tabular font-medium text-muted">{formatNumber(remaining)}</span> of <span className="tabular">{formatNumber(comp.daily_submission_limit)}</span> left today · resets 00:00 UTC
              </span>
            </>
          ) : (
            <span>Daily limit: <span className="tabular">{formatNumber(comp.daily_submission_limit)}</span></span>
          )}
          {comp.total_submission_limit ? <span>· total limit <span className="tabular">{formatNumber(comp.total_submission_limit)}</span></span> : null}
        </div>
        <div className="flex gap-2">
          {uploading ? (
            <Button variant="ghost" onClick={() => abortRef.current?.abort()} icon={<X className="h-4 w-4" aria-hidden />}>Cancel upload</Button>
          ) : null}
          <Button onClick={submit} disabled={!file || disabled} loading={uploading} icon={<Upload className="h-4 w-4" aria-hidden />} className="flex-1 sm:flex-none">
            {uploading ? `Uploading ${Math.round((progress ?? 0) * 100)}%` : "Upload submission"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ----------------------------------------------------------------------------- latest submission

/** Restrained success moment: a pulse ring and a drawn check, shown only after a real transition to Scored. */
function ScoredMark({ celebrate }: { celebrate: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-inset ring-[color-mix(in_oklab,var(--success)_35%,transparent)]",
        celebrate && "animate-pulse-ring [animation-iteration-count:3]",
      )}
    >
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className={celebrate ? "animate-check" : undefined} />
      </svg>
    </span>
  );
}

function LatestSubmission({ sub, comp, celebrate }: { sub: SubmissionOut; comp: CompetitionDetail; celebrate: boolean }) {
  const pending = PENDING.has(sub.status);
  const failed = sub.status === "failed" || sub.status === "rejected";
  return (
    <section
      aria-labelledby="latest-heading"
      aria-live="polite"
      className={cn(
        "relative overflow-hidden rounded-[var(--radius-lg)] border bg-surface surface-sheen shadow-card",
        sub.status === "scored" ? "border-[color-mix(in_oklab,var(--success)_30%,var(--border))]" : failed ? "border-danger/30" : "border-border",
      )}
    >
      {celebrate ? (
        <div aria-hidden className="pointer-events-none absolute -top-20 left-1/2 h-40 w-[28rem] max-w-full -translate-x-1/2 animate-fade-in" style={{ background: "radial-gradient(closest-side, var(--success-soft), transparent)" }} />
      ) : null}
      <div className="relative flex flex-wrap items-start justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          <PanelLabel>Latest submission</PanelLabel>
          <h2 id="latest-heading" className="mt-1 truncate font-mono text-[13.5px] font-medium text-fg" title={sub.filename}>{sub.filename}</h2>
          <p className="mt-0.5 text-xs text-subtle">
            <time dateTime={sub.submitted_at} title={formatDateTime(sub.submitted_at)}>{relativeTime(sub.submitted_at)}</time>
            {" · "}
            <span className="tabular">{formatBytes(sub.size_bytes)}</span>
            {sub.row_count !== null && sub.row_count !== undefined ? <> · <span className="tabular">{formatNumber(sub.row_count)}</span> rows</> : null}
          </p>
        </div>
        <StatusBadge status={sub.status} className={celebrate ? "animate-pop" : undefined} />
      </div>
      <SubmissionPipeline key={sub.id} status={sub.status} className="relative px-3 pb-4 pt-5" />
      {sub.status === "scored" ? (
        <div className="relative flex items-center gap-3 border-t border-border bg-bg-elevated/40 px-5 py-3.5">
          <ScoredMark celebrate={celebrate} />
          <div className="min-w-0">
            <p className="text-eyebrow text-subtle">Public score{comp.evaluation ? ` · ${comp.evaluation.metric_label}` : ""}</p>
            <p className={cn("tabular font-mono text-xl font-semibold tracking-[-0.02em] text-fg", celebrate && "animate-pop")}>{formatScore(sub.public_score)}</p>
          </div>
          {celebrate ? <p className="ml-auto hidden text-xs font-medium text-success sm:block">Scored just now</p> : null}
        </div>
      ) : pending ? (
        <p className="relative flex items-center gap-2 border-t border-border bg-bg-elevated/40 px-5 py-3 text-xs text-info">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Refreshing automatically while it&apos;s processed…
        </p>
      ) : failed && sub.error_message ? (
        <p className="relative border-t border-border bg-danger-soft/50 px-5 py-3 text-xs text-danger">{sub.error_message}</p>
      ) : null}
    </section>
  );
}

// ----------------------------------------------------------------------------- score history

function ScoreHistory({ comp, team, submissions }: { comp: CompetitionDetail; team: TeamOut | null | undefined; submissions: SubmissionOut[] }) {
  const points = ((team?.score_history ?? []) as unknown as ScorePoint[]).filter((p) => typeof p.score === "number") as (ScorePoint & { score: number })[];
  const maximize = comp.evaluation?.direction !== "minimize";
  const best = points.length ? points.reduce((b, p) => (maximize ? (p.score > b ? p.score : b) : p.score < b ? p.score : b), points[0].score) : null;
  const selected = submissions.filter((s) => s.is_final_selected).length;
  const tiles = [
    { label: "Best public score", value: <span className="font-mono">{formatScore(best, 4)}</span>, hint: directionLabel(comp.evaluation?.direction) },
    { label: "Scored", value: formatNumber(points.length), hint: `${formatNumber(team?.submission_count ?? 0)} submitted in total` },
    { label: "Final picks", value: `${selected}/${comp.final_selection_limit}`, hint: "Selected for final ranking" },
    { label: "Left today", value: comp.viewer.submissions_remaining_today ?? "—", hint: `of ${formatNumber(comp.daily_submission_limit)} per day` },
  ];
  return (
    <div className="min-w-0 space-y-4">
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card">
        {tiles.map((t, i) => (
          <div key={t.label} className={cn("relative bg-surface px-4 py-3.5", i === 0 && "surface-sheen")}>
            <dt className="text-eyebrow text-subtle">{t.label}</dt>
            <dd className="tabular mt-2 text-[1.45rem] font-semibold leading-none tracking-[-0.03em] text-fg">{t.value}</dd>
            <dd className="mt-1.5 text-[11px] text-subtle">{t.hint}</dd>
          </div>
        ))}
      </dl>
      <Card>
        <CardHeader icon={<LineChartIcon />} title="Public score over time" description={comp.evaluation?.metric_label} />
        {/* Contains the chart's visually hidden data table so it can't widen the page on narrow screens. */}
        <CardBody className="relative overflow-hidden">
          {points.length >= 2 ? (
            <LineChart
              label={`${comp.evaluation?.metric_label ?? "Score"} of each scored submission over time`}
              data={points.map((p) => ({ x: p.submitted_at, y: p.score }))}
              height={180}
              yFormat={(v) => formatScore(v, 4)}
              xFormat={(x) => formatDate(String(x), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            />
          ) : (
            <div className="flex flex-col items-center gap-2 py-6 text-center">
              <span className="flex h-9 w-9 items-center justify-center rounded-full border border-dashed border-border-strong text-subtle">
                <LineChartIcon className="h-4 w-4" aria-hidden />
              </span>
              <p className="text-sm text-subtle">Your chart appears after two scored submissions.</p>
            </div>
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

function SubmissionsTable({ comp, items, celebrateId }: { comp: CompetitionDetail; items: SubmissionOut[]; celebrateId: string | null }) {
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
          <TH className="hidden sm:table-cell">Submitted</TH>
          <TH className="px-3 sm:px-4">File</TH>
          <TH className="px-3 sm:px-4">Status</TH>
          <TH className="px-3 text-right sm:px-4">Public score</TH>
          {showPrivate ? <TH className="text-right">Private score</TH> : null}
          <TH className="text-center">Final</TH>
          {/* `relative` keeps the visually hidden label inside the table's scroll container. */}
          <TH className="relative"><span className="sr-only">Actions</span></TH>
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
            <TR key={s.id} className={cn(s.is_final_selected && "bg-accent-soft/60 shadow-[inset_3px_0_0_var(--accent)]")}>
              <TD className="hidden whitespace-nowrap py-2.5 sm:table-cell">
                <time dateTime={s.submitted_at} title={formatDateTime(s.submitted_at)} className="text-fg">{relativeTime(s.submitted_at)}</time>
                <div className="tabular text-[11px] text-subtle">{formatDateTime(s.submitted_at)}</div>
              </TD>
              <TD className="max-w-[9.5rem] px-3 py-2.5 sm:max-w-xs sm:px-4">
                <p className="truncate font-mono text-[13px] text-fg" title={s.filename}>{s.filename}</p>
                <time dateTime={s.submitted_at} title={formatDateTime(s.submitted_at)} className="block text-[11px] text-muted sm:hidden">{relativeTime(s.submitted_at)}</time>
                {s.description ? <p className="line-clamp-2 text-xs text-muted">{s.description}</p> : null}
                <p className="tabular text-[11px] text-subtle">
                  {formatBytes(s.size_bytes)}
                  {s.row_count !== null && s.row_count !== undefined ? ` · ${formatNumber(s.row_count)} rows` : ""}
                  <span className="hidden sm:inline">
                    {` · sha ${s.sha256_prefix}`}
                    {s.evaluator_version ? ` · evaluator ${s.evaluator_version}` : ""}
                  </span>
                </p>
                {multiMember && s.submitter ? <UserLink user={s.submitter} size={16} className="mt-1 text-xs" /> : null}
              </TD>
              <TD className="px-3 py-2.5 sm:px-4">
                <div className="flex flex-wrap items-center gap-1">
                  <StatusBadge status={s.status} className={s.id === celebrateId ? "animate-pop" : undefined} />
                  {s.invalidated ? <Badge tone="danger" icon={<Ban className="h-3 w-3" aria-hidden />}>Invalidated</Badge> : null}
                </div>
                {s.invalidated && s.invalidation_reason ? <p className="mt-1 max-w-xs text-xs text-danger">{s.invalidation_reason}</p> : null}
                {s.status === "failed" || s.status === "rejected" ? <Issues sub={s} /> : null}
              </TD>
              <TD className="tabular px-3 py-2.5 text-right font-mono text-[13px] sm:px-4">
                <span className={cn(s.public_score === null || s.public_score === undefined ? "text-subtle" : "text-fg")}>{formatScore(s.public_score)}</span>
                {Object.keys(s.secondary_scores ?? {}).length ? (
                  <div className="mt-0.5 space-y-0.5 text-[11px] text-subtle">
                    {Object.entries(s.secondary_scores as Record<string, { public?: number | null; private?: number | null }>).map(([k, val]) => (
                      <div key={k}>{k} {formatScore(val?.public ?? null, 4)}</div>
                    ))}
                  </div>
                ) : null}
              </TD>
              {showPrivate ? <TD className="tabular py-2.5 text-right font-mono text-[13px] text-fg">{formatScore(s.private_score)}</TD> : null}
              <TD className="py-2.5 text-center">
                <Button
                  size="sm"
                  variant={s.is_final_selected ? "primary" : "outline"}
                  className="h-9 sm:h-8"
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
              <TD className="py-2.5 text-right">
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

function HistorySkeleton() {
  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" role="status" aria-label="Loading submissions">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 border-b border-border px-4 py-3.5 last:border-b-0" style={{ opacity: 1 - i * 0.1 }}>
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="h-3.5 flex-1" />
          <Skeleton className="h-[22px] w-16 rounded-full" />
          <Skeleton className="hidden h-3.5 w-16 sm:block" />
        </div>
      ))}
    </div>
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

  // Celebrate only a transition we actually observed: a submission seen as pending that is now scored.
  const seen = useRef(new Map<string, string>());
  const [celebrateId, setCelebrateId] = useState<string | null>(null);
  useEffect(() => {
    let fresh: string | null = null;
    for (const s of items) {
      const before = seen.current.get(s.id);
      if (before && PENDING.has(before) && s.status === "scored") fresh = s.id;
      seen.current.set(s.id, s.status);
    }
    if (fresh) setCelebrateId(fresh);
  }, [items]);

  const latest = page === 1 ? items[0] : undefined;

  return (
    <div className="space-y-12">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          <UploadCard comp={comp} onUploaded={(sub) => seen.current.set(sub.id, sub.status)} />
          {latest ? <LatestSubmission sub={latest} comp={comp} celebrate={latest.id === celebrateId} /> : null}
        </div>
        <ScoreHistory comp={comp} team={team.data} submissions={items} />
      </div>

      <Block
        id="history"
        eyebrow="Your runs"
        title="Submission history"
        icon={<History />}
        description={
          <>
            Select up to {comp.final_selection_limit} final {comp.final_selection_limit === 1 ? "submission" : "submissions"} for the private leaderboard.
            If you select none, your top {comp.final_selection_limit} by public score are used.
          </>
        }
        action={
          pendingCount ? (
            <p className="inline-flex items-center gap-1.5 rounded-full bg-info-soft px-2.5 py-1 text-xs font-medium text-info" role="status">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {pendingCount} in progress · refreshing automatically
            </p>
          ) : null
        }
      >
        {query.isPending ? (
          <HistorySkeleton />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Upload />}
            title="No submissions yet"
            description="Upload your first predictions file above. Invalid files are rejected with row-level feedback and don't use up your daily quota."
            action={<LinkButton href={`/competitions/${slug}/data`} variant="secondary">Get the data</LinkButton>}
          />
        ) : (
          <>
            <SubmissionsTable comp={comp} items={items} celebrateId={celebrateId} />
            <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
          </>
        )}
      </Block>
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
      <section aria-labelledby="submissions-join-heading">
        <h2 id="submissions-join-heading" className="sr-only">Submissions</h2>
        <EmptyState
          icon={<Users />}
          title="Join to submit"
          description="Only registered participants can submit. Join the competition to get started."
          action={<JoinAction comp={comp} />}
        />
      </section>
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
