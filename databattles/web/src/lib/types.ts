/**
 * Shared frontend types.
 *
 * `Schemas` re-exports the OpenAPI component schemas generated from the backend (`npm run gen:api`).
 * Endpoints that return ad-hoc aggregates (dashboards, profiles) are typed by hand below.
 */
import type { components } from "./api-schema";

export type Schemas = components["schemas"];

export type UserMini = Schemas["UserMini"];
export type OrgMini = Schemas["OrgMini"];
export type Me = Schemas["MeOut"];
export type CompetitionCard = Schemas["CompetitionCard"];
export type CompetitionDetail = Schemas["CompetitionDetail"];
export type CompetitionWrite = Schemas["CompetitionWrite"];
export type ViewerContext = Schemas["ViewerContext"];
export type SubmissionOut = Schemas["SubmissionOut"];
export type LeaderboardOut = Schemas["LeaderboardOut"];
export type LeaderboardRow = Schemas["LeaderboardRow"];
export type TeamOut = Schemas["TeamOut"];
export type DatasetCard = Schemas["DatasetCard"];
export type DatasetDetail = Schemas["DatasetDetail"];
export type CertificateOut = Schemas["CertificateOut"];
export type BadgeOut = Schemas["BadgeOut"];
export type Message = Schemas["Message"];

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  group_count: number;
  read_at: string | null;
  created_at: string;
}

export interface PublicConfig {
  app_name: string;
  demo_mode: boolean;
  env: string;
  auth_providers: string[];
  github_integration: boolean;
  limits: { dataset_file_mb: number; submission_mb: number; image_mb: number };
  metrics: { key: string; label: string; direction: "maximize" | "minimize"; kind: string; description: string }[];
  evaluators: { key: string; label: string; version: string; submission_format: string }[];
  flags: Record<string, boolean>;
}

export interface ProjectCard {
  id: string;
  slug: string;
  title: string;
  summary: string;
  tags: string[];
  technologies: string[];
  owner: UserMini | null;
  is_featured: boolean;
  is_open_source: boolean;
  repo_url: string | null;
  demo_url: string | null;
  cover_style: string;
  cover_image_url: string | null;
  visibility: string;
  status: string;
  maintainer_verified: boolean;
  updated_at: string;
  is_demo: boolean;
}

export interface OrgCard {
  id: string;
  slug: string;
  name: string;
  type: string;
  tagline: string | null;
  logo_url: string | null;
  accent_color: string | null;
  country: string | null;
  city: string | null;
  verification_status: string;
  member_count: number;
  is_demo: boolean;
}

export interface CourseCard {
  id: string;
  slug: string;
  title: string;
  summary: string;
  category: string;
  difficulty: string;
  estimated_minutes: number;
  tags: string[];
  org: OrgMini | null;
  cover_style: string;
  enrollment_count: number;
  lesson_count: number | null;
  status: string;
  visibility: string;
  issues_certificate: boolean;
  has_badge: boolean;
  is_demo: boolean;
  progress: { enrolled: boolean; percent: number; completed: boolean };
}

export interface SearchResult {
  type: string;
  id: string;
  title: string;
  subtitle: string | null;
  url: string;
  snippet: string;
  tags: string[];
  score: number;
}
