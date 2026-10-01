import type { CompetitionCard, ProjectCard } from "@/lib/types";

/** Response of GET /meta/landing. */
export interface Landing {
  stats: Record<string, number>;
  includes_demo_data: boolean;
  featured_competitions: CompetitionCard[];
  featured_projects: ProjectCard[];
}
