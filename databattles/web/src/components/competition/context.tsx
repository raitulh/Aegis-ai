"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { createContext, useContext, type ReactNode } from "react";

import { get } from "@/lib/api";
import { qk } from "@/lib/query";
import type { CompetitionDetail } from "@/lib/types";

/** Page-local query keys that are not part of the shared `qk` factory. */
export const ck = {
  announcements: (slug: string) => ["competitions", slug, "announcements"] as const,
  resultsHistory: (slug: string) => ["competitions", slug, "results-history"] as const,
  teams: (slug: string, filter?: object) => ["competitions", slug, "teams", filter ?? {}] as const,
  rubric: (slug: string) => ["competitions", slug, "rubric"] as const,
  projectSubmissions: (slug: string) => ["competitions", slug, "project-submissions"] as const,
  presentations: (slug: string) => ["competitions", slug, "presentations"] as const,
  myInvitations: ["me", "team-invitations"] as const,
};

export function useCompetitionQuery(slug: string) {
  return useQuery({
    queryKey: qk.competition(slug),
    queryFn: () => get<CompetitionDetail>(`/competitions/${encodeURIComponent(slug)}`),
    enabled: Boolean(slug),
  });
}

const CompetitionCtx = createContext<CompetitionDetail | null>(null);

export function CompetitionProvider({ value, children }: { value: CompetitionDetail; children: ReactNode }) {
  return <CompetitionCtx.Provider value={value}>{children}</CompetitionCtx.Provider>;
}

/**
 * The competition loaded by `/competitions/[slug]/layout.tsx`. Child pages only render once it is available,
 * so this never returns null inside the competition routes.
 */
export function useCompetition(): CompetitionDetail {
  const value = useContext(CompetitionCtx);
  if (!value) throw new Error("useCompetition must be used inside the competition layout");
  return value;
}

export function useSlug(): string {
  const params = useParams<{ slug: string }>();
  return params?.slug ?? "";
}
