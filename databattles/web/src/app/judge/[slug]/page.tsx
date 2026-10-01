"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Gavel,
  Inbox,
  Lock,
  MapPin,
  Save,
  Send,
  ShieldAlert,
  Users,
  Video,
} from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { toCriteria, weightedTotal, type Criterion } from "@/components/organizer/judging";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/extras";
import { Field, FormError, Input, Textarea } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { ProgressBar } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, NotFoundState, PermissionDenied, Spinner } from "@/components/ui/states";
import { ApiError, post, put, get, type FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useApiMutation, useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";
import type { Message } from "@/lib/types";

import { ENTRY_STATE_LABEL, EntryStateIcon, StatePill, SuccessMark, WorkstationSkeleton } from "../_components/judge-ui";

interface QueueEntry {
  team_id: string;
  label: string;
  submission: {
    title: string;
    summary: string | null;
    description_html: string;
    repo_url: string | null;
    demo_url: string | null;
    video_url: string | null;
    submitted_at: string;
  } | null;
  my_score: { scores: Record<string, number>; feedback: string | null; status: string; weighted_total: number | null } | null;
  slot: { starts_at: string; ends_at: string; location: string | null; meeting_url: string | null } | null;
}

interface Queue {
  competition: { slug: string; title: string };
  rubric: unknown[];
  rubric_version: number;
  finalized: boolean;
  entries: QueueEntry[];
}

const queueKey = (slug: string) => ["judge", slug, "queue"] as const;

function entryState(e: QueueEntry): "submitted" | "draft" | "todo" {
  if (e.my_score?.status === "submitted") return "submitted";
  if (e.my_score) return "draft";
  return "todo";
}

function ScoreForm({ slug, entry, criteria, finalized, judgeId, onDirtyChange, rubricVersion, celebrate = false }: {
  slug: string;
  entry: QueueEntry;
  criteria: Criterion[];
  finalized: boolean;
  judgeId: string;
  onDirtyChange: (dirty: boolean) => void;
  /** Presentational: shown in the panel eyebrow. */
  rubricVersion: number;
  /** Presentational: true right after this entry's score was really submitted (plays the success mark once). */
  celebrate?: boolean;
}) {
  const qc = useQueryClient();
  const initialValues = useMemo(() => {
    const out: Record<string, string> = {};
    for (const c of criteria) {
      const v = entry.my_score?.scores?.[c.key];
      out[c.key] = v === undefined || v === null ? "" : String(v);
    }
    return out;
  }, [criteria, entry.my_score]);
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [feedback, setFeedback] = useState(entry.my_score?.feedback ?? "");
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = JSON.stringify(values) !== JSON.stringify(initialValues) || feedback !== (entry.my_score?.feedback ?? "");
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useUnsavedChangesWarning(dirty);

  const parsed: Record<string, number | undefined> = {};
  const clientErrors: FieldErrors = {};
  for (const c of criteria) {
    const raw = values[c.key]?.trim() ?? "";
    if (raw === "") continue;
    const n = Number(raw);
    if (Number.isNaN(n)) clientErrors[c.key] = "Enter a number.";
    else if (n < c.min || n > c.max) clientErrors[c.key] = `Must be between ${c.min} and ${c.max}.`;
    else if (!c.allow_decimal && !Number.isInteger(n)) clientErrors[c.key] = "Whole numbers only.";
    else parsed[c.key] = n;
  }
  const complete = criteria.every((c) => parsed[c.key] !== undefined);
  const total = weightedTotal(criteria, parsed);
  const errors = { ...clientErrors, ...serverErrors };
  const hasClientErrors = Object.keys(clientErrors).length > 0;

  const save = useApiMutation(
    (submit: boolean) => {
      const scores: Record<string, number> = {};
      for (const [k, v] of Object.entries(parsed)) if (v !== undefined) scores[k] = v;
      return put<Message>(`/competitions/${slug}/judging/scores/${entry.team_id}`, { scores, feedback: feedback.trim() || null, submit });
    },
    {
      success: (m) => m.message,
      invalidate: [queueKey(slug), ["judge", "events"]],
      onSuccess: () => {
        setServerErrors({});
        setFormError(null);
      },
      onError: (e) => {
        setServerErrors(e.fields);
        if (!Object.keys(e.fields).length) setFormError(e.message);
      },
    },
  );
  const recuse = useApiMutation(
    (reason: string) => post<Message>(`/competitions/${slug}/judging/conflicts`, { judge_id: judgeId, team_id: entry.team_id, reason }),
    {
      success: "Conflict declared. This entry has been removed from your queue.",
      onSuccess: () => qc.invalidateQueries({ queryKey: queueKey(slug) }),
    },
  );
  const submitted = entry.my_score?.status === "submitted";

  // Presentational only.
  const totalWeight = criteria.reduce((n, c) => n + c.weight, 0);
  const scoredCount = criteria.filter((c) => parsed[c.key] !== undefined).length;

  return (
    <Card
      role="region"
      aria-labelledby="score-panel-title"
      className={cn(
        "@container flex flex-col xl:max-h-[calc(100dvh-6.5rem)]",
        submitted && !dirty && "border-[color-mix(in_oklab,var(--success)_28%,var(--border))]",
      )}
    >
      {/* Header: state, title, live weighted total */}
      <div className="shrink-0 border-b border-border px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id="score-panel-title" className="text-base font-semibold tracking-[-0.015em] text-fg">Your score</h2>
            <p className="mt-0.5 text-xs leading-relaxed text-muted">
              {finalized ? "Judging is finalized — scores are locked." : submitted ? "Submitted. You can still update it until judging is finalized." : "Drafts are private and don't count until you submit."}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <StatePill state={entryState(entry)} celebrate={celebrate} />
            {dirty ? (
              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-warning">
                <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                Unsaved changes
              </span>
            ) : null}
          </div>
        </div>
        <div className="mt-3 flex items-end gap-4 rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3.5 py-2.5">
          <div className="shrink-0">
            <p className="text-eyebrow text-subtle">Weighted total</p>
            <p className="tabular mt-1.5 text-[1.6rem] font-semibold leading-none tracking-[-0.03em] text-fg" aria-live="polite">
              {total === null ? "—" : total.toFixed(2)}
              <span className="text-sm font-normal tracking-normal text-subtle"> / 100</span>
            </p>
          </div>
          <div className="min-w-0 flex-1 pb-1">
            <p className="tabular mb-1.5 text-right font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">
              {scoredCount}/{criteria.length} scored<span className="hidden @sm:inline"> · rubric v{rubricVersion}</span>
            </p>
            <ProgressBar value={total ?? 0} label="Weighted total preview" />
          </div>
        </div>
      </div>

      {/* Body: criteria, feedback — scrolls inside the sticky panel on desktop */}
      <div className="min-h-0 flex-1 xl:overflow-y-auto">
        <ol className="divide-y divide-border">
          {criteria.map((c, i) => (
            <li key={c.key} className="px-5 py-3">
              <Field
                label={
                  <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <span className="tabular font-mono text-[11px] font-normal text-subtle">{String(i + 1).padStart(2, "0")}</span>
                    {c.label}
                    <span className="text-xs font-normal text-subtle">
                      {c.min}–{c.max}
                      {c.allow_decimal ? ", decimals allowed" : ", whole numbers"} · weight {formatNumber(c.weight, 2)}
                      {totalWeight > 0 ? ` (${Math.round((c.weight / totalWeight) * 100)}%)` : ""}
                    </span>
                  </span>
                }
                hint={c.description || undefined}
                error={errors[c.key]}
              >
                {(p) => (
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      aria-hidden
                      tabIndex={-1}
                      min={c.min}
                      max={c.max}
                      step={c.allow_decimal ? "any" : 1}
                      value={parsed[c.key] ?? c.min}
                      disabled={finalized}
                      onChange={(e) => setValues((v) => ({ ...v, [c.key]: e.target.value }))}
                      className={cn("hidden min-w-0 flex-1 accent-[var(--accent)] sm:block", parsed[c.key] === undefined && "opacity-40")}
                    />
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Input
                        {...p}
                        type="number"
                        inputMode={c.allow_decimal ? "decimal" : "numeric"}
                        min={c.min}
                        max={c.max}
                        step={c.allow_decimal ? "any" : 1}
                        value={values[c.key] ?? ""}
                        disabled={finalized}
                        onChange={(e) => {
                          setValues((v) => ({ ...v, [c.key]: e.target.value }));
                          if (serverErrors[c.key]) setServerErrors((s) => ({ ...s, [c.key]: "" }));
                        }}
                        className="w-24 text-right tabular-nums sm:w-20"
                      />
                      <span className="tabular w-8 text-xs text-subtle">/ {formatNumber(c.max, 2)}</span>
                    </div>
                  </div>
                )}
              </Field>
            </li>
          ))}
          <li className="space-y-4 px-5 py-3">
            <Field label="Feedback for the organizers" hint="Optional. Organizers may share it with the team. Up to 5,000 characters." error={errors.feedback}>
              {(p) => <Textarea {...p} rows={3} maxLength={5000} value={feedback} disabled={finalized} onChange={(e) => setFeedback(e.target.value)} />}
            </Field>
            <FormError message={formError} />
          </li>
        </ol>
      </div>

      {/* Footer: actions in reading order. In the narrow desktop panel and on phones the primary action takes its own row. */}
      {!finalized ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-b-[var(--radius-lg)] border-t border-border bg-bg-elevated/60 px-4 py-3">
          <ConfirmDialog
            trigger={<Button variant="ghost" size="sm" className="mr-auto h-9 sm:h-8" icon={<ShieldAlert className="h-4 w-4" />}>Declare a conflict</Button>}
            title="Declare a conflict of interest?"
            description="Use this if you know a team member personally or professionally, or have any other reason you can't judge impartially. The entry leaves your queue and any draft score is discarded. This can't be undone."
            confirmLabel="Declare conflict"
            requireReason
            reasonLabel="Reason (visible to organizers)"
            onConfirm={(reason) => recuse.mutateAsync(reason).catch(() => undefined)}
          />
          <Button variant="secondary" className="flex-1 @lg:flex-none" icon={<Save className="h-4 w-4" />} loading={save.isPending && save.variables === false} disabled={hasClientErrors || !dirty} onClick={() => save.mutate(false)}>
            Save draft
          </Button>
          <ConfirmDialog
            trigger={<Button className="w-full @lg:w-auto" icon={<Send className="h-4 w-4" />} loading={save.isPending && save.variables === true} disabled={!complete || hasClientErrors || (submitted && !dirty)}>{submitted ? "Update submitted score" : "Submit score"}</Button>}
            title={submitted ? "Update your submitted score?" : "Submit this score?"}
            description={`Weighted total ${total?.toFixed(2) ?? "—"} / 100. Submitted scores count toward results and lock the rubric. You can revise until the organizers finalize judging.`}
            confirmLabel={submitted ? "Update score" : "Submit score"}
            tone="primary"
            onConfirm={() => save.mutateAsync(true).catch(() => undefined)}
          />
        </div>
      ) : (
        <div className="flex shrink-0 items-center gap-2 rounded-b-[var(--radius-lg)] border-t border-border bg-bg-elevated/60 px-5 py-3 text-xs text-muted">
          <Lock className="h-3.5 w-3.5 shrink-0" aria-hidden /> Scores are locked for this event.
        </div>
      )}
    </Card>
  );
}

const LINKS = [
  ["Repository", "repo_url", ExternalLink],
  ["Live demo", "demo_url", ArrowUpRight],
  ["Video", "video_url", Video],
] as const;

function EntryDetails({ entry }: { entry: QueueEntry }) {
  const s = entry.submission;
  const st = entryState(entry);
  return (
    <Card className="overflow-hidden">
      <div className="relative px-5 pb-5 pt-5 sm:px-6">
        <div aria-hidden className="pointer-events-none absolute -right-16 -top-24 h-56 w-72 max-w-full opacity-70" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
        <div className="relative flex flex-wrap items-center gap-2">
          <p className="inline-flex min-w-0 items-center gap-1.5 text-eyebrow text-subtle">
            <Users className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="truncate">{entry.label}</span>
          </p>
          <span className="inline-flex items-center gap-1.5 text-xs text-muted">
            <EntryStateIcon state={st} className="h-3.5 w-3.5" />
            {ENTRY_STATE_LABEL[st]}
          </span>
        </div>
        {s ? (
          <>
            <h2 className="relative mt-2.5 break-words text-2xl font-semibold tracking-[-0.025em] text-fg">{s.title}</h2>
            {s.summary ? <p className="relative mt-2 max-w-prose text-[15px] leading-relaxed text-muted">{s.summary}</p> : null}
            {LINKS.some(([, key]) => s[key]) ? (
              <div className="relative mt-4 flex flex-wrap gap-2">
                {LINKS.map(([label, key, Icon]) => {
                  const url = s[key];
                  return url ? (
                    <a
                      key={label}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-md)] border border-border bg-surface-2 px-3 text-sm text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)] transition-colors hover:border-border-strong hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                    >
                      <Icon className="h-3.5 w-3.5 text-subtle" aria-hidden /> {label}
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  ) : null;
                })}
              </div>
            ) : null}
          </>
        ) : (
          <h2 className="relative mt-2.5 text-2xl font-semibold tracking-[-0.025em] text-fg">{entry.label}</h2>
        )}
      </div>

      <dl className="grid grid-cols-1 gap-px border-y border-border bg-border sm:grid-cols-2">
        <div className="min-w-0 bg-surface px-5 py-3 sm:px-6">
          <dt className="text-eyebrow text-subtle">Submitted</dt>
          <dd className="mt-1 text-sm text-fg">
            {s ? (
              <span className="flex flex-col">
                <span>{relativeTime(s.submitted_at)}</span>
                <span className="tabular text-xs text-subtle">{formatDateTime(s.submitted_at)}</span>
              </span>
            ) : (
              <span className="text-subtle">—</span>
            )}
          </dd>
        </div>
        <div className="min-w-0 bg-surface px-5 py-3 sm:px-6">
          <dt className="text-eyebrow text-subtle">Presentation</dt>
          <dd className="mt-1 text-sm text-fg">
            {entry.slot ? (
              <span className="flex flex-col gap-1">
                <span className="inline-flex items-center gap-1.5">
                  <CalendarClock className="h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden /> {formatDateTime(entry.slot.starts_at)} ({relativeTime(entry.slot.starts_at)})
                </span>
                {entry.slot.location ? (
                  <span className="inline-flex items-center gap-1.5"><MapPin className="h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden /> {entry.slot.location}</span>
                ) : null}
                {entry.slot.meeting_url ? (
                  <a href={entry.slot.meeting_url} target="_blank" rel="noopener noreferrer" className="w-fit text-accent-strong hover:underline">Join meeting</a>
                ) : null}
              </span>
            ) : (
              <span className="text-subtle">No slot scheduled</span>
            )}
          </dd>
        </div>
      </dl>

      <div className="px-5 py-5 sm:px-6">
        {s ? (
          s.description_html ? (
            <>
              <p className="mb-3 text-eyebrow text-subtle">Project write-up</p>
              <Prose html={s.description_html} className="text-sm" />
            </>
          ) : (
            <p className="text-sm text-subtle">The team didn't add a write-up.</p>
          )
        ) : (
          <InlineNotice tone="warning" title="No project submitted">This team hasn't submitted a project yet. Check with the organizers before scoring.</InlineNotice>
        )}
      </div>
    </Card>
  );
}

/** Conflict-of-interest guidance; shown in every state of the page. */
function ConflictNote({ className }: { className?: string }) {
  return (
    <p className={cn("flex items-start gap-2 text-xs leading-relaxed text-muted", className)}>
      <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
      <span>
        Conflict of interest: if you know a team member personally or professionally, declare a conflict instead of scoring. The entry leaves your queue and your score won&apos;t count.
      </span>
    </p>
  );
}

/** How totals are computed; shown in every state of the page. */
function ScoringFootnote({ className }: { className?: string }) {
  return (
    <p className={cn("text-[11px] leading-relaxed text-subtle", className)}>
      Scores are weighted per the rubric and scaled to 0–100 by the server; the total shown here is a preview using the same formula.
    </p>
  );
}

/** Moves focus between queue entries with the arrow keys (Enter/Space then opens the focused entry). */
function onQueueKeyDown(e: KeyboardEvent<HTMLUListElement>) {
  if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
  const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("button[data-entry]"));
  const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
  if (i === -1) return;
  e.preventDefault();
  const next =
    e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : e.key === "ArrowDown" || e.key === "ArrowRight" ? Math.min(buttons.length - 1, i + 1) : Math.max(0, i - 1);
  buttons[next]?.focus();
}

export default function JudgeQueuePage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const queue = useQuery({
    queryKey: queueKey(slug),
    queryFn: () => get<Queue>(`/competitions/${slug}/judging/queue`),
    enabled: !!me.data,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const criteria = useMemo(() => toCriteria(queue.data?.rubric ?? []), [queue.data?.rubric]);
  const entries = useMemo(() => queue.data?.entries ?? [], [queue.data?.entries]);

  useEffect(() => {
    if (!entries.length) return;
    if (!selected || !entries.some((e) => e.team_id === selected)) {
      setSelected((entries.find((e) => entryState(e) !== "submitted") ?? entries[0]).team_id);
    }
  }, [entries, selected]);

  // Presentational: detect a real transition of an entry to "submitted" in fetched data to play a one-off success mark.
  const [celebrate, setCelebrate] = useState<string | null>(null);
  const snapshot = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    const next = new Map(entries.map((e) => [e.team_id, `${entryState(e)}|${e.my_score?.weighted_total ?? ""}|${e.my_score?.feedback ?? ""}`]));
    const prev = snapshot.current;
    snapshot.current = next;
    if (!prev) return;
    for (const [id, sig] of next) {
      const before = prev.get(id);
      if (before !== undefined && before !== sig && sig.startsWith("submitted|")) {
        setCelebrate(id);
        return;
      }
    }
  }, [entries]);

  if (me.isPending || !me.data) return <Spinner />;
  if (queue.isPending) {
    return (
      <Container size="xl">
        <WorkstationSkeleton />
      </Container>
    );
  }
  if (queue.isError) {
    const e = queue.error;
    return (
      <Container className="py-12">
        {e instanceof ApiError && e.status === 403 ? (
          <PermissionDenied message="You're not a judge for this event. If you were just invited, ask the organizers to check your assignment." />
        ) : e instanceof ApiError && e.status === 404 ? (
          <NotFoundState what="event" />
        ) : (
          <ErrorState error={e} onRetry={() => queue.refetch()} />
        )}
      </Container>
    );
  }

  const q = queue.data;
  const submittedCount = entries.filter((e) => entryState(e) === "submitted").length;
  const draftCount = entries.filter((e) => entryState(e) === "draft").length;
  const todoCount = entries.length - submittedCount - draftCount;
  const current = entries.find((e) => e.team_id === selected) ?? null;
  const currentIndex = current ? entries.indexOf(current) : -1;
  const select = (id: string) => {
    if (id === selected) return;
    if (dirty && !window.confirm("You have unsaved changes for this entry. Discard them?")) return;
    setDirty(false);
    setCelebrate(null);
    setSelected(id);
  };

  return (
    <Container size="xl">
      <PageHeader
        className="pb-6"
        eyebrow="Judging"
        icon={<Gavel />}
        title={q.competition.title}
        description={`${formatNumber(submittedCount)} of ${formatNumber(entries.length)} assigned entries submitted · rubric v${q.rubric_version}`}
        actions={
          <>
            <LinkButton href="/judge" variant="ghost" size="sm" className="h-9 sm:h-8" icon={<ArrowLeft className="h-4 w-4" />}>All events</LinkButton>
            <LinkButton href={`/competitions/${slug}`} variant="secondary" size="sm" className="h-9 sm:h-8" icon={<ArrowUpRight className="h-4 w-4" />}>Competition page</LinkButton>
          </>
        }
      />

      {q.finalized ? (
        <div className="mb-6">
          <InlineNotice tone="info" title="Judging is finalized">Scores are locked. You can review what you submitted.</InlineNotice>
        </div>
      ) : null}

      {criteria.length === 0 || entries.length === 0 ? (
        <div className="animate-rise">
          {criteria.length === 0 ? (
            <EmptyState icon={<Lock />} title="The rubric isn't ready yet" description="The organizers haven't defined the scoring criteria. You'll be able to score entries once they do." />
          ) : (
            <EmptyState icon={<Inbox />} title="No entries assigned to you yet" description="Entries appear here once teams submit projects and the organizers assign them to you." />
          )}
          {/* The same guidance the populated workstation shows in its rail and under the score panel. */}
          <div className="mx-auto mt-6 max-w-2xl space-y-4">
            {entries.length > 0 ? (
              <div>
                <div className="mb-2 flex items-baseline justify-between gap-2">
                  <p className="text-eyebrow text-subtle">Judging progress</p>
                  <p className="tabular text-xs text-muted">
                    {formatNumber(submittedCount)} / {formatNumber(entries.length)} submitted
                  </p>
                </div>
                <ProgressBar value={(submittedCount / entries.length) * 100} label="Judging progress" />
              </div>
            ) : null}
            <ConflictNote />
            <ScoringFootnote className="border-t border-border pt-4" />
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[256px_minmax(0,1fr)] xl:grid-cols-[272px_minmax(0,1fr)_392px]">
          {/* Queue rail */}
          <nav aria-label="Entries" className="min-w-0 animate-rise lg:sticky lg:top-[5.5rem] lg:row-span-2 lg:self-start xl:row-span-1">
            <div className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-4 shadow-card">
              <div className="flex items-baseline justify-between gap-2">
                <p className="text-eyebrow text-subtle">Queue</p>
                <p className="tabular text-sm font-semibold text-fg">
                  {formatNumber(submittedCount)}
                  <span className="font-normal text-subtle"> / {formatNumber(entries.length)}</span>
                </p>
              </div>
              <ProgressBar className="mt-2.5" value={entries.length ? (submittedCount / entries.length) * 100 : 0} label="Judging progress" />
              <ul className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" aria-label="Queue summary">
                <li className="inline-flex items-center gap-1"><EntryStateIcon state="submitted" className="h-3.5 w-3.5" /><span className="tabular">{submittedCount}</span> submitted</li>
                <li className="inline-flex items-center gap-1"><EntryStateIcon state="draft" className="h-3.5 w-3.5" /><span className="tabular">{draftCount}</span> draft</li>
                <li className="inline-flex items-center gap-1"><EntryStateIcon state="todo" className="h-3.5 w-3.5" /><span className="tabular">{todoCount}</span> to do</li>
              </ul>
            </div>

            <ul
              onKeyDown={onQueueKeyDown}
              className="-mx-4 mt-3 flex snap-x scroll-px-4 gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:scroll-px-0 sm:px-0 lg:flex-col lg:gap-1 lg:overflow-visible"
            >
              {entries.map((e) => {
                const st = entryState(e);
                const active = e.team_id === selected;
                return (
                  <li key={e.team_id} className="shrink-0 snap-start">
                    <button
                      type="button"
                      data-entry={e.team_id}
                      onClick={() => select(e.team_id)}
                      aria-current={active ? "true" : undefined}
                      className={cn(
                        "relative flex w-full min-w-52 items-start gap-2.5 rounded-[var(--radius-md)] border px-3 py-2.5 text-left text-sm transition-[background-color,border-color] duration-200 lg:min-w-0",
                        "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)]",
                        active ? "border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] bg-accent-soft" : "border-border bg-surface/60 hover:border-border-strong hover:bg-surface-2 lg:border-transparent lg:bg-transparent",
                      )}
                    >
                      {active ? <span aria-hidden className="absolute inset-y-2 left-0 hidden w-0.5 rounded-full bg-brand lg:block" /> : null}
                      <EntryStateIcon state={st} className="mt-0.5" pop={celebrate === e.team_id} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-fg">{e.submission?.title ?? e.label}</span>
                        <span className="block truncate text-xs text-subtle">
                          {e.label} · {st === "submitted" ? "Submitted" : st === "draft" ? "Draft" : "Not started"}
                        </span>
                      </span>
                      {st === "submitted" && e.my_score?.weighted_total != null ? (
                        <span className="tabular mt-0.5 shrink-0 font-mono text-xs text-fg" title="Your submitted weighted total">
                          <span className="sr-only">, your weighted total </span>
                          {e.my_score.weighted_total.toFixed(1)}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>

            <p className="mt-3 hidden items-center gap-1.5 text-[11px] text-subtle lg:flex">
              <Kbd>↑</Kbd>
              <Kbd>↓</Kbd> move · <Kbd>Enter</Kbd> open
            </p>
            <ConflictNote className="mt-4 border-t border-border pt-4" />
          </nav>

          {current ? (
            <>
              {/* Selected entry */}
              <section aria-label="Selected entry" className="min-w-0 animate-rise [animation-delay:60ms]">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <p className="tabular whitespace-nowrap font-mono text-[11px] uppercase tracking-[0.14em] text-subtle">
                    Entry {currentIndex + 1} of {entries.length}
                  </p>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-9 gap-1 px-2.5 sm:h-8 sm:gap-2 sm:px-3"
                      icon={<ChevronLeft className="h-4 w-4" />}
                      disabled={currentIndex <= 0}
                      onClick={() => currentIndex > 0 && select(entries[currentIndex - 1].team_id)}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-9 gap-1 px-2.5 sm:h-8 sm:gap-2 sm:px-3"
                      disabled={currentIndex >= entries.length - 1}
                      onClick={() => currentIndex < entries.length - 1 && select(entries[currentIndex + 1].team_id)}
                    >
                      Next <ChevronRight className="h-4 w-4" aria-hidden />
                    </Button>
                  </div>
                </div>
                <EntryDetails entry={current} />
              </section>

              {/* Scoring panel */}
              <div className="min-w-0 animate-rise [animation-delay:120ms] lg:col-start-2 xl:sticky xl:top-[5.5rem] xl:col-start-3 xl:row-start-1 xl:self-start">
                <ScoreForm
                  key={`${current.team_id}-${current.my_score?.status ?? "none"}-${current.my_score?.weighted_total ?? ""}-${q.rubric_version}`}
                  slug={slug}
                  entry={current}
                  criteria={criteria}
                  finalized={q.finalized}
                  judgeId={me.data.id}
                  onDirtyChange={setDirty}
                  rubricVersion={q.rubric_version}
                  celebrate={celebrate === current.team_id}
                />
                {entryState(current) === "submitted" ? (
                  <p className="mt-3 flex items-center gap-2 text-xs text-subtle">
                    {celebrate === current.team_id ? <SuccessMark celebrate className="h-6 w-6" /> : null}
                    <Badge tone="success">Submitted</Badge> This score counts toward the results.
                  </p>
                ) : null}
                <ScoringFootnote className="mt-3" />
              </div>
            </>
          ) : null}
        </div>
      )}
    </Container>
  );
}
