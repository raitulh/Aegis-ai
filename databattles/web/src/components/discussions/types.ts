/**
 * Response shapes for the discussions endpoints (`backend/app/modules/discussions/service.py` returns dicts).
 */
import type { UserMini } from "@/lib/types";

export interface DiscussionCategory {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  staff_only_posting: boolean;
  thread_count: number;
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

export interface ThreadListContext {
  competition?: { slug: string; title: string };
  can_moderate?: boolean;
  project?: { slug: string; title: string };
  category?: { slug: string; name: string; staff_only_posting: boolean };
}

export interface ThreadPage {
  items: ThreadRow[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
  context: ThreadListContext;
}

export interface CommentOut {
  id: string;
  author: UserMini | null;
  body_html: string;
  /** Only returned to the comment's author (for editing). */
  body_md: string | null;
  reply_to_id: string | null;
  hidden: boolean;
  deleted: boolean;
  edited_at: string | null;
  created_at: string;
  is_accepted: boolean;
  can_edit: boolean;
  can_delete: boolean;
}

export interface ThreadViewer {
  can_reply: boolean;
  can_edit: boolean;
  can_moderate: boolean;
  can_accept: boolean;
  muted: boolean;
  signed_in: boolean;
}

export interface ThreadDetail {
  id: string;
  title: string;
  body_html: string;
  body_md: string | null;
  author: UserMini | null;
  pinned: boolean;
  locked: boolean;
  hidden: boolean;
  deleted: boolean;
  is_announcement: boolean;
  reply_count: number;
  created_at: string;
  edited_at: string | null;
  last_activity_at: string;
  category: { slug: string; name: string } | null;
  competition: { slug: string; title: string } | null;
  project: { slug: string; title: string } | null;
  accepted_comment: CommentOut | null;
  comments: { items: CommentOut[]; total: number; page: number; page_size: number; has_next: boolean };
  viewer: ThreadViewer;
  edit_note: string;
}

export interface Revision {
  id: string;
  title: string | null;
  body_md: string | null;
  created_at: string;
}

export const REPORT_REASONS = [
  { value: "spam", label: "Spam or advertising" },
  { value: "abuse", label: "Harassment or abuse" },
  { value: "misleading", label: "Misleading or cheating" },
  { value: "copyright", label: "Copyright violation" },
  { value: "privacy", label: "Shares private information" },
  { value: "other", label: "Something else" },
] as const;

export type ReportReason = (typeof REPORT_REASONS)[number]["value"];
