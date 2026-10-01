"use client";

import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Switch, TagInput, Textarea } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { InlineNotice } from "@/components/ui/states";
import type { FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { CompetitionWrite, PublicConfig } from "@/lib/types";
import { utcToZonedInput, zonedInputToUtc } from "./datetime";
import {
  DIFFICULTIES,
  EVENT_TYPES,
  FORMATS,
  LEADERBOARD_VISIBILITY,
  SCORING_MODES,
  STARTER_KINDS,
  TASK_TYPES,
  TimeZoneSelect,
  VISIBILITIES,
  ZonedDateTimeField,
  fieldError,
} from "./shared";

export type StarterKind = (typeof STARTER_KINDS)[number];

export interface CompetitionFormState {
  // basics
  title: string;
  summary: string;
  event_type: string;
  task_type: string;
  difficulty: string;
  tags: string[];
  host_org_id: string;
  // schedule (wall-clock values in `timezone`)
  timezone: string;
  starts_at: string;
  ends_at: string;
  registration_opens_at: string;
  registration_closes_at: string;
  team_lock_at: string;
  // participation
  visibility: string;
  team_min_size: string;
  team_max_size: string;
  daily_submission_limit: string;
  total_submission_limit: string;
  max_submission_mb: string;
  final_selection_limit: string;
  format: string;
  venue_name: string;
  venue_address: string;
  venue_notes: string;
  // scoring
  scoring_mode: string;
  evaluator: string;
  metric: string;
  secondary_metrics: string[];
  id_column: string;
  target_column: string;
  strict_schema: boolean;
  positive_label: string;
  expected_row_count: string;
  leaderboard_visibility: string;
  show_university_on_leaderboard: boolean;
  cert_participation: boolean;
  cert_award_top_n: string;
  // content
  description_md: string;
  rules_md: string;
  evaluation_md: string;
  scoring_notes_md: string;
  has_prize: boolean;
  prize_summary: string;
  prize_md: string;
  faq: { q: string; a: string }[];
  starter_assets: { label: string; url: string; kind: StarterKind }[];
  organizer_contact_email: string;
  organizer_notes: string;
}

export const emptyCompetitionForm = (timezone = ""): CompetitionFormState => ({
  title: "",
  summary: "",
  event_type: "ml_competition",
  task_type: "classification",
  difficulty: "beginner",
  tags: [],
  host_org_id: "",
  timezone,
  starts_at: "",
  ends_at: "",
  registration_opens_at: "",
  registration_closes_at: "",
  team_lock_at: "",
  visibility: "public",
  team_min_size: "1",
  team_max_size: "4",
  daily_submission_limit: "5",
  total_submission_limit: "",
  max_submission_mb: "20",
  final_selection_limit: "2",
  format: "online",
  venue_name: "",
  venue_address: "",
  venue_notes: "",
  scoring_mode: "automatic",
  evaluator: "csv_prediction",
  metric: "accuracy",
  secondary_metrics: [],
  id_column: "id",
  target_column: "target",
  strict_schema: true,
  positive_label: "1",
  expected_row_count: "",
  leaderboard_visibility: "visible",
  show_university_on_leaderboard: true,
  cert_participation: true,
  cert_award_top_n: "10",
  description_md: "",
  rules_md: "",
  evaluation_md: "",
  scoring_notes_md: "",
  has_prize: false,
  prize_summary: "",
  prize_md: "",
  faq: [],
  starter_assets: [],
  organizer_contact_email: "",
  organizer_notes: "",
});

const str = (v: unknown, fallback = ""): string => (v === null || v === undefined ? fallback : String(v));

/** Builds form state from the organizer manage view (`raw` + private config). */
export function formFromManage(m: {
  raw: Record<string, unknown>;
  evaluation: Record<string, unknown>;
  certificate_rules: Record<string, unknown>;
  show_university_on_leaderboard: boolean;
  organizer_notes: string | null;
  title: string;
}): CompetitionFormState {
  const r = m.raw;
  const tz = str(r.timezone, "UTC") || "UTC";
  const ev = m.evaluation ?? {};
  const base = emptyCompetitionForm(tz);
  return {
    ...base,
    title: m.title,
    summary: str(r.summary),
    event_type: str(r.event_type, base.event_type),
    task_type: str(r.task_type, base.task_type),
    difficulty: str(r.difficulty, base.difficulty),
    tags: Array.isArray(r.tags) ? (r.tags as string[]) : [],
    host_org_id: str(r.host_org_id),
    starts_at: utcToZonedInput(r.starts_at as string | null, tz),
    ends_at: utcToZonedInput(r.ends_at as string | null, tz),
    registration_opens_at: utcToZonedInput(r.registration_opens_at as string | null, tz),
    registration_closes_at: utcToZonedInput(r.registration_closes_at as string | null, tz),
    team_lock_at: utcToZonedInput(r.team_lock_at as string | null, tz),
    visibility: str(r.visibility, base.visibility),
    team_min_size: str(r.team_min_size, base.team_min_size),
    team_max_size: str(r.team_max_size, base.team_max_size),
    daily_submission_limit: str(r.daily_submission_limit, base.daily_submission_limit),
    total_submission_limit: str(r.total_submission_limit),
    max_submission_mb: str(r.max_submission_mb, base.max_submission_mb),
    final_selection_limit: str(r.final_selection_limit, base.final_selection_limit),
    format: str(r.format, base.format),
    venue_name: str(r.venue_name),
    venue_address: str(r.venue_address),
    venue_notes: str(r.venue_notes),
    scoring_mode: str(r.scoring_mode, base.scoring_mode),
    evaluator: str(ev.evaluator, base.evaluator),
    metric: str(ev.metric, base.metric),
    secondary_metrics: Array.isArray(ev.secondary_metrics) ? (ev.secondary_metrics as string[]) : [],
    id_column: str(ev.id_column, "id"),
    target_column: str(ev.target_column, "target"),
    strict_schema: ev.strict_schema === undefined ? true : Boolean(ev.strict_schema),
    positive_label: str(ev.positive_label, "1"),
    expected_row_count: str(ev.expected_row_count),
    leaderboard_visibility: str(r.leaderboard_visibility, base.leaderboard_visibility),
    show_university_on_leaderboard: m.show_university_on_leaderboard,
    cert_participation: m.certificate_rules?.participation === undefined ? true : Boolean(m.certificate_rules.participation),
    cert_award_top_n: str(m.certificate_rules?.award_top_n, "10"),
    description_md: str(r.description_md),
    rules_md: str(r.rules_md),
    evaluation_md: str(r.evaluation_md),
    scoring_notes_md: str(r.scoring_notes_md),
    has_prize: Boolean(r.has_prize),
    prize_summary: str(r.prize_summary),
    prize_md: str(r.prize_md),
    faq: Array.isArray(r.faq) ? (r.faq as { q: string; a: string }[]).map((f) => ({ q: str(f.q), a: str(f.a) })) : [],
    starter_assets: Array.isArray(r.starter_assets)
      ? (r.starter_assets as { label: string; url: string; kind?: string }[]).map((a) => ({
          label: str(a.label),
          url: str(a.url),
          kind: (STARTER_KINDS as readonly string[]).includes(str(a.kind)) ? (a.kind as StarterKind) : "other",
        }))
      : [],
    organizer_contact_email: str(r.organizer_contact_email),
    organizer_notes: str(m.organizer_notes),
  };
}

const intOrUndefined = (s: string): number | undefined => (s.trim() === "" || Number.isNaN(Number(s)) ? undefined : Math.trunc(Number(s)));

/** Full CompetitionWrite payload. Non-nullable numeric columns are omitted when blank. */
export function toCompetitionPayload(f: CompetitionFormState): CompetitionWrite {
  const tz = f.timezone || "UTC";
  const p: CompetitionWrite = {
    title: f.title.trim(),
    summary: f.summary.trim(),
    event_type: f.event_type,
    task_type: f.task_type,
    difficulty: f.difficulty,
    tags: f.tags,
    host_org_id: f.host_org_id || null,
    timezone: tz,
    starts_at: zonedInputToUtc(f.starts_at, tz),
    ends_at: zonedInputToUtc(f.ends_at, tz),
    registration_opens_at: zonedInputToUtc(f.registration_opens_at, tz),
    registration_closes_at: zonedInputToUtc(f.registration_closes_at, tz),
    team_lock_at: zonedInputToUtc(f.team_lock_at, tz),
    visibility: f.visibility,
    total_submission_limit: intOrUndefined(f.total_submission_limit) ?? null,
    format: f.format,
    venue_name: f.venue_name.trim() || null,
    venue_address: f.venue_address.trim() || null,
    venue_notes: f.venue_notes.trim() || null,
    scoring_mode: f.scoring_mode,
    leaderboard_visibility: f.leaderboard_visibility,
    show_university_on_leaderboard: f.show_university_on_leaderboard,
    certificate_rules: { participation: f.cert_participation, award_top_n: intOrUndefined(f.cert_award_top_n) ?? 0 },
    description_md: f.description_md,
    rules_md: f.rules_md,
    evaluation_md: f.evaluation_md,
    scoring_notes_md: f.scoring_notes_md,
    has_prize: f.has_prize,
    prize_summary: f.prize_summary.trim() || null,
    prize_md: f.prize_md,
    faq: f.faq.filter((x) => x.q.trim() || x.a.trim()).map((x) => ({ q: x.q.trim(), a: x.a.trim() })),
    starter_assets: f.starter_assets.filter((a) => a.label.trim() || a.url.trim()).map((a) => ({ label: a.label.trim(), url: a.url.trim(), kind: a.kind })),
    organizer_contact_email: f.organizer_contact_email.trim() || null,
    organizer_notes: f.organizer_notes,
  };
  const ints: [keyof CompetitionWrite, string][] = [
    ["team_min_size", f.team_min_size],
    ["team_max_size", f.team_max_size],
    ["daily_submission_limit", f.daily_submission_limit],
    ["max_submission_mb", f.max_submission_mb],
    ["final_selection_limit", f.final_selection_limit],
  ];
  for (const [k, v] of ints) {
    const n = intOrUndefined(v);
    if (n !== undefined) (p as Record<string, unknown>)[k] = n;
  }
  if (f.scoring_mode === "automatic") {
    const evaluation: Record<string, unknown> = {
      evaluator: f.evaluator || "csv_prediction",
      metric: f.metric,
      secondary_metrics: f.secondary_metrics.filter((m) => m !== f.metric).slice(0, 3),
      id_column: f.id_column.trim() || "id",
      target_column: f.target_column.trim() || "target",
      strict_schema: f.strict_schema,
      positive_label: f.positive_label.trim() || "1",
    };
    const rows = intOrUndefined(f.expected_row_count);
    if (rows) evaluation.expected_row_count = rows;
    p.evaluation = evaluation;
  }
  return p;
}

/** Only the keys whose value differs from `initial` (so locked-but-unchanged fields are never sent). */
export function diffPayload(initial: CompetitionWrite, current: CompetitionWrite): CompetitionWrite {
  const out: Record<string, unknown> = {};
  const keys = new Set([...Object.keys(initial), ...Object.keys(current)]);
  for (const k of keys) {
    const a = (initial as Record<string, unknown>)[k];
    const b = (current as Record<string, unknown>)[k];
    if (b === undefined) continue;
    if (JSON.stringify(a ?? null) !== JSON.stringify(b ?? null)) out[k] = b;
  }
  return out as CompetitionWrite;
}

/** Client-side checks mirroring the server's rules (the server validates again). */
export function validateCompetitionForm(f: CompetitionFormState, opts: { requireHost: boolean }): FieldErrors {
  const e: FieldErrors = {};
  if (f.title.trim().length < 3) e.title = "Use at least 3 characters.";
  if (f.summary.length > 280) e.summary = "Keep the summary under 280 characters.";
  if (opts.requireHost && !f.host_org_id) e.host_org_id = "Choose an organization you manage.";
  const tz = f.timezone || "UTC";
  const t = (v: string) => {
    const iso = zonedInputToUtc(v, tz);
    return iso ? new Date(iso).getTime() : null;
  };
  const starts = t(f.starts_at);
  const ends = t(f.ends_at);
  const regOpen = t(f.registration_opens_at);
  const regClose = t(f.registration_closes_at);
  const lock = t(f.team_lock_at);
  if (starts && ends && ends <= starts) e.ends_at = "End must be after start.";
  if (regOpen && regClose && regClose <= regOpen) e.registration_closes_at = "Registration must close after it opens.";
  if (regClose && ends && regClose > ends) e.registration_closes_at = "Registration must close before the competition ends.";
  if (lock && ends && lock > ends) e.team_lock_at = "Team lock must be before the end.";
  const range = (key: keyof CompetitionFormState, lo: number, hi: number, optional = false) => {
    const v = String(f[key]).trim();
    if (!v) {
      if (!optional) e[key] = "Required.";
      return;
    }
    const n = Number(v);
    if (!Number.isInteger(n) || n < lo || n > hi) e[key] = `Enter a whole number from ${lo} to ${hi}.`;
  };
  range("team_min_size", 1, 20);
  range("team_max_size", 1, 20);
  range("daily_submission_limit", 1, 100);
  range("total_submission_limit", 1, 10000, true);
  range("max_submission_mb", 1, 200);
  range("final_selection_limit", 1, 5);
  if (!e.team_min_size && !e.team_max_size && Number(f.team_max_size) < Number(f.team_min_size)) {
    e.team_max_size = "Maximum team size must be at least the minimum.";
  }
  if (f.scoring_mode === "automatic") {
    if (!f.metric) e["evaluation.metric"] = "Choose a metric.";
    if ((f.id_column.trim() || "id") === (f.target_column.trim() || "target")) e["evaluation.target_column"] = "Id and target columns must differ.";
  }
  if (f.organizer_contact_email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.organizer_contact_email.trim())) {
    e.organizer_contact_email = "Enter a valid email address.";
  }
  f.starter_assets.forEach((a, i) => {
    if ((a.label.trim() || a.url.trim()) && !/^https?:\/\/\S+$/i.test(a.url.trim())) e[`starter_assets.${i}.url`] = "Use a full http(s):// URL.";
    if (a.url.trim() && !a.label.trim()) e[`starter_assets.${i}.label`] = "Add a label.";
  });
  f.faq.forEach((x, i) => {
    if (x.q.trim() && !x.a.trim()) e[`faq.${i}.a`] = "Add an answer.";
    if (x.a.trim() && !x.q.trim()) e[`faq.${i}.q`] = "Add a question.";
  });
  return e;
}

/* ------------------------------------------------------------------ section components */

export interface SectionProps {
  form: CompetitionFormState;
  set: (patch: Partial<CompetitionFormState>) => void;
  errors: FieldErrors;
  /** Returns true when the API field is read-only (finalized / scoring locked). */
  locked?: (apiField: string) => boolean;
}

const never = () => false;

function ChoiceCards({ legend, name, value, options, onChange, disabled, error }: {
  legend: ReactNode;
  name: string;
  value: string;
  options: { value: string; label: string; description: string }[];
  onChange: (v: string) => void;
  disabled?: boolean;
  error?: string;
}) {
  return (
    <fieldset disabled={disabled} className="disabled:opacity-60">
      <legend className="mb-2 text-sm font-medium text-fg">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((o) => {
          const checked = value === o.value;
          return (
            <label
              key={o.value}
              className={cn(
                "flex cursor-pointer gap-3 rounded-[var(--radius-md)] border p-3 transition-colors",
                checked ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong",
                disabled && "cursor-not-allowed",
              )}
            >
              <input type="radio" name={name} value={o.value} checked={checked} onChange={() => onChange(o.value)} className="mt-1 accent-[var(--accent)]" />
              <span>
                <span className="block text-sm font-medium text-fg">{o.label}</span>
                <span className="mt-0.5 block text-xs text-muted">{o.description}</span>
              </span>
            </label>
          );
        })}
      </div>
      {error ? <p role="alert" className="mt-1.5 text-xs font-medium text-danger">{error}</p> : null}
    </fieldset>
  );
}

export interface HostOption {
  id: string;
  name: string;
  role: string;
}

export function BasicsSection({ form, set, errors, locked = never, hosts, hostsLoading, allowNoHost }: SectionProps & {
  hosts?: HostOption[];
  hostsLoading?: boolean;
  allowNoHost?: boolean;
}) {
  return (
    <div className="grid gap-5">
      <Field label="Title" required error={fieldError(errors, "title")} hint="3–140 characters.">
        {(p) => <Input {...p} value={form.title} maxLength={140} disabled={locked("title")} onChange={(e) => set({ title: e.target.value })} />}
      </Field>
      <Field label="Summary" error={fieldError(errors, "summary")} hint={`One or two sentences shown on cards. ${form.summary.length}/280 (at least 20 to publish).`}>
        {(p) => <Textarea {...p} rows={2} value={form.summary} maxLength={280} disabled={locked("summary")} onChange={(e) => set({ summary: e.target.value })} />}
      </Field>
      <div className="grid gap-5 sm:grid-cols-3">
        <Field label="Event type" error={fieldError(errors, "event_type")}>
          {(p) => (
            <Select {...p} value={form.event_type} disabled={locked("event_type")} onChange={(e) => set({ event_type: e.target.value })}>
              {EVENT_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Task type" error={fieldError(errors, "task_type")}>
          {(p) => (
            <Select {...p} value={form.task_type} disabled={locked("task_type")} onChange={(e) => set({ task_type: e.target.value })}>
              {TASK_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Difficulty" error={fieldError(errors, "difficulty")}>
          {(p) => (
            <Select {...p} value={form.difficulty} disabled={locked("difficulty")} onChange={(e) => set({ difficulty: e.target.value })}>
              {DIFFICULTIES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          )}
        </Field>
      </div>
      <Field label="Tags" error={fieldError(errors, "tags")} hint="Press Enter or comma after each tag (up to 12).">
        {(p) => (locked("tags") ? <Input {...p} value={form.tags.join(", ")} disabled readOnly /> : <TagInput id={p.id} value={form.tags} onChange={(tags) => set({ tags })} placeholder="tabular, beginner-friendly…" />)}
      </Field>
      {hosts !== undefined || hostsLoading ? (
        <Field
          label="Host organization"
          required={!allowNoHost}
          error={fieldError(errors, "host_org_id")}
          hint="Competitions are hosted by an organization you manage (owner, admin or manager)."
        >
          {(p) => (
            <Select {...p} value={form.host_org_id} disabled={hostsLoading || locked("host_org_id")} onChange={(e) => set({ host_org_id: e.target.value })}>
              <option value="" disabled={!allowNoHost}>
                {hostsLoading ? "Loading your organizations…" : allowNoHost ? "No host (platform event)" : "Choose an organization"}
              </option>
              {form.host_org_id && hosts && !hosts.some((h) => h.id === form.host_org_id) ? <option value={form.host_org_id}>Current host</option> : null}
              {hosts?.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name} ({h.role})
                </option>
              ))}
            </Select>
          )}
        </Field>
      ) : null}
    </div>
  );
}

export function ScheduleSection({ form, set, errors, locked = never }: SectionProps) {
  const tz = form.timezone || "UTC";
  return (
    <div className="grid gap-5">
      <Field
        label="Time zone"
        error={fieldError(errors, "timezone")}
        hint="Times below are entered in this zone. Participants always see deadlines in their own local time. Changing the zone keeps the wall-clock times and moves the actual instants."
      >
        {(p) => <TimeZoneSelect {...p} value={tz} disabled={locked("timezone")} onChange={(timezone) => set({ timezone })} />}
      </Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <ZonedDateTimeField label="Starts" timeZone={tz} value={form.starts_at} onChange={(v) => set({ starts_at: v })} error={fieldError(errors, "starts_at")} disabled={locked("starts_at")} hint="Submissions open at this time (required to publish)." />
        <ZonedDateTimeField label="Ends" timeZone={tz} value={form.ends_at} onChange={(v) => set({ ends_at: v })} error={fieldError(errors, "ends_at")} disabled={locked("ends_at")} hint="Submissions close at this instant (exclusive)." />
        <ZonedDateTimeField label="Registration opens" timeZone={tz} value={form.registration_opens_at} onChange={(v) => set({ registration_opens_at: v })} error={fieldError(errors, "registration_opens_at")} disabled={locked("registration_opens_at")} hint="Optional — defaults to publication." />
        <ZonedDateTimeField label="Registration closes" timeZone={tz} value={form.registration_closes_at} onChange={(v) => set({ registration_closes_at: v })} error={fieldError(errors, "registration_closes_at")} disabled={locked("registration_closes_at")} hint="Optional — defaults to the end." />
        <ZonedDateTimeField label="Team changes lock" timeZone={tz} value={form.team_lock_at} onChange={(v) => set({ team_lock_at: v })} error={fieldError(errors, "team_lock_at")} disabled={locked("team_lock_at")} hint="Optional — after this, teams can't merge or change members." />
      </div>
    </div>
  );
}

export function ParticipationSection({ form, set, errors, locked = never }: SectionProps) {
  const num = (key: keyof CompetitionFormState, label: string, hint: string, min: number, max: number, placeholder?: string) => (
    <Field label={label} error={fieldError(errors, key)} hint={hint}>
      {(p) => (
        <Input
          {...p}
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          placeholder={placeholder}
          value={String(form[key])}
          disabled={locked(key)}
          onChange={(e) => set({ [key]: e.target.value } as Partial<CompetitionFormState>)}
        />
      )}
    </Field>
  );
  return (
    <div className="grid gap-6">
      <ChoiceCards
        legend="Visibility"
        name="visibility"
        value={form.visibility}
        options={VISIBILITIES}
        onChange={(visibility) => set({ visibility })}
        disabled={locked("visibility")}
        error={fieldError(errors, "visibility")}
      />
      {form.visibility === "invite_only" || form.visibility === "private" ? (
        <InlineNotice tone="info">An invite code is generated when you publish. You can share or rotate it from the overview page.</InlineNotice>
      ) : null}
      <div className="grid gap-5 sm:grid-cols-2">
        {num("team_min_size", "Minimum team size", "1 lets people compete solo.", 1, 20)}
        {num("team_max_size", "Maximum team size", "Up to 20 members.", 1, 20)}
        {num("daily_submission_limit", "Daily submissions per team", "Resets at 00:00 UTC.", 1, 100)}
        {num("total_submission_limit", "Total submissions per team", "Leave blank for no overall limit.", 1, 10000, "Unlimited")}
        {num("max_submission_mb", "Max submission size (MB)", "Per file, 1–200 MB.", 1, 200)}
        {num("final_selection_limit", "Final submissions a team may select", "Used for the private leaderboard (1–5).", 1, 5)}
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Format" error={fieldError(errors, "format")}>
          {(p) => (
            <Select {...p} value={form.format} disabled={locked("format")} onChange={(e) => set({ format: e.target.value })}>
              {FORMATS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          )}
        </Field>
        {form.format !== "online" ? (
          <Field label="Venue name" error={fieldError(errors, "venue_name")}>
            {(p) => <Input {...p} value={form.venue_name} maxLength={160} disabled={locked("venue_name")} onChange={(e) => set({ venue_name: e.target.value })} />}
          </Field>
        ) : null}
      </div>
      {form.format !== "online" ? (
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Venue address" error={fieldError(errors, "venue_address")}>
            {(p) => <Input {...p} value={form.venue_address} maxLength={300} disabled={locked("venue_address")} onChange={(e) => set({ venue_address: e.target.value })} />}
          </Field>
          <Field label="Venue notes" error={fieldError(errors, "venue_notes")} hint="Access, parking, what to bring.">
            {(p) => <Textarea {...p} rows={2} value={form.venue_notes} maxLength={5000} disabled={locked("venue_notes")} onChange={(e) => set({ venue_notes: e.target.value })} />}
          </Field>
        </div>
      ) : null}
    </div>
  );
}

export function ScoringSection({ form, set, errors, locked = never, config }: SectionProps & { config?: PublicConfig }) {
  const metrics = config?.metrics ?? [];
  const metric = metrics.find((m) => m.key === form.metric);
  const evalLocked = locked("evaluation");
  return (
    <div className="grid gap-6">
      <ChoiceCards
        legend="Scoring mode"
        name="scoring_mode"
        value={form.scoring_mode}
        options={SCORING_MODES}
        onChange={(scoring_mode) => set({ scoring_mode })}
        disabled={locked("scoring_mode")}
        error={fieldError(errors, "scoring_mode")}
      />
      {form.scoring_mode === "automatic" ? (
        <div className="grid gap-5">
          {config && config.evaluators.length > 1 ? (
            <Field label="Evaluator" error={fieldError(errors, "evaluation.evaluator")}>
              {(p) => (
                <Select {...p} value={form.evaluator} disabled={evalLocked} onChange={(e) => set({ evaluator: e.target.value })}>
                  {config.evaluators.map((ev) => <option key={ev.key} value={ev.key}>{ev.label} (v{ev.version})</option>)}
                </Select>
              )}
            </Field>
          ) : null}
          <Field
            label="Primary metric"
            required
            error={fieldError(errors, "evaluation.metric")}
            hint={metric ? `${metric.description} ${metric.direction === "maximize" ? "Higher is better." : "Lower is better."}` : "Loading metrics…"}
          >
            {(p) => (
              <Select {...p} value={form.metric} disabled={evalLocked || !metrics.length} onChange={(e) => set({ metric: e.target.value, secondary_metrics: form.secondary_metrics.filter((m) => m !== e.target.value) })}>
                {!metrics.length ? <option value={form.metric}>{form.metric}</option> : null}
                {metrics.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label} — {m.direction === "maximize" ? "maximize ↑" : "minimize ↓"}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Id column" error={fieldError(errors, "evaluation.id_column")} hint="Row identifier in the ground truth and submissions.">
              {(p) => <Input {...p} value={form.id_column} maxLength={64} disabled={evalLocked} onChange={(e) => set({ id_column: e.target.value })} className="font-mono" />}
            </Field>
            <Field label="Target column" error={fieldError(errors, "evaluation.target_column")} hint="Column holding the prediction / true value.">
              {(p) => <Input {...p} value={form.target_column} maxLength={64} disabled={evalLocked} onChange={(e) => set({ target_column: e.target.value })} className="font-mono" />}
            </Field>
            {metric?.kind === "probability" ? (
              <Field label="Positive label" error={fieldError(errors, "evaluation.positive_label")} hint="Ground-truth value treated as the positive class.">
                {(p) => <Input {...p} value={form.positive_label} maxLength={64} disabled={evalLocked} onChange={(e) => set({ positive_label: e.target.value })} className="font-mono" />}
              </Field>
            ) : null}
            <Field label="Expected row count" error={fieldError(errors, "evaluation.expected_row_count")} hint="Optional — reject submissions with a different number of rows.">
              {(p) => <Input {...p} type="number" min={1} value={form.expected_row_count} disabled={evalLocked} onChange={(e) => set({ expected_row_count: e.target.value })} />}
            </Field>
          </div>
          {metrics.length > 1 ? (
            <fieldset disabled={evalLocked} className="disabled:opacity-60">
              <legend className="text-sm font-medium text-fg">Secondary metrics</legend>
              <p className="mb-2 text-xs text-muted">Reported alongside the primary metric (up to 3). They never affect ranking.</p>
              <div className="flex flex-wrap gap-2">
                {metrics.filter((m) => m.key !== form.metric).map((m) => {
                  const on = form.secondary_metrics.includes(m.key);
                  const full = !on && form.secondary_metrics.length >= 3;
                  return (
                    <button
                      key={m.key}
                      type="button"
                      aria-pressed={on}
                      disabled={full || evalLocked}
                      title={m.description}
                      onClick={() => set({ secondary_metrics: on ? form.secondary_metrics.filter((x) => x !== m.key) : [...form.secondary_metrics, m.key] })}
                      className={cn(
                        "rounded-full border px-3 py-1 text-sm transition-colors disabled:opacity-50",
                        on ? "border-accent bg-accent-soft text-accent-strong" : "border-border text-muted hover:text-fg",
                      )}
                    >
                      {m.label} {m.direction === "maximize" ? "↑" : "↓"}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          ) : null}
          <Switch
            checked={form.strict_schema}
            disabled={evalLocked}
            onChange={(strict_schema) => set({ strict_schema })}
            label="Strict schema"
            description="Reject submissions that contain columns other than the id and target columns."
          />
          <Field label="Leaderboard visibility" error={fieldError(errors, "leaderboard_visibility")}>
            {(p) => (
              <Select {...p} value={form.leaderboard_visibility} disabled={locked("leaderboard_visibility")} onChange={(e) => set({ leaderboard_visibility: e.target.value })}>
                {LEADERBOARD_VISIBILITY.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            )}
          </Field>
        </div>
      ) : form.scoring_mode === "judged" ? (
        <InlineNotice tone="info" title="Judging is configured after creation">
          Define the rubric, assign judges and schedule presentations from the Judging tab of the manage area. The public leaderboard stays hidden until results are finalized.
        </InlineNotice>
      ) : (
        <InlineNotice tone="info">No leaderboard or scores — participation certificates can still be issued to people who joined and took part.</InlineNotice>
      )}
      <Switch
        checked={form.show_university_on_leaderboard}
        disabled={locked("show_university_on_leaderboard")}
        onChange={(show_university_on_leaderboard) => set({ show_university_on_leaderboard })}
        label="Show universities on the leaderboard"
        description="Displays each member's verified university next to their name."
      />
      <fieldset disabled={locked("certificate_rules")} className="grid gap-4 rounded-[var(--radius-md)] border border-border p-4 disabled:opacity-60">
        <legend className="px-1 text-sm font-medium text-fg">Certificates</legend>
        <Switch
          checked={form.cert_participation}
          onChange={(cert_participation) => set({ cert_participation })}
          label="Participation certificates"
          description="For members of teams with at least one valid entry."
        />
        <Field label="Award certificates for the top N" error={fieldError(errors, "certificate_rules")} hint="Ranks 1–N receive an award certificate. Use 0 to disable.">
          {(p) => <Input {...p} type="number" min={0} max={1000} value={form.cert_award_top_n} onChange={(e) => set({ cert_award_top_n: e.target.value })} className="sm:max-w-40" />}
        </Field>
      </fieldset>
    </div>
  );
}

export function ContentSection({ form, set, errors, locked = never }: SectionProps) {
  const md = (key: "description_md" | "rules_md" | "evaluation_md" | "prize_md" | "scoring_notes_md", label: string, hint: string, rows = 10, max = 100000) => (
    <Field label={label} error={fieldError(errors, key)} hint={hint}>
      {(p) =>
        locked(key) ? (
          <Textarea {...p} value={form[key]} disabled readOnly rows={4} />
        ) : (
          <MarkdownEditor id={p.id} aria-invalid={p["aria-invalid"]} aria-describedby={p["aria-describedby"]} value={form[key]} onChange={(v) => set({ [key]: v } as Partial<CompetitionFormState>)} rows={rows} maxLength={max} />
        )
      }
    </Field>
  );
  const moveFaq = (i: number, d: -1 | 1) => {
    const next = [...form.faq];
    const j = i + d;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    set({ faq: next });
  };
  return (
    <div className="grid gap-6">
      {md("description_md", "Problem statement", "What participants will build and why it matters (at least 50 characters to publish).", 12)}
      {md("rules_md", "Rules", "Eligibility, external data, team rules, code of conduct (at least 20 characters to publish).", 10)}
      {form.scoring_mode === "automatic" ? md("evaluation_md", "Evaluation", "How submissions are scored, the submission format and the public/private split.", 8, 50000) : null}
      {md("scoring_notes_md", "Scoring notes", "Optional clarifications shown next to the leaderboard.", 4, 20000)}

      <div className="grid gap-4 rounded-[var(--radius-md)] border border-border p-4">
        <Switch checked={form.has_prize} disabled={locked("has_prize")} onChange={(has_prize) => set({ has_prize })} label="This competition has prizes" />
        {form.has_prize ? (
          <>
            <Field label="Prize summary" error={fieldError(errors, "prize_summary")} hint="Short text shown on cards, e.g. “$1,000 + internships”.">
              {(p) => <Input {...p} value={form.prize_summary} maxLength={120} disabled={locked("prize_summary")} onChange={(e) => set({ prize_summary: e.target.value })} />}
            </Field>
            {md("prize_md", "Prize details", "Amounts, eligibility and how prizes are delivered.", 6, 20000)}
          </>
        ) : null}
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium text-fg">FAQ</h3>
            <p className="text-xs text-muted">Common questions, shown on the overview page.</p>
          </div>
          <Button size="sm" variant="secondary" icon={<Plus className="h-4 w-4" />} disabled={locked("faq") || form.faq.length >= 30} onClick={() => set({ faq: [...form.faq, { q: "", a: "" }] })}>
            Add question
          </Button>
        </div>
        {form.faq.length === 0 ? <p className="rounded-[var(--radius-md)] border border-dashed border-border px-4 py-3 text-sm text-subtle">No questions yet.</p> : null}
        <ol className="grid gap-3">
          {form.faq.map((item, i) => (
            <li key={i} className="grid gap-3 rounded-[var(--radius-md)] border border-border p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-subtle">Question {i + 1}</span>
                <div className="flex gap-1">
                  <Button size="icon" variant="ghost" aria-label={`Move question ${i + 1} up`} disabled={i === 0 || locked("faq")} onClick={() => moveFaq(i, -1)}><ArrowUp className="h-4 w-4" /></Button>
                  <Button size="icon" variant="ghost" aria-label={`Move question ${i + 1} down`} disabled={i === form.faq.length - 1 || locked("faq")} onClick={() => moveFaq(i, 1)}><ArrowDown className="h-4 w-4" /></Button>
                  <Button size="icon" variant="ghost" aria-label={`Remove question ${i + 1}`} disabled={locked("faq")} onClick={() => set({ faq: form.faq.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
              <Field label="Question" error={fieldError(errors, `faq.${i}.q`)}>
                {(p) => <Input {...p} value={item.q} maxLength={300} disabled={locked("faq")} onChange={(e) => set({ faq: form.faq.map((f, j) => (j === i ? { ...f, q: e.target.value } : f)) })} />}
              </Field>
              <Field label="Answer" error={fieldError(errors, `faq.${i}.a`)}>
                {(p) => <Textarea {...p} rows={3} value={item.a} maxLength={4000} disabled={locked("faq")} onChange={(e) => set({ faq: form.faq.map((f, j) => (j === i ? { ...f, a: e.target.value } : f)) })} />}
              </Field>
            </li>
          ))}
        </ol>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium text-fg">Starter assets</h3>
            <p className="text-xs text-muted">Links to starter notebooks, repos or docs. Changing them bumps the starter-asset version.</p>
          </div>
          <Button size="sm" variant="secondary" icon={<Plus className="h-4 w-4" />} disabled={locked("starter_assets") || form.starter_assets.length >= 20} onClick={() => set({ starter_assets: [...form.starter_assets, { label: "", url: "", kind: "notebook" }] })}>
            Add link
          </Button>
        </div>
        {form.starter_assets.length === 0 ? <p className="rounded-[var(--radius-md)] border border-dashed border-border px-4 py-3 text-sm text-subtle">No starter assets.</p> : null}
        <ul className="grid gap-3">
          {form.starter_assets.map((a, i) => (
            <li key={i} className="grid gap-3 rounded-[var(--radius-md)] border border-border p-3 sm:grid-cols-[1fr_2fr_9rem_auto] sm:items-start">
              <Field label="Label" error={fieldError(errors, `starter_assets.${i}.label`)}>
                {(p) => <Input {...p} value={a.label} maxLength={120} disabled={locked("starter_assets")} onChange={(e) => set({ starter_assets: form.starter_assets.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />}
              </Field>
              <Field label="URL" error={fieldError(errors, `starter_assets.${i}.url`) ?? (i === 0 ? fieldError(errors, "starter_assets") : undefined)}>
                {(p) => <Input {...p} type="url" placeholder="https://" value={a.url} maxLength={500} disabled={locked("starter_assets")} onChange={(e) => set({ starter_assets: form.starter_assets.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)) })} />}
              </Field>
              <Field label="Kind">
                {(p) => (
                  <Select {...p} value={a.kind} disabled={locked("starter_assets")} onChange={(e) => set({ starter_assets: form.starter_assets.map((x, j) => (j === i ? { ...x, kind: e.target.value as StarterKind } : x)) })}>
                    {STARTER_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                  </Select>
                )}
              </Field>
              <Button size="icon" variant="ghost" className="sm:mt-6" aria-label={`Remove link ${i + 1}`} disabled={locked("starter_assets")} onClick={() => set({ starter_assets: form.starter_assets.filter((_, j) => j !== i) })}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Organizer contact email" error={fieldError(errors, "organizer_contact_email")} hint="Shown to participants for questions. Optional.">
          {(p) => <Input {...p} type="email" value={form.organizer_contact_email} disabled={locked("organizer_contact_email")} onChange={(e) => set({ organizer_contact_email: e.target.value })} />}
        </Field>
        <Field label={<>Organizer notes <Badge tone="outline" className="ml-1">Staff only</Badge></>} error={fieldError(errors, "organizer_notes")} hint="Private notes for your team — never shown to participants.">
          {(p) => <Textarea {...p} rows={3} value={form.organizer_notes} maxLength={20000} disabled={locked("organizer_notes")} onChange={(e) => set({ organizer_notes: e.target.value })} />}
        </Field>
      </div>
    </div>
  );
}

/** API field names grouped by form section — used to jump to the section containing a server error. */
export const SECTION_FIELDS: Record<"basics" | "schedule" | "participation" | "scoring" | "content", string[]> = {
  basics: ["title", "summary", "event_type", "task_type", "difficulty", "tags", "host_org_id", "parent_id", "cover_style"],
  schedule: ["timezone", "starts_at", "ends_at", "registration_opens_at", "registration_closes_at", "team_lock_at"],
  participation: ["visibility", "team_min_size", "team_max_size", "daily_submission_limit", "total_submission_limit", "max_submission_mb", "final_selection_limit", "format", "venue_name", "venue_address", "venue_notes"],
  scoring: ["scoring_mode", "evaluation", "leaderboard_visibility", "show_university_on_leaderboard", "certificate_rules"],
  content: ["description_md", "rules_md", "evaluation_md", "scoring_notes_md", "has_prize", "prize_summary", "prize_md", "faq", "starter_assets", "organizer_contact_email", "organizer_notes", "dataset_version_id"],
};

export function sectionForField(key: string): keyof typeof SECTION_FIELDS | null {
  const root = key.split(".")[0];
  for (const [section, keys] of Object.entries(SECTION_FIELDS)) {
    if (keys.includes(root)) return section as keyof typeof SECTION_FIELDS;
  }
  return null;
}
