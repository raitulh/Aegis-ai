/**
 * Local types for competition pages. Generated schemas are re-used where the API declares a response model;
 * ad-hoc dict responses (discussions, judging, dataset previews) are typed here from the backend services.
 */
import type { Schemas, UserMini } from "@/lib/types";

export type Announcement = Schemas["AnnouncementOut"];
export type Snapshot = Schemas["SnapshotOut"];
export type TeamListItem = Schemas["TeamListItem"];
export type TeamMember = Schemas["TeamMemberOut"];
export type PendingInvite = Schemas["PendingInviteOut"];
export type Invitation = Schemas["InvitationOut"];
export type DatasetFile = Schemas["DatasetFileOut"];
export type Rubric = Schemas["RubricOut"];
export type ScheduleItem = Schemas["ScheduleItemOut"];

/** Row returned by GET /discussions/threads (backend `discussions.service._thread_row`). */
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

export interface ThreadsResponse {
  items: ThreadRow[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
  context: { competition?: { slug: string; title: string }; can_moderate?: boolean };
}

/** `preview` payload of GET /datasets/{slug}/files/{id}/preview for CSV/TSV files. */
export interface TablePreview {
  columns: string[];
  rows: string[][];
  truncated_columns?: boolean;
}

/** Judged events: GET /competitions/{slug}/project-submissions rows. */
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

/** Judged events: GET /competitions/{slug}/presentations rows. */
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

/** Rubric criterion (judging service `_validate_criteria`). */
export interface Criterion {
  key: string;
  label: string;
  description?: string | null;
  min: number;
  max: number;
  weight: number;
}

/** Entry of `TeamOut.score_history` (leaderboards.service.team_score_history). */
export interface ScorePoint {
  submission_id: string;
  submitted_at: string;
  score: number | null;
}

/** Entry of `SubmissionOut.error_details` (evaluation.core.ValidationReport). */
export interface SubmissionIssue {
  code?: string;
  message?: string;
  row?: number;
}
