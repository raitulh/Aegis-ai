"use client";

import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { Field, Input, Select } from "@/components/ui/form";
import { API_BASE, get, type FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { useDebounced } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail, Page, Schemas } from "@/lib/types";
import { timeZoneOptions, zonedInputToUtc, zoneLabel } from "./datetime";

export type CompetitionManage = Schemas["CompetitionManage"];
export type PublishCheck = Schemas["PublishCheck"];
export type TeamListItem = Schemas["TeamListItem"];

/* ------------------------------------------------------------------ query keys & hooks */

export const manageKey = (slug: string) => ["competitions", slug, "manage"] as const;
export const publishChecksKey = (slug: string) => ["competitions", slug, "publish-checks"] as const;

/** Organizer-only view (private config). 403/404 for everyone else. */
export function useManage(slug: string) {
  return useQuery({ queryKey: manageKey(slug), queryFn: () => get<CompetitionManage>(`/competitions/${slug}/manage`) });
}

/** Public detail (shared cache with the competition layout). */
export function useCompetitionDetail(slug: string) {
  return useQuery({ queryKey: qk.competition(slug), queryFn: () => get<CompetitionDetail>(`/competitions/${slug}`) });
}

/** Everything that changes when organizers edit a competition. */
export const competitionKeys = (slug: string) => [qk.competition(slug), manageKey(slug), publishChecksKey(slug)] as const;

export function useTeams(slug: string, q = "") {
  const dq = useDebounced(q.trim(), 250);
  return useQuery({
    queryKey: ["competitions", slug, "teams", { q: dq, page_size: 100 }],
    queryFn: () => get<Page<TeamListItem>>(`/competitions/${slug}/teams`, { page_size: 100, q: dq || undefined }),
    staleTime: 30_000,
  });
}

/* ------------------------------------------------------------------ helpers */

/** Error for `key`, falling back to nested keys such as `faq.0.q` or `evaluation.metric`. */
export function fieldError(fields: FieldErrors | undefined, key: string): string | undefined {
  if (!fields) return undefined;
  if (fields[key]) return fields[key];
  const nested = Object.keys(fields).find((k) => k.startsWith(`${key}.`));
  return nested ? fields[nested] : undefined;
}

export function isTerminal(lifecycle: string | undefined): boolean {
  return lifecycle === "finalized" || lifecycle === "archived";
}

/* ------------------------------------------------------------------ constants */

export const EVENT_TYPES: { value: string; label: string }[] = [
  { value: "ml_competition", label: "ML competition" },
  { value: "datathon", label: "Datathon" },
  { value: "hackathon", label: "Hackathon" },
  { value: "coding_contest", label: "Coding contest" },
  { value: "research_challenge", label: "Research challenge" },
  { value: "workshop", label: "Workshop" },
  { value: "demo_day", label: "Demo day" },
  { value: "practice", label: "Practice" },
];

export const TASK_TYPES: { value: string; label: string }[] = [
  { value: "classification", label: "Classification" },
  { value: "regression", label: "Regression" },
  { value: "nlp", label: "NLP" },
  { value: "computer_vision", label: "Computer vision" },
  { value: "data_analysis", label: "Data analysis" },
  { value: "coding", label: "Coding" },
  { value: "research", label: "Research" },
  { value: "hackathon", label: "Hackathon" },
  { value: "other", label: "Other" },
];

export const DIFFICULTIES = [
  { value: "beginner", label: "Beginner" },
  { value: "intermediate", label: "Intermediate" },
  { value: "advanced", label: "Advanced" },
];

export const VISIBILITIES: { value: string; label: string; description: string }[] = [
  { value: "public", label: "Public", description: "Listed in discovery. Anyone signed in can view and join." },
  { value: "university", label: "University members", description: "Listed only for members of the host organization; joining requires verified membership." },
  { value: "invite_only", label: "Invite only", description: "Unlisted but viewable by link. Joining requires the invite code." },
  { value: "private", label: "Private", description: "Hidden from everyone except staff and invited participants." },
];

export const SCORING_MODES: { value: string; label: string; description: string }[] = [
  { value: "automatic", label: "Automatic", description: "Participants upload CSV predictions that are scored against your hidden ground truth." },
  { value: "judged", label: "Judged", description: "Teams submit a project; judges score it with your rubric." },
  { value: "none", label: "No scoring", description: "Participation-only events such as workshops." },
];

export const LEADERBOARD_VISIBILITY = [
  { value: "visible", label: "Visible — scores and ranks" },
  { value: "ranks_only", label: "Ranks only — scores hidden" },
  { value: "hidden", label: "Hidden until results are final" },
];

export const FORMATS = [
  { value: "online", label: "Online" },
  { value: "offline", label: "In person" },
  { value: "hybrid", label: "Hybrid" },
];

export const STARTER_KINDS = ["colab", "kaggle", "notebook", "github", "docs", "other"] as const;
export const SCHEDULE_KINDS = ["session", "workshop", "deadline", "presentation", "ceremony", "checkin"] as const;
export const SPONSOR_TIERS = ["title", "platinum", "gold", "silver", "partner", "community"] as const;

/* ------------------------------------------------------------------ small UI pieces */

/** Section heading for manage pages (the competition layout owns the page's h1). */
export function ManageHeading({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h2 className="text-xl font-semibold tracking-tight text-fg">{title}</h2>
        {description ? <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

/** Same-origin CSV download (the session cookie authorizes it). */
export function DownloadLink({ path, children, className }: { path: string; children: ReactNode; className?: string }) {
  return (
    <a
      href={`${API_BASE}${path}`}
      download
      className={cn(
        "inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] border border-border-strong px-3 text-sm font-medium text-fg transition-colors hover:bg-surface-2",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
        className,
      )}
    >
      <Download className="h-4 w-4" aria-hidden />
      {children}
    </a>
  );
}

export function TimeZoneSelect({ value, onChange, disabled, ...aria }: {
  value: string;
  onChange: (tz: string) => void;
  disabled?: boolean;
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}) {
  const options = useMemo(() => {
    const all = timeZoneOptions();
    const zones = value && !all.includes(value) ? [value, ...all] : all;
    const now = new Date();
    return zones.map((z) => (
      <option key={z} value={z}>
        {z.replace(/_/g, " ")} ({zoneLabel(z, now)})
      </option>
    ));
  }, [value]);
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} {...aria}>
      {options}
    </Select>
  );
}

/** datetime-local input interpreted in `timeZone`; the hint shows the instant in the viewer's own zone. */
export function ZonedDateTimeField({
  label,
  value,
  onChange,
  timeZone,
  error,
  hint,
  required,
  disabled,
}: {
  label: ReactNode;
  value: string;
  onChange: (v: string) => void;
  timeZone: string;
  error?: string;
  hint?: ReactNode;
  required?: boolean;
  disabled?: boolean;
}) {
  const iso = zonedInputToUtc(value, timeZone);
  return (
    <Field
      label={label}
      required={required}
      error={error}
      hint={
        <>
          {hint ? <span className="block">{hint}</span> : null}
          {iso ? <span>Your time: {formatDateTime(iso)}</span> : <span>Entered in {timeZone.replace(/_/g, " ")}.</span>}
        </>
      }
    >
      {(p) => <Input {...p} type="datetime-local" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} />}
    </Field>
  );
}

/** Searchable team select backed by GET /competitions/{slug}/teams. */
export function TeamPicker({
  slug,
  value,
  onChange,
  allowNone,
  noneLabel = "— None —",
  label = "Team",
  error,
  hint,
}: {
  slug: string;
  value: string;
  onChange: (teamId: string) => void;
  allowNone?: boolean;
  noneLabel?: string;
  label?: ReactNode;
  error?: string;
  hint?: ReactNode;
}) {
  const [q, setQ] = useState("");
  const teams = useTeams(slug, q);
  const items = teams.data?.items ?? [];
  return (
    <Field label={label} error={error} hint={hint ?? (teams.data && teams.data.total > items.length ? `Showing ${items.length} of ${teams.data.total} teams — search to narrow down.` : undefined)}>
      {(p) => (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="Search teams"
            placeholder="Search teams…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="sm:max-w-48"
          />
          <Select {...p} value={value} onChange={(e) => onChange(e.target.value)} disabled={teams.isPending}>
            {allowNone ? <option value="">{noneLabel}</option> : <option value="" disabled>{teams.isPending ? "Loading teams…" : "Choose a team"}</option>}
            {value && !items.some((t) => t.id === value) ? <option value={value}>Selected team</option> : null}
            {items.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.is_solo ? " (solo)" : ` · ${t.member_count} members`}
              </option>
            ))}
          </Select>
        </div>
      )}
    </Field>
  );
}

/** Simple checklist row used by publish checks and finalize prerequisites. */
export function CheckRow({ ok, label, detail, required = true }: { ok: boolean; label: ReactNode; detail?: ReactNode; required?: boolean }) {
  return (
    <li className="flex items-start gap-3 py-2.5">
      <span
        className={cn(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
          ok ? "bg-success-soft text-success" : required ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning",
        )}
        aria-hidden
      >
        {ok ? "✓" : required ? "!" : "–"}
      </span>
      <div className="min-w-0 text-sm">
        <p className="font-medium text-fg">
          {label}
          <span className="sr-only">{ok ? " — done" : required ? " — required, not done" : " — recommended, not done"}</span>
          {!required ? <span className="ml-2 text-xs font-normal text-subtle">Recommended</span> : null}
        </p>
        {detail ? <p className="mt-0.5 text-xs text-muted">{detail}</p> : null}
      </div>
    </li>
  );
}
