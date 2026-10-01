/**
 * Hand-written types for the ad-hoc dict endpoints used by the dashboard, profile and settings pages.
 * Source of truth: backend/app/modules/users/service.py (`dashboard()`, `public_profile()`), credentials/router.py,
 * opensource/service.py (`account_status()`).
 */
import type { OrgMini, Schemas } from "@/lib/types";

export const DASHBOARD_WIDGETS = [
  "competitions",
  "deadlines",
  "submissions",
  "invitations",
  "contributions",
  "credentials",
  "recommended",
  "recent",
  "learning",
  "activity",
  "completion",
] as const;
export type DashboardWidget = (typeof DASHBOARD_WIDGETS)[number];

export interface DashboardData {
  greeting_name: string;
  active_competitions: {
    slug: string;
    title: string;
    status: string;
    ends_at: string | null;
    team_name: string | null;
    is_solo: boolean;
    best_score: number | null;
    rank: number | null;
    ranked_teams: number | null;
    metric: string | null;
    cover_style: string;
  }[];
  deadlines: { title: string; at: string; url: string; kind: string }[];
  recent_submissions: {
    id: string;
    competition: { slug: string; title: string };
    status: string;
    public_score: number | null;
    submitted_at: string;
    error_message: string | null;
  }[];
  invitations: { id: string; team_name: string; competition: { slug: string; title: string }; expires_at: string | null }[];
  contribution_notifications: { id: string; title: string; link: string | null; created_at: string }[];
  recent_badges: { name: string; icon: string; color: string; awarded_at: string }[];
  recent_certificates: { public_id: string; event_title: string; result_label: string; issued_at: string }[];
  recommended: {
    slug: string;
    title: string;
    summary: string;
    ends_at: string | null;
    matched: string[];
    cover_style: string;
    task_type: string;
  }[];
  recommendation_basis: "declared_interests" | "upcoming_public";
  recently_viewed: { type: string; title: string; url: string; viewed_at: string }[];
  learning: { slug: string; title: string; progress_pct: number; resume_url: string }[];
  activity: Record<string, number>;
  profile_completion: { percent: number; checks: Record<string, boolean> };
  prefs: { hidden?: string[]; order?: string[] };
}

export interface ProfileResult {
  id: string;
  competition: { slug: string; title: string };
  rank: number | null;
  total_ranked: number;
  label: string | null;
  score: number | null;
  hidden: boolean;
}

export interface ProfileBadge {
  id: string;
  public_id: string;
  slug: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  category: string;
  rarity_label: string | null;
  awarded_at: string;
  manual: boolean;
  hidden: boolean;
}

export interface ProfileCertificate {
  public_id: string;
  event_title: string;
  result_label: string;
  issuer_name: string;
  issued_at: string;
  hidden: boolean;
  is_demo: boolean;
}

export interface ContributionItem {
  title: string;
  number: number;
  url: string;
  repo: string;
  merged_at: string | null;
  additions: number | null;
  deletions: number | null;
  is_demo: boolean;
}

export interface PublicProfile {
  id: string;
  handle: string;
  display_name: string;
  headline: string | null;
  bio_html: string | null;
  avatar_url: string | null;
  cover_style: string;
  website_url: string | null;
  joined_at: string;
  is_self: boolean;
  is_demo: boolean;
  indexable: boolean;
  verified: { university: OrgMini | null; github_login: string | null; email_verified: boolean };
  self_declared: {
    university: OrgMini | null;
    department: string | null;
    graduation_year: number | null;
    skills: string[];
    interests: string[];
  };
  stats: {
    competitions: number;
    top10_finishes: number;
    certificates: number;
    badges: number;
    projects: number;
    courses_completed: number;
    merged_prs: number;
  };
  results: ProfileResult[];
  competitions: { slug: string; title: string; status: string; joined_at: string }[];
  certificates: ProfileCertificate[];
  badges: ProfileBadge[];
  projects: {
    slug: string;
    title: string;
    summary: string;
    tags: string[];
    is_open_source: boolean;
    repo_url: string | null;
    demo_url: string | null;
    cover_style: string;
  }[];
  courses: { slug: string; title: string; completed_at: string }[];
  contributions: { github_login: string | null; merged_count: number; repositories: number; items: ContributionItem[] } | null;
  activity: Record<string, number> | null;
  timeline: { kind: string; title: string; url: string | null; at: string | null }[];
}

export type ProfileOut = Schemas["ProfileOut"];
export type MyCertificate = Schemas["MyCertificateOut"];
export type BadgeDef = Schemas["BadgeOut"];

export interface MyBadgeAward {
  id: string;
  public_id: string;
  badge: BadgeDef;
  awarded_at: string;
  evidence: Record<string, unknown> | null;
  hidden_on_profile: boolean;
  manual: boolean;
}

export interface GitHubAccountStatus {
  connected: boolean;
  login: string | null;
  avatar_url: string | null;
  connected_at: string | null;
  scopes: string | null;
  oauth_enabled: boolean;
}

export interface UniversityOption {
  id: string;
  name: string;
  slug: string;
  email_domains: string[];
  departments: { id: string; name: string }[];
}

export const COVER_STYLES = ["aurora", "nebula", "circuit", "dunes", "mono", "sunrise"] as const;
