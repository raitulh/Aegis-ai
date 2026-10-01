import type { Schemas, UserMini } from "@/lib/types";

export interface Criterion {
  key: string;
  label: string;
  description?: string | null;
  min: number;
  max: number;
  weight: number;
  allow_decimal: boolean;
}

export type RubricOut = Schemas["RubricOut"];

export interface JudgingOverview {
  rubric: Criterion[];
  rubric_version: number;
  reveal_scores_to_judges: boolean;
  blind_judging: boolean;
  locked: boolean;
  finalized: boolean;
  scores: {
    judge: UserMini | null;
    team_id: string;
    team_name: string;
    scores: Record<string, number> | null;
    feedback: string | null;
    weighted_total: number | null;
    status: string;
    submitted_at: string | null;
  }[];
  assignments: { id: string; judge: UserMini; team_id: string | null; panel: string | null }[];
  conflicts: { judge_id: string; team_id: string; reason: string }[];
  preview: Schemas["LeaderboardRow"][];
}

export interface ProjectSubmission {
  team_id: string;
  team_name: string;
  title: string;
  summary: string | null;
  description_html: string;
  repo_url: string | null;
  demo_url: string | null;
  video_url: string | null;
  submitted_at: string;
  updated_at: string;
}

export interface PresentationSlot {
  id: string;
  team_id: string | null;
  team_name: string | null;
  starts_at: string;
  ends_at: string;
  location: string | null;
  meeting_url: string | null;
  notes: string | null;
}

export const rubricKey = (slug: string) => ["competitions", slug, "rubric"] as const;
export const overviewKey = (slug: string) => ["competitions", slug, "judging", "overview"] as const;
export const projectSubmissionsKey = (slug: string) => ["competitions", slug, "project-submissions"] as const;
export const presentationsKey = (slug: string) => ["competitions", slug, "presentations"] as const;

export function toCriteria(raw: unknown[]): Criterion[] {
  return raw.map((r) => {
    const c = r as Record<string, unknown>;
    return {
      key: String(c.key ?? ""),
      label: String(c.label ?? c.key ?? ""),
      description: c.description ? String(c.description) : null,
      min: Number(c.min ?? 0),
      max: Number(c.max ?? 10),
      weight: Number(c.weight ?? 1),
      allow_decimal: Boolean(c.allow_decimal),
    };
  });
}

/**
 * Same formula as the server: each criterion is normalized to 0–1 within its range, weighted, averaged and
 * scaled to 0–100. Returns null until every criterion has a valid value.
 */
export function weightedTotal(criteria: Criterion[], scores: Record<string, number | undefined>): number | null {
  if (!criteria.length) return null;
  const totalWeight = criteria.reduce((n, c) => n + c.weight, 0);
  if (totalWeight <= 0) return null;
  let acc = 0;
  for (const c of criteria) {
    const v = scores[c.key];
    if (v === undefined || Number.isNaN(v) || v < c.min || v > c.max || c.max <= c.min) return null;
    acc += ((v - c.min) / (c.max - c.min)) * c.weight;
  }
  return Math.round((acc / totalWeight) * 100 * 100) / 100;
}
