"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, Circle, ExternalLink, Lock, MapPin, PencilLine, Save, Send, ShieldAlert } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { toCriteria, weightedTotal, type Criterion } from "@/components/organizer/judging";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Textarea } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { ProgressBar } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, NotFoundState, PermissionDenied, SkeletonRows, Spinner } from "@/components/ui/states";
import { ApiError, post, put, get, type FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useApiMutation, useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";
import type { Message } from "@/lib/types";

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

function ScoreForm({ slug, entry, criteria, finalized, judgeId, onDirtyChange }: {
  slug: string;
  entry: QueueEntry;
  criteria: Criterion[];
  finalized: boolean;
  judgeId: string;
  onDirtyChange: (dirty: boolean) => void;
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

  return (
    <Card>
      <CardHeader
        title="Your score"
        description={finalized ? "Judging is finalized — scores are locked." : submitted ? "Submitted. You can still update it until judging is finalized." : "Drafts are private and don't count until you submit."}
        action={
          <div className="text-right">
            <p className="text-xs text-subtle">Weighted total</p>
            <p className="text-2xl font-semibold tabular-nums text-fg" aria-live="polite">
              {total === null ? "—" : total.toFixed(2)}
              <span className="text-sm font-normal text-subtle"> / 100</span>
            </p>
          </div>
        }
      />
      <CardBody className="space-y-5">
        {criteria.map((c) => (
          <Field
            key={c.key}
            label={
              <span className="flex flex-wrap items-baseline gap-x-2">
                {c.label}
                <span className="text-xs font-normal text-subtle">
                  {c.min}–{c.max}
                  {c.allow_decimal ? ", decimals allowed" : ", whole numbers"} · weight {formatNumber(c.weight, 2)}
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
                  className="hidden flex-1 accent-[var(--accent)] sm:block"
                />
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
                  className="w-28 tabular-nums"
                />
              </div>
            )}
          </Field>
        ))}
        <Field label="Feedback for the organizers" hint="Optional. Organizers may share it with the team. Up to 5,000 characters." error={errors.feedback}>
          {(p) => <Textarea {...p} rows={4} maxLength={5000} value={feedback} disabled={finalized} onChange={(e) => setFeedback(e.target.value)} />}
        </Field>
        <FormError message={formError} />
      </CardBody>
      {!finalized ? (
        <CardFooter className="flex-wrap justify-between">
          <ConfirmDialog
            trigger={<Button variant="ghost" size="sm" icon={<ShieldAlert className="h-4 w-4" />}>Declare a conflict</Button>}
            title="Declare a conflict of interest?"
            description="Use this if you know a team member personally or professionally, or have any other reason you can't judge impartially. The entry leaves your queue and any draft score is discarded. This can't be undone."
            confirmLabel="Declare conflict"
            requireReason
            reasonLabel="Reason (visible to organizers)"
            onConfirm={(reason) => recuse.mutateAsync(reason).catch(() => undefined)}
          />
          <div className="flex gap-2">
            <Button variant="secondary" icon={<Save className="h-4 w-4" />} loading={save.isPending && save.variables === false} disabled={hasClientErrors || !dirty} onClick={() => save.mutate(false)}>
              Save draft
            </Button>
            <ConfirmDialog
              trigger={<Button icon={<Send className="h-4 w-4" />} loading={save.isPending && save.variables === true} disabled={!complete || hasClientErrors || (submitted && !dirty)}>{submitted ? "Update submitted score" : "Submit score"}</Button>}
              title={submitted ? "Update your submitted score?" : "Submit this score?"}
              description={`Weighted total ${total?.toFixed(2) ?? "—"} / 100. Submitted scores count toward results and lock the rubric. You can revise until the organizers finalize judging.`}
              confirmLabel={submitted ? "Update score" : "Submit score"}
              tone="primary"
              onConfirm={() => save.mutateAsync(true).catch(() => undefined)}
            />
          </div>
        </CardFooter>
      ) : null}
    </Card>
  );
}

function EntryDetails({ entry }: { entry: QueueEntry }) {
  const s = entry.submission;
  return (
    <Card>
      <CardBody className="space-y-3">
        <p className="text-xs font-medium uppercase tracking-wider text-subtle">{entry.label}</p>
        {s ? (
          <>
            <h2 className="text-lg font-semibold text-fg">{s.title}</h2>
            {s.summary ? <p className="text-sm text-muted">{s.summary}</p> : null}
            <div className="flex flex-wrap gap-2">
              {([["Repository", s.repo_url], ["Live demo", s.demo_url], ["Video", s.video_url]] as const).map(([label, url]) =>
                url ? (
                  <a key={label} href={url} target="_blank" rel="noopener noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-md)] border border-border px-3 text-sm text-fg hover:bg-surface-2">
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden /> {label}
                  </a>
                ) : null,
              )}
            </div>
            {s.description_html ? <Prose html={s.description_html} className="border-t border-border pt-3 text-sm" /> : null}
            <p className="text-xs text-subtle">Submitted {relativeTime(s.submitted_at)} · {formatDateTime(s.submitted_at)}</p>
          </>
        ) : (
          <InlineNotice tone="warning" title="No project submitted">This team hasn't submitted a project yet. Check with the organizers before scoring.</InlineNotice>
        )}
        {entry.slot ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-[var(--radius-md)] bg-surface-2 px-3 py-2 text-sm">
            <span className="inline-flex items-center gap-1.5"><CalendarClock className="h-4 w-4 text-subtle" aria-hidden /> Presentation {formatDateTime(entry.slot.starts_at)} ({relativeTime(entry.slot.starts_at)})</span>
            {entry.slot.location ? <span className="inline-flex items-center gap-1.5"><MapPin className="h-4 w-4 text-subtle" aria-hidden /> {entry.slot.location}</span> : null}
            {entry.slot.meeting_url ? (
              <a href={entry.slot.meeting_url} target="_blank" rel="noopener noreferrer" className="text-accent-strong hover:underline">Join meeting</a>
            ) : null}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
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

  if (me.isPending || !me.data) return <Spinner />;
  if (queue.isPending) {
    return (
      <Container size="xl" className="py-8">
        <SkeletonRows rows={6} />
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
  const current = entries.find((e) => e.team_id === selected) ?? null;
  const select = (id: string) => {
    if (id === selected) return;
    if (dirty && !window.confirm("You have unsaved changes for this entry. Discard them?")) return;
    setDirty(false);
    setSelected(id);
  };

  return (
    <Container size="xl">
      <PageHeader
        eyebrow="Judging"
        title={q.competition.title}
        description={`${formatNumber(submittedCount)} of ${formatNumber(entries.length)} assigned entries submitted · rubric v${q.rubric_version}`}
        actions={
          <>
            <LinkButton href="/judge" variant="ghost" size="sm">All events</LinkButton>
            <LinkButton href={`/competitions/${slug}`} variant="secondary" size="sm">Competition page</LinkButton>
          </>
        }
      />
      <div className="mb-6 space-y-3">
        <ProgressBar value={entries.length ? (submittedCount / entries.length) * 100 : 0} label="Judging progress" />
        {q.finalized ? (
          <InlineNotice tone="info" title="Judging is finalized">Scores are locked. You can review what you submitted.</InlineNotice>
        ) : null}
        <p className="flex items-start gap-2 text-xs text-muted">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          Conflict of interest: if you know a team member personally or professionally, declare a conflict instead of scoring. The entry leaves your queue and your score won&apos;t count.
        </p>
      </div>

      {criteria.length === 0 ? (
        <EmptyState icon={<Lock className="h-5 w-5" />} title="The rubric isn't ready yet" description="The organizers haven't defined the scoring criteria. You'll be able to score entries once they do." />
      ) : entries.length === 0 ? (
        <EmptyState title="No entries assigned to you yet" description="Entries appear here once teams submit projects and the organizers assign them to you." />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
          <nav aria-label="Entries" className="lg:sticky lg:top-20 lg:self-start">
            <ul className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:gap-1 lg:overflow-visible">
              {entries.map((e) => {
                const st = entryState(e);
                const active = e.team_id === selected;
                return (
                  <li key={e.team_id} className="shrink-0">
                    <button
                      type="button"
                      onClick={() => select(e.team_id)}
                      aria-current={active ? "true" : undefined}
                      className={cn(
                        "flex w-full min-w-48 items-start gap-2.5 rounded-[var(--radius-md)] border px-3 py-2 text-left text-sm transition-colors",
                        active ? "border-accent bg-accent-soft" : "border-transparent hover:bg-surface-2",
                      )}
                    >
                      {st === "submitted" ? (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                      ) : st === "draft" ? (
                        <PencilLine className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                      ) : (
                        <Circle className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
                      )}
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-fg">{e.submission?.title ?? e.label}</span>
                        <span className="block truncate text-xs text-subtle">
                          {e.label} · {st === "submitted" ? `Submitted${e.my_score?.weighted_total != null ? ` · ${e.my_score.weighted_total.toFixed(1)}` : ""}` : st === "draft" ? "Draft" : "Not started"}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>
          {current ? (
            <div className="min-w-0 space-y-4">
              <EntryDetails entry={current} />
              <ScoreForm
                key={`${current.team_id}-${current.my_score?.status ?? "none"}-${current.my_score?.weighted_total ?? ""}-${q.rubric_version}`}
                slug={slug}
                entry={current}
                criteria={criteria}
                finalized={q.finalized}
                judgeId={me.data.id}
                onDirtyChange={setDirty}
              />
              {entryState(current) === "submitted" ? (
                <p className="flex items-center gap-2 text-xs text-subtle"><Badge tone="success">Submitted</Badge> This score counts toward the results.</p>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
      <p className="mt-10 text-xs text-subtle">
        Scores are weighted per the rubric and scaled to 0–100 by the server; the total shown here is a preview using the same formula.
      </p>
    </Container>
  );
}
