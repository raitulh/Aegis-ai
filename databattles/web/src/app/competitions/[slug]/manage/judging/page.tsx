"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  CalendarPlus,
  CheckCheck,
  ExternalLink,
  EyeOff,
  FolderGit2,
  Gavel,
  ListOrdered,
  Lock,
  Plus,
  Presentation,
  Save,
  ShieldAlert,
  Trash2,
  Trophy,
  UserPlus,
  Users,
} from "lucide-react";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { zonedInputToUtc } from "@/components/organizer/datetime";
import {
  overviewKey,
  presentationsKey,
  projectSubmissionsKey,
  rubricKey,
  toCriteria,
  type JudgingOverview,
  type PresentationSlot,
  type ProjectSubmission,
  type RubricOut,
} from "@/components/organizer/judging";
import { DownloadLink, ManageHeading, TeamPicker, ZonedDateTimeField, fieldError, manageKey, useCompetitionDetail, useManage, useTeams } from "@/components/organizer/shared";
import { InlineEmpty, ListSurface, Panel, SectionHeader, SubHeading, Tile, TileGrid } from "@/components/organizer/ui";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Switch, Textarea } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { RankBadge } from "@/components/ui/extras";
import { ProgressBar } from "@/components/ui/misc";
import { EmptyState, ErrorState, InlineNotice, QueryState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { ApiError, del, get, post, put } from "@/lib/api";
import { formatDateTime, formatNumber, formatScore, relativeTime } from "@/lib/format";
import { useApiMutation, useUnsavedChangesWarning } from "@/lib/hooks";
import type { Message } from "@/lib/types";

const KEY_RE = /^[a-z][a-z0-9_]{1,31}$/;

interface DraftCriterion {
  key: string;
  label: string;
  description: string;
  min: string;
  max: string;
  weight: string;
  allow_decimal: boolean;
}

const slugKey = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "c_$1").slice(0, 32);

/* ------------------------------------------------------------------ rubric */

function RubricEditor({ slug, rubric }: { slug: string; rubric: RubricOut }) {
  const initial = useMemo<DraftCriterion[]>(
    () =>
      toCriteria(rubric.criteria).map((c) => ({
        key: c.key,
        label: c.label,
        description: c.description ?? "",
        min: String(c.min),
        max: String(c.max),
        weight: String(c.weight),
        allow_decimal: c.allow_decimal,
      })),
    [rubric.criteria],
  );
  const [rows, setRows] = useState<DraftCriterion[]>(initial.length ? initial : [{ key: "impact", label: "Impact", description: "", min: "0", max: "10", weight: "1", allow_decimal: false }]);
  const [reveal, setReveal] = useState(rubric.reveal_scores_to_judges);
  const [blind, setBlind] = useState(rubric.blind_judging);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const criteriaLocked = rubric.locked || rubric.finalized;
  const dirty =
    JSON.stringify(rows) !== JSON.stringify(initial) || reveal !== rubric.reveal_scores_to_judges || blind !== rubric.blind_judging;
  useUnsavedChangesWarning(dirty);

  const errors = useMemo(() => {
    const out: Record<string, string> = {};
    const seen = new Set<string>();
    rows.forEach((r, i) => {
      if (!KEY_RE.test(r.key)) out[`${i}.key`] = "2–32 chars: lowercase letters, digits, _; start with a letter.";
      else if (seen.has(r.key)) out[`${i}.key`] = "Keys must be unique.";
      seen.add(r.key);
      if (!r.label.trim()) out[`${i}.label`] = "Required.";
      const lo = Number(r.min);
      const hi = Number(r.max);
      const w = Number(r.weight);
      if (r.min === "" || Number.isNaN(lo)) out[`${i}.min`] = "Number required.";
      if (r.max === "" || Number.isNaN(hi)) out[`${i}.max`] = "Number required.";
      else if (!Number.isNaN(lo) && hi <= lo) out[`${i}.max`] = "Must exceed min.";
      if (r.weight === "" || Number.isNaN(w) || w <= 0) out[`${i}.weight`] = "Must be positive.";
    });
    if (!rows.length) out.criteria = "Add at least one criterion.";
    if (rows.length > 12) out.criteria = "Use at most 12 criteria.";
    return out;
  }, [rows]);
  const totalWeight = rows.reduce((n, r) => n + (Number(r.weight) > 0 ? Number(r.weight) : 0), 0);

  const save = useApiMutation(
    () =>
      put<RubricOut>(`/competitions/${slug}/rubric`, {
        ...(criteriaLocked
          ? {}
          : {
              criteria: rows.map((r) => ({
                key: r.key,
                label: r.label.trim(),
                description: r.description.trim() || null,
                min: Number(r.min),
                max: Number(r.max),
                weight: Number(r.weight),
                allow_decimal: r.allow_decimal,
              })),
            }),
        reveal_scores_to_judges: reveal,
        blind_judging: blind,
      }),
    {
      success: (r) => `Rubric saved (version ${r.version}).`,
      invalidate: [rubricKey(slug), overviewKey(slug), manageKey(slug), ["competitions", slug, "publish-checks"]],
      onSuccess: () => setServerError(null),
      onError: (e) => setServerError(e),
    },
  );
  const update = (i: number, patch: Partial<DraftCriterion>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, d: -1 | 1) =>
    setRows((rs) => {
      const j = i + d;
      if (j < 0 || j >= rs.length) return rs;
      const next = [...rs];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const invalid = !criteriaLocked && Object.keys(errors).length > 0;

  return (
    <div className="space-y-5">
      {rubric.finalized ? (
        <InlineNotice tone="info" title="Judging is finalized">The rubric and all scores are locked.</InlineNotice>
      ) : rubric.locked ? (
        <InlineNotice tone="warning" title="Criteria locked">Judges have submitted scores, so criteria can't change. You can still adjust visibility settings.</InlineNotice>
      ) : null}
      <Panel
        icon={<ListOrdered />}
        title="Criteria"
        description={`Version ${rubric.version}. Each criterion is normalized to its range and weighted; totals are on a 0–100 scale.`}
        action={
          !criteriaLocked ? (
            <Button size="sm" variant="secondary" icon={<Plus className="h-4 w-4" />} disabled={rows.length >= 12} onClick={() => setRows((rs) => [...rs, { key: "", label: "", description: "", min: "0", max: "10", weight: "1", allow_decimal: false }])}>
              Add criterion
            </Button>
          ) : null
        }
      >
        <div className="space-y-3">
          {errors.criteria && !criteriaLocked ? <FormError message={errors.criteria} /> : null}
          <ol className="space-y-3">
            {rows.map((r, i) => (
              <li key={i} className="rounded-[var(--radius-md)] border border-border bg-bg-elevated/50 p-3.5">
                <fieldset disabled={criteriaLocked} className="grid gap-3">
                  <legend className="sr-only">Criterion {i + 1}</legend>
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <span className="tabular flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 font-mono text-[11px] text-muted" aria-hidden>{i + 1}</span>
                      <span className="text-xs font-medium text-subtle">
                        Criterion {i + 1} · <span className="tabular">{totalWeight > 0 && Number(r.weight) > 0 ? `${Math.round((Number(r.weight) / totalWeight) * 100)}% of total` : "—"}</span>
                      </span>
                      <span className="hidden h-1 max-w-32 flex-1 overflow-hidden rounded-full bg-surface-3 sm:block" aria-hidden>
                        <span className="block h-full rounded-full bg-brand" style={{ width: `${totalWeight > 0 && Number(r.weight) > 0 ? Math.round((Number(r.weight) / totalWeight) * 100) : 0}%` }} />
                      </span>
                    </div>
                    {!criteriaLocked ? (
                      <div className="flex gap-1">
                        <Button size="icon" variant="ghost" aria-label={`Move criterion ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp className="h-4 w-4" /></Button>
                        <Button size="icon" variant="ghost" aria-label={`Move criterion ${i + 1} down`} disabled={i === rows.length - 1} onClick={() => move(i, 1)}><ArrowDown className="h-4 w-4" /></Button>
                        <Button size="icon" variant="ghost" aria-label={`Remove criterion ${i + 1}`} disabled={rows.length <= 1} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
                      </div>
                    ) : null}
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
                    <Field label="Label" error={errors[`${i}.label`]}>
                      {(p) => <Input {...p} value={r.label} maxLength={80} onChange={(e) => update(i, { label: e.target.value })} onBlur={() => !r.key && r.label.trim() && update(i, { key: slugKey(r.label) })} />}
                    </Field>
                    <Field label="Key" error={errors[`${i}.key`]} hint="Stable id used in exports.">
                      {(p) => <Input {...p} value={r.key} maxLength={32} className="font-mono" onChange={(e) => update(i, { key: e.target.value.toLowerCase() })} />}
                    </Field>
                  </div>
                  <Field label="Guidance for judges" hint="Optional, up to 300 characters.">
                    {(p) => <Textarea {...p} rows={2} value={r.description} maxLength={300} onChange={(e) => update(i, { description: e.target.value })} />}
                  </Field>
                  <div className="grid grid-cols-3 gap-3 sm:grid-cols-[repeat(3,8rem)_1fr] sm:items-end">
                    <Field label="Min" error={errors[`${i}.min`]}>
                      {(p) => <Input {...p} type="number" step="any" value={r.min} onChange={(e) => update(i, { min: e.target.value })} />}
                    </Field>
                    <Field label="Max" error={errors[`${i}.max`]}>
                      {(p) => <Input {...p} type="number" step="any" value={r.max} onChange={(e) => update(i, { max: e.target.value })} />}
                    </Field>
                    <Field label="Weight" error={errors[`${i}.weight`]}>
                      {(p) => <Input {...p} type="number" step="any" min={0} value={r.weight} onChange={(e) => update(i, { weight: e.target.value })} />}
                    </Field>
                    <div className="col-span-3 pb-2 sm:col-span-1">
                      <Switch checked={r.allow_decimal} disabled={criteriaLocked} onChange={(v) => update(i, { allow_decimal: v })} label="Allow decimals" />
                    </div>
                  </div>
                </fieldset>
              </li>
            ))}
          </ol>
        </div>
      </Panel>
      <Panel icon={<EyeOff />} title="Judging visibility">
        <div className="space-y-4">
          <Switch checked={blind} disabled={rubric.finalized} onChange={setBlind} label="Blind judging" description="Judges see anonymous entry labels instead of team names." />
          <Switch checked={reveal} disabled={rubric.finalized} onChange={setReveal} label="Let judges see each other's totals" description="Judges can view aggregate score progress. Individual criterion scores and feedback stay organizer-only." />
        </div>
      </Panel>
      {serverError ? <FormError message={fieldError(serverError.fields, "criteria") ?? serverError.message} /> : null}
      {!rubric.finalized ? (
        <div className="sticky bottom-0 z-20 -mx-4 flex items-center gap-2 border-t border-border bg-[var(--glass-strong)] px-4 py-3 backdrop-blur-xl sm:mx-0 sm:rounded-[var(--radius-lg)] sm:border">
          <p className="mr-auto min-w-0 text-xs text-subtle" aria-live="polite">{dirty ? (invalid ? "Fix the highlighted fields to save." : "Unsaved rubric changes.") : "No unsaved changes."}</p>
          <Button variant="ghost" disabled={!dirty} onClick={() => { setRows(initial); setReveal(rubric.reveal_scores_to_judges); setBlind(rubric.blind_judging); }}>Discard</Button>
          <Button icon={<Save className="h-4 w-4" />} loading={save.isPending} disabled={!dirty || invalid} onClick={() => save.mutate(undefined)}>Save rubric</Button>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ judges & conflicts */

function JudgesTab({ slug, overview, judges, teamName, finalized }: {
  slug: string;
  overview: JudgingOverview;
  judges: { id: string; handle: string; display_name: string; avatar_url?: string | null }[];
  teamName: (id: string | null) => string;
  finalized: boolean;
}) {
  const [handle, setHandle] = useState("");
  const [teamId, setTeamId] = useState("");
  const [panel, setPanel] = useState("");
  const [assignError, setAssignError] = useState<ApiError | null>(null);
  const [cJudge, setCJudge] = useState("");
  const [cTeam, setCTeam] = useState("");
  const [cReason, setCReason] = useState("");
  const [conflictError, setConflictError] = useState<ApiError | null>(null);
  const invalidate = [overviewKey(slug), manageKey(slug)] as const;

  const assign = useApiMutation(
    () => post<Message>(`/competitions/${slug}/judges`, { handle: handle.trim().replace(/^@/, ""), team_id: teamId || null, panel: panel.trim() || null }),
    {
      success: "Judge assigned. They've been notified.",
      invalidate,
      onSuccess: () => {
        setHandle("");
        setTeamId("");
        setAssignError(null);
      },
      onError: (e) => setAssignError(e),
    },
  );
  const unassign = useApiMutation((id: string) => del<Message>(`/competitions/${slug}/judges/${id}`), { success: "Assignment removed.", invalidate });
  const conflict = useApiMutation(
    () => post<Message>(`/competitions/${slug}/judging/conflicts`, { judge_id: cJudge, team_id: cTeam, reason: cReason.trim() }),
    {
      success: "Conflict recorded. That judge's scores for the team are excluded.",
      invalidate,
      onSuccess: () => {
        setCTeam("");
        setCReason("");
        setConflictError(null);
      },
      onError: (e) => setConflictError(e),
    },
  );

  const judgeName = (id: string) => {
    const j = judges.find((x) => x.id === id) ?? overview.assignments.find((a) => a.judge.id === id)?.judge;
    return j ? `${j.display_name} (@${j.handle})` : "Judge";
  };
  const unassigned = judges.filter((j) => !overview.assignments.some((a) => a.judge.id === j.id));

  return (
    <div className="space-y-6">
      <Panel icon={<UserPlus />} title="Assignments" description="Assign a judge to every entry or to specific teams. Adding someone here also makes them a judge.">
        <div className="space-y-5">
          {!finalized ? (
            <form
              className="grid gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated/40 p-3.5"
              onSubmit={(e) => {
                e.preventDefault();
                if (handle.trim()) assign.mutate(undefined);
              }}
            >
              <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
                <Field label="Judge handle" error={assignError?.fields.handle ?? (assignError && !Object.keys(assignError.fields).length ? assignError.message : undefined)}>
                  {(p) => <Input {...p} value={handle} placeholder="@handle" list="judge-handles" autoComplete="off" onChange={(e) => { setHandle(e.target.value); setAssignError(null); }} />}
                </Field>
                <Field label="Panel" hint="Optional grouping label.">
                  {(p) => <Input {...p} value={panel} maxLength={60} onChange={(e) => setPanel(e.target.value)} />}
                </Field>
              </div>
              <datalist id="judge-handles">
                {judges.map((j) => <option key={j.id} value={j.handle}>{j.display_name}</option>)}
              </datalist>
              <TeamPicker slug={slug} value={teamId} onChange={setTeamId} allowNone noneLabel="All entries" label="Entries" error={assignError?.fields.team_id} hint="“All entries” covers every team that submits a project." />
              <div className="flex justify-end">
                <Button type="submit" icon={<UserPlus className="h-4 w-4" />} loading={assign.isPending} disabled={!handle.trim()}>Assign</Button>
              </div>
            </form>
          ) : null}
          {overview.assignments.length === 0 ? (
            <InlineEmpty icon={<Gavel />} title="No assignments yet" description="Judges only see entries assigned to them." />
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Judge</TH>
                  <TH>Entries</TH>
                  <TH>Panel</TH>
                  <TH className="relative"><span className="sr-only">Actions</span></TH>
                </tr>
              </THead>
              <TBody>
                {overview.assignments.map((a) => (
                  <TR key={a.id}>
                    <TD><UserLink user={a.judge} /></TD>
                    <TD>{a.team_id ? teamName(a.team_id) : <Badge tone="accent">All entries</Badge>}</TD>
                    <TD className="text-muted">{a.panel ?? "—"}</TD>
                    <TD className="text-right">
                      {!finalized ? (
                        <ConfirmDialog
                          trigger={<Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} aria-label={`Remove assignment for ${a.judge.display_name}`}>Remove</Button>}
                          title="Remove this assignment?"
                          description="The judge stops seeing these entries. Scores already submitted are kept."
                          confirmLabel="Remove"
                          onConfirm={() => unassign.mutateAsync(a.id).catch(() => undefined)}
                        />
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          {unassigned.length ? (
            <p className="text-xs text-muted">
              Judges without assignments: {unassigned.map((j) => `@${j.handle}`).join(", ")} — they won't see any entries yet.
            </p>
          ) : null}
        </div>
      </Panel>

      <Panel icon={<ShieldAlert />} title="Conflicts of interest" description="A conflicted judge never sees that team's entry; their scores for it are discarded or excluded from results.">
        <div className="space-y-5">
          {overview.conflicts.length === 0 ? (
            <p className="rounded-[var(--radius-md)] border border-dashed border-border-strong px-3 py-3 text-sm text-subtle">No conflicts recorded.</p>
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-md)] border border-border bg-bg-elevated/50">
              {overview.conflicts.map((c, i) => (
                <li key={`${c.judge_id}-${c.team_id}-${i}`} className="flex flex-col gap-1 px-3 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between">
                  <span className="flex items-center gap-2">
                    <ShieldAlert className="h-4 w-4 text-warning" aria-hidden />
                    <span className="font-medium text-fg">{judgeName(c.judge_id)}</span>
                    <span className="text-subtle">↔</span>
                    <span>{teamName(c.team_id)}</span>
                  </span>
                  <span className="text-xs text-muted">{c.reason}</span>
                </li>
              ))}
            </ul>
          )}
          {!finalized && judges.length ? (
            <div className="grid gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated/40 p-3.5">
              <SubHeading>Record a conflict</SubHeading>
              <Field label="Judge">
                {(p) => (
                  <Select {...p} value={cJudge} onChange={(e) => setCJudge(e.target.value)}>
                    <option value="" disabled>Choose a judge</option>
                    {judges.map((j) => <option key={j.id} value={j.id}>{j.display_name} (@{j.handle})</option>)}
                  </Select>
                )}
              </Field>
              <TeamPicker slug={slug} value={cTeam} onChange={setCTeam} label="Team" />
              <Field label="Reason" error={conflictError?.fields.reason} hint="e.g. “Supervises a team member”. Recorded in the audit log.">
                {(p) => <Input {...p} value={cReason} maxLength={300} onChange={(e) => setCReason(e.target.value)} />}
              </Field>
              <FormError message={conflictError && !Object.keys(conflictError.fields).length ? conflictError.message : null} />
              <div className="flex justify-end">
                <ConfirmDialog
                  trigger={<Button variant="secondary" disabled={!cJudge || !cTeam || cReason.trim().length < 3} loading={conflict.isPending}>Record conflict</Button>}
                  title="Record this conflict?"
                  description="Any draft score from this judge for the team is deleted, and submitted scores are excluded from results. This can't be undone from the UI."
                  confirmLabel="Record conflict"
                  onConfirm={() => conflict.mutateAsync(undefined).catch(() => undefined)}
                />
              </div>
            </div>
          ) : null}
        </div>
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------------ scores */

function ScoresTab({ slug, overview, entriesCount, judges, finalizedResults }: {
  slug: string;
  overview: JudgingOverview;
  entriesCount: number;
  judges: { id: string; handle: string; display_name: string; avatar_url?: string | null }[];
  finalizedResults: boolean;
}) {
  const finalize = useApiMutation(() => post<Message>(`/competitions/${slug}/judging/finalize`), {
    success: (m) => m.message,
    invalidate: [overviewKey(slug), rubricKey(slug)],
  });
  const progress = judges.map((j) => {
    const mine = overview.assignments.filter((a) => a.judge.id === j.id);
    const conflicts = overview.conflicts.filter((c) => c.judge_id === j.id).length;
    const expected = mine.some((a) => a.team_id === null) ? Math.max(0, entriesCount - conflicts) : mine.filter((a) => a.team_id).length;
    const submitted = overview.scores.filter((s) => s.judge?.id === j.id && s.status === "submitted").length;
    const drafts = overview.scores.filter((s) => s.judge?.id === j.id && s.status === "draft").length;
    return { judge: j, expected, submitted, drafts };
  });
  const criteria = overview.rubric;
  const byTeam = useMemo(() => {
    const m = new Map<string, JudgingOverview["scores"]>();
    for (const s of overview.scores) m.set(s.team_id, [...(m.get(s.team_id) ?? []), s]);
    return [...m.entries()].sort((a, b) => (a[1][0]?.team_name ?? "").localeCompare(b[1][0]?.team_name ?? ""));
  }, [overview.scores]);

  return (
    <div className="space-y-6">
      <Panel
        icon={<Activity />}
        title="Judge progress"
        flush
        action={<DownloadLink path={`/competitions/${slug}/judging/scores.csv`}>Scores CSV</DownloadLink>}
      >
        {progress.length === 0 ? (
          <p className="px-5 py-4 text-sm text-subtle">No judges yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {progress.map((p) => (
              <li key={p.judge.id} className="grid gap-2 px-4 py-3 sm:grid-cols-[15rem_1fr_9rem] sm:items-center sm:gap-4 sm:px-5">
                <UserLink user={p.judge} size={26} className="font-medium" />
                <ProgressBar value={p.expected ? (p.submitted / p.expected) * 100 : 0} label={`${p.judge.display_name}: ${p.submitted} of ${p.expected} submitted`} />
                <span className="tabular text-xs text-muted sm:text-right">
                  <span className="font-medium text-fg">{p.submitted}</span>/{p.expected} submitted{p.drafts ? ` · ${p.drafts} draft` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <section aria-labelledby="scores-by-entry">
        <SectionHeader id="scores-by-entry" title="Scores by entry" description="Draft scores don't count until the judge submits them." />
        {byTeam.length === 0 ? (
          <InlineEmpty icon={<Gavel />} title="No scores yet" description="Scores appear as judges work through their queue." />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Entry</TH>
                <TH>Judge</TH>
                <TH>Status</TH>
                <TH className="text-right">Total</TH>
                <TH>Criteria</TH>
              </tr>
            </THead>
            <TBody>
              {byTeam.flatMap(([teamId, rows]) =>
                rows.map((s, i) => (
                  <TR key={`${teamId}-${s.judge?.id ?? i}`}>
                    <TD className="relative font-medium">{i === 0 ? s.team_name : <span className="sr-only">{s.team_name}</span>}</TD>
                    <TD><UserLink user={s.judge} size={20} /></TD>
                    <TD>
                      <Badge tone={s.status === "submitted" ? "success" : "neutral"}>{s.status === "submitted" ? "Submitted" : "Draft"}</Badge>
                      {s.submitted_at ? <span className="block text-[11px] text-subtle" title={formatDateTime(s.submitted_at)}>{relativeTime(s.submitted_at)}</span> : null}
                    </TD>
                    <TD className="tabular text-right font-mono text-xs font-medium text-fg">{formatScore(s.weighted_total, 2)}</TD>
                    <TD className="text-xs text-muted">
                      {s.scores ? criteria.map((c) => `${c.label}: ${s.scores?.[c.key] ?? "—"}`).join(" · ") : "—"}
                      {s.feedback ? <p className="mt-1 line-clamp-2 italic">“{s.feedback}”</p> : null}
                    </TD>
                  </TR>
                )),
              )}
              </TBody>
            </Table>
          )}
      </section>

      <section aria-labelledby="judging-ranking">
        <SectionHeader id="judging-ranking" title="Ranking preview" description="Average of submitted weighted totals per entry; ties broken by the first criterion, then submission time." />
        {overview.preview.length === 0 ? (
          <InlineEmpty icon={<Trophy />} title="Nothing to rank yet" description="The preview appears once judges submit scores." />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH className="w-16">Rank</TH>
                <TH>Team</TH>
                <TH className="text-right">Average (0–100)</TH>
                <TH className="text-right">Judges</TH>
              </tr>
            </THead>
            <TBody>
              {overview.preview.map((r) => (
                <TR key={r.team_id}>
                  <TD className="py-2.5"><RankBadge rank={r.rank} size="sm" /></TD>
                  <TD className="py-2.5 font-medium text-fg">{r.team_name}</TD>
                  <TD className="tabular py-2.5 text-right font-mono text-xs font-medium text-fg">{formatScore(r.score, 2)}</TD>
                  <TD className="tabular py-2.5 text-right">{r.judge_count ?? "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </section>

      <Panel
        tone={overview.finalized ? "default" : "danger"}
        icon={<Lock />}
        title="Finalize judging"
        description={overview.finalized ? "Judging is finalized — scores are locked." : "Locks all scores so judges can't change them. Then finalize the results to publish the ranking."}
        action={
          overview.finalized ? (
            <Badge tone="success" icon={<Lock className="h-3 w-3" aria-hidden />}>Finalized</Badge>
          ) : (
            <ConfirmDialog
              trigger={<Button variant="danger" icon={<Lock className="h-4 w-4" />} loading={finalize.isPending}>Finalize judging</Button>}
              title="Finalize judging? This can't be undone."
              description="All scores lock immediately. Unsubmitted drafts won't count. You can then finalize results on the Results page."
              confirmLabel="Finalize judging"
              onConfirm={() => finalize.mutateAsync(undefined).catch(() => undefined)}
            />
          )
        }
      >
        {overview.finalized && !finalizedResults ? (
          <LinkButton href={`/competitions/${slug}/manage/results`} size="sm" variant="secondary">Go to results</LinkButton>
        ) : null}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------------ entries & presentations */

function EntriesTab({ slug }: { slug: string }) {
  const q = useQuery({ queryKey: projectSubmissionsKey(slug), queryFn: () => get<ProjectSubmission[]>(`/competitions/${slug}/project-submissions`) });
  return (
    <QueryState
      query={q}
      loading={<SkeletonRows rows={4} />}
      isEmpty={(d) => d.length === 0}
      empty={<EmptyState icon={<FolderGit2 />} title="No project submissions yet" description="Teams submit a title, summary and links from the competition page while submissions are open." />}
    >
      {(items) => (
        <ListSurface>
          {items.map((s) => (
            <li key={s.team_id} className="px-4 py-4 sm:px-5">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="text-eyebrow text-subtle">{s.team_name}</p>
                  <h3 className="mt-1 font-semibold tracking-[-0.01em] text-fg">{s.title}</h3>
                  {s.summary ? <p className="mt-1 max-w-3xl text-sm text-muted">{s.summary}</p> : null}
                </div>
                <span className="tabular shrink-0 text-xs text-subtle" title={formatDateTime(s.updated_at)}>Updated {relativeTime(s.updated_at)}</span>
              </div>
              <div className="mt-2.5 flex flex-wrap gap-2 text-[13px]">
                {([["Repository", s.repo_url], ["Demo", s.demo_url], ["Video", s.video_url]] as const).map(([label, url]) =>
                  url ? (
                    <a key={label} href={url} target="_blank" rel="noopener noreferrer" className="inline-flex h-9 items-center gap-1 rounded-full border border-border bg-surface-2 px-3 text-muted sm:h-7 sm:px-2.5 transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden /> {label}
                    </a>
                  ) : null,
                )}
              </div>
              {s.description_html ? (
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm text-muted hover:text-fg">Full description</summary>
                  <Prose html={s.description_html} className="mt-2 text-sm" />
                </details>
              ) : null}
            </li>
          ))}
        </ListSurface>
      )}
    </QueryState>
  );
}

function PresentationsTab({ slug, timeZone, readOnly }: { slug: string; timeZone: string; readOnly: boolean }) {
  const q = useQuery({ queryKey: presentationsKey(slug), queryFn: () => get<PresentationSlot[]>(`/competitions/${slug}/presentations`) });
  const empty = { team_id: "", starts_at: "", ends_at: "", location: "", meeting_url: "", notes: "" };
  const [f, setF] = useState(empty);
  const [error, setError] = useState<ApiError | null>(null);
  const [localErr, setLocalErr] = useState<Record<string, string>>({});
  const add = useApiMutation(
    () =>
      post<Message>(`/competitions/${slug}/presentations`, {
        team_id: f.team_id || null,
        starts_at: zonedInputToUtc(f.starts_at, timeZone),
        ends_at: zonedInputToUtc(f.ends_at, timeZone),
        location: f.location.trim() || null,
        meeting_url: f.meeting_url.trim() || null,
        notes: f.notes.trim() || null,
      }),
    {
      success: "Presentation slot added.",
      invalidate: [presentationsKey(slug)],
      onSuccess: () => {
        setF({ ...empty, starts_at: f.ends_at });
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  const remove = useApiMutation((id: string) => del<Message>(`/competitions/${slug}/presentations/${id}`), {
    success: "Slot removed.",
    invalidate: [presentationsKey(slug)],
  });
  function submit(e: React.FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    const s = zonedInputToUtc(f.starts_at, timeZone);
    const en = zonedInputToUtc(f.ends_at, timeZone);
    if (!s) errs.starts_at = "Required.";
    if (!en) errs.ends_at = "Required.";
    else if (s && new Date(en) <= new Date(s)) errs.ends_at = "End must be after start.";
    if (f.meeting_url.trim() && !/^https?:\/\/\S+$/i.test(f.meeting_url.trim())) errs.meeting_url = "Use a full http(s):// URL.";
    setLocalErr(errs);
    if (!Object.keys(errs).length) add.mutate(undefined);
  }
  const err = (k: string) => localErr[k] ?? fieldError(error?.fields, k);
  return (
    <div className="space-y-5">
      <QueryState
        query={q}
        loading={<SkeletonRows rows={3} />}
        isEmpty={(d) => d.length === 0}
        empty={<InlineEmpty icon={<Presentation />} title="No presentation slots" description="Schedule demo or pitch slots; teams see their own slot and meeting link." />}
      >
        {(slots) => (
          <ListSurface as="ol">
            {slots.map((s) => (
              <li key={s.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 text-sm">
                  <p className="font-medium text-fg">{s.team_name ?? <span className="text-muted">Unassigned slot</span>}</p>
                  <p className="tabular text-muted">{formatDateTime(s.starts_at)} – {formatDateTime(s.ends_at)}</p>
                  <p className="tabular text-xs text-subtle">Event time: {formatDateTime(s.starts_at, timeZone)}</p>
                  <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted">
                    {s.location ? <span>{s.location}</span> : null}
                    {s.meeting_url ? <a href={s.meeting_url} target="_blank" rel="noopener noreferrer" className="text-accent-strong hover:underline">Meeting link</a> : null}
                  </div>
                  {s.notes ? <p className="mt-1 text-xs text-muted">{s.notes}</p> : null}
                </div>
                {!readOnly ? (
                  <ConfirmDialog
                    trigger={<Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} aria-label="Remove slot">Remove</Button>}
                    title="Remove this slot?"
                    confirmLabel="Remove"
                    onConfirm={() => remove.mutateAsync(s.id).catch(() => undefined)}
                  />
                ) : null}
              </li>
            ))}
          </ListSurface>
        )}
      </QueryState>
      {!readOnly ? (
        <form onSubmit={submit} className="grid gap-4 rounded-[var(--radius-lg)] border border-border bg-surface p-4 shadow-card sm:p-5" noValidate>
          <SubHeading>Add a slot</SubHeading>
          <TeamPicker slug={slug} value={f.team_id} onChange={(team_id) => setF({ ...f, team_id })} allowNone noneLabel="Unassigned" label="Team" error={err("team_id")} />
          <div className="grid gap-4 sm:grid-cols-2">
            <ZonedDateTimeField label="Starts" required timeZone={timeZone} value={f.starts_at} onChange={(v) => setF({ ...f, starts_at: v })} error={err("starts_at")} hint={`Event time zone: ${timeZone}`} />
            <ZonedDateTimeField label="Ends" required timeZone={timeZone} value={f.ends_at} onChange={(v) => setF({ ...f, ends_at: v })} error={err("ends_at")} />
            <Field label="Location" error={err("location")}>
              {(p) => <Input {...p} value={f.location} maxLength={200} onChange={(e) => setF({ ...f, location: e.target.value })} />}
            </Field>
            <Field label="Meeting URL" error={err("meeting_url")} hint="Visible to staff and the assigned team only.">
              {(p) => <Input {...p} type="url" value={f.meeting_url} maxLength={500} placeholder="https://" onChange={(e) => setF({ ...f, meeting_url: e.target.value })} />}
            </Field>
          </div>
          <Field label="Notes for staff" error={err("notes")}>
            {(p) => <Input {...p} value={f.notes} maxLength={500} onChange={(e) => setF({ ...f, notes: e.target.value })} />}
          </Field>
          <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
          <div className="flex justify-end">
            <Button type="submit" icon={<CalendarPlus className="h-4 w-4" />} loading={add.isPending}>Add slot</Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ page */

export default function ManageJudgingPage() {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  const detail = useCompetitionDetail(slug);
  const judged = manage.data ? String(manage.data.raw.scoring_mode) === "judged" : false;
  const rubric = useQuery({ queryKey: rubricKey(slug), queryFn: () => get<RubricOut>(`/competitions/${slug}/rubric`), enabled: judged });
  const overview = useQuery({ queryKey: overviewKey(slug), queryFn: () => get<JudgingOverview>(`/competitions/${slug}/judging/overview`), enabled: judged });
  const entries = useQuery({ queryKey: projectSubmissionsKey(slug), queryFn: () => get<ProjectSubmission[]>(`/competitions/${slug}/project-submissions`), enabled: judged });
  const teams = useTeams(slug, "");
  const [tab, setTab] = useState("rubric");

  const teamNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const t of teams.data?.items ?? []) m.set(t.id, t.name);
    for (const s of overview.data?.scores ?? []) m.set(s.team_id, s.team_name);
    for (const e of entries.data ?? []) m.set(e.team_id, e.team_name);
    return m;
  }, [teams.data, overview.data, entries.data]);
  const teamName = (id: string | null) => (id ? teamNames.get(id) ?? "Team" : "All entries");

  if (manage.isPending || detail.isPending) return <SkeletonRows rows={6} />;
  if (manage.isError) return <ErrorState error={manage.error} onRetry={() => manage.refetch()} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  if (!judged) {
    return (
      <div>
        <ManageHeading eyebrow="Configure" icon={<Gavel />} title="Judging" />
        <EmptyState
          icon={<Gavel />}
          title="This competition isn't judged"
          description="Rubrics, judge assignments and presentation slots apply to judged events. Switch the scoring mode to “Judged” in Settings to use them."
          action={<LinkButton href={`/competitions/${slug}/manage/settings`} variant="secondary">Open settings</LinkButton>}
        />
      </div>
    );
  }

  const m = manage.data;
  const judges = m.staff.filter((s) => s.role === "judge").map((s) => s.user);
  const resultsFinal = m.lifecycle === "finalized" || m.lifecycle === "archived";

  return (
    <div>
      <ManageHeading
        eyebrow="Configure"
        icon={<Gavel />}
        title="Judging"
        description="Define the rubric, assign judges, follow scoring progress and schedule presentations."
        actions={overview.data?.finalized ? <Badge tone="success" icon={<Lock className="h-3 w-3" aria-hidden />}>Judging finalized</Badge> : undefined}
      />
      <TileGrid cols={4} className="mb-6 animate-rise [animation-delay:40ms]">
        <Tile label="Judges" icon={<Users />} value={formatNumber(judges.length)} hint={overview.data ? `${formatNumber(overview.data.assignments.length)} assignments` : undefined} />
        <Tile label="Entries" icon={<FolderGit2 />} accent="cyan" value={entries.data ? formatNumber(entries.data.length) : "—"} hint="Project submissions" />
        <Tile
          label="Scores submitted"
          icon={<CheckCheck />}
          accent="success"
          value={overview.data ? formatNumber(overview.data.scores.filter((s) => s.status === "submitted").length) : "—"}
          hint={overview.data ? `${formatNumber(overview.data.scores.filter((s) => s.status === "draft").length)} drafts` : undefined}
        />
        <Tile
          label="Rubric"
          icon={<ListOrdered />}
          accent="info"
          value={rubric.data ? `v${rubric.data.version}` : "—"}
          hint={overview.data ? (overview.data.finalized ? "Judging finalized" : overview.data.locked ? "Criteria locked" : "Editable") : undefined}
        />
      </TileGrid>
      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "rubric", label: "Rubric" },
          { value: "judges", label: "Judges", count: judges.length },
          { value: "scores", label: "Scores", count: overview.data?.scores.filter((s) => s.status === "submitted").length },
          { value: "entries", label: "Entries", count: entries.data?.length },
          { value: "presentations", label: "Presentations" },
        ]}
      >
        <TabPanel value="rubric">
          <QueryState query={rubric} loading={<SkeletonRows rows={5} />}>
            {(r) => <RubricEditor key={`${r.version}-${r.locked}-${r.finalized}`} slug={slug} rubric={r} />}
          </QueryState>
        </TabPanel>
        <TabPanel value="judges">
          <QueryState query={overview} loading={<SkeletonRows rows={5} />}>
            {(o) => <JudgesTab slug={slug} overview={o} judges={judges} teamName={teamName} finalized={o.finalized} />}
          </QueryState>
        </TabPanel>
        <TabPanel value="scores">
          <QueryState query={overview} loading={<SkeletonRows rows={5} />}>
            {(o) => <ScoresTab slug={slug} overview={o} entriesCount={entries.data?.length ?? 0} judges={judges} finalizedResults={resultsFinal} />}
          </QueryState>
        </TabPanel>
        <TabPanel value="entries">
          <EntriesTab slug={slug} />
        </TabPanel>
        <TabPanel value="presentations">
          <PresentationsTab slug={slug} timeZone={detail.data.timezone} readOnly={m.lifecycle === "archived"} />
        </TabPanel>
      </Tabs>
    </div>
  );
}
