/**
 * Local types for the catalog areas (datasets, projects, open source). Several of these endpoints return ad-hoc
 * dicts that are not in the generated OpenAPI schema — shapes mirror backend/app/modules/{projects,opensource}/service.py.
 */
import type { OrgMini, ProjectCard, Schemas, UserMini } from "@/lib/types";

export type DatasetVersion = Schemas["DatasetVersionOut"];
export type DatasetFile = Schemas["DatasetFileOut"];

export interface DataDictionaryEntry {
  column: string;
  type: string;
  description: string;
}

/** GET /datasets/{slug}/files/{id}/preview — `preview` is built by `_preview()` for CSV/TSV files only. */
export interface FilePreview {
  file_id: string;
  filename: string;
  preview: { columns: string[]; rows: string[][]; truncated_columns?: boolean } | null;
  row_count: number | null;
}

/** GET /orgs/me/memberships */
export interface OrgMembership {
  org: OrgMini;
  role: string;
  status: string;
  verified: boolean;
  verification_method: string;
  joined_at: string;
}

export interface ProjectMember {
  user: UserMini;
  role: string;
}

export interface ProjectMedia {
  id: string;
  url: string;
  width: number;
  height: number;
  alt_text: string;
  position: number;
}

export interface ProjectRepository {
  full_name: string;
  html_url: string;
  stars: number | null;
  forks: number | null;
  open_issues: number | null;
  language: string | null;
  license: string | null;
  last_synced_at: string | null;
  sync_status: string;
  is_demo: boolean;
}

export interface ProjectDetail extends ProjectCard {
  description_html: string;
  description_md: string | null;
  paper_url: string | null;
  video_url: string | null;
  docs_url: string | null;
  org: OrgMini | null;
  members: ProjectMember[];
  media: ProjectMedia[];
  datasets: { slug: string; title: string }[];
  competition: { slug: string; title: string } | null;
  repository: ProjectRepository | null;
  discussion_count: number;
  taken_down: boolean;
  takedown_reason: string | null;
  viewer: { role: string | null; can_edit: boolean; is_owner: boolean; can_moderate: boolean };
  created_at: string;
}

/** Repository card from GET /opensource/repos and POST /opensource/repos. */
export interface RepoCard {
  id: string;
  full_name: string;
  name: string;
  owner_login: string;
  description: string | null;
  language: string | null;
  stars: number | null;
  forks: number | null;
  open_issues: number | null;
  topics: string[];
  html_url: string;
  license: string | null;
  is_archived: boolean;
  sync_status: string;
  last_synced_at: string | null;
  contributors_count: number | null;
  is_demo: boolean;
  project: { slug: string; title: string } | null;
}

export interface IssueRow {
  id: string;
  number: number;
  title: string;
  labels: string[];
  html_url: string;
  is_beginner_friendly: boolean;
  is_promoted: boolean;
  comments: number;
  created_at: string | null;
  updated_at: string | null;
  repo: { id: string; full_name: string; language: string | null; is_demo: boolean };
}

export interface OpenSourceOverview {
  stats: { repositories: number; open_issues: number; beginner_issues: number; merged_prs_90d: number };
  top_contributors: { user: UserMini; merged_prs: number }[];
  languages: { language: string; repositories: number }[];
  github: { oauth_enabled: boolean; webhooks_enabled: boolean };
  attribution_note: string;
}

export interface GitHubAccountStatus {
  connected: boolean;
  login: string | null;
  avatar_url: string | null;
  connected_at: string | null;
  scopes: string | null;
  oauth_enabled: boolean;
}

export interface ThreadRow {
  id: string;
  title: string;
  author: UserMini | null;
  pinned: boolean;
  locked: boolean;
  hidden: boolean;
  is_announcement: boolean;
  reply_count: number;
  has_accepted_answer: boolean;
  last_activity_at: string;
  created_at: string;
  deleted: boolean;
}

export interface ThreadList {
  items: ThreadRow[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
}
