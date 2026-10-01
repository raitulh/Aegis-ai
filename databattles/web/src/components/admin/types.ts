/** Hand-written types for platform admin / moderation endpoints (backend/app/modules/admin/router.py, moderation/). */
import type { OrgMini, UserMini } from "@/lib/types";

export interface AdminHealth {
  database: "ok" | "error";
  api: {
    uptime_seconds: number;
    requests: number;
    server_errors: number;
    error_rate: number;
    latency_ms_p50: number | null;
    latency_ms_p95: number | null;
    latency_ms_p99: number | null;
    status_counts: Record<string, number>;
    counters: Record<string, number>;
  };
  jobs: { by_status: Record<string, number>; oldest_pending_age_seconds: number; dead_last_24h: number };
  worker_last_job_finished_at: string | null;
  submissions: { stuck_over_30m: number; failed_24h: number };
  errors_24h: number;
  emails_failed_24h: number;
  open_reports: number;
  suspicious_auth_events_24h: number;
  rate_limit_incidents: { bucket: string; count: number; last_at: string }[];
  config: Record<string, string | boolean>;
}

export interface AdminStats {
  users: { total: number; new_7d: number; demo: number };
  competitions: { total: number; published: number };
  submissions: { total: number; last_24h: number };
  organizations: { total: number; verified: number };
  signups_by_day: { day: string; count: number }[];
  submissions_by_day: { day: string; count: number }[];
}

export interface AdminUserRow {
  user: UserMini;
  email: string | null;
  status: "active" | "suspended" | "banned" | "deleted";
  status_reason: string | null;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
  roles: string[];
  is_demo: boolean;
}

export interface AuditEntry {
  id: string;
  created_at: string;
  action: string;
  actor: UserMini | null;
  target_type: string | null;
  target_id: string | null;
  reason: string | null;
  meta: Record<string, unknown>;
}

export interface JobRow {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "dead";
  attempts: number;
  max_attempts: number;
  run_after: string | null;
  last_error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ErrorRow {
  id: string;
  created_at: string;
  request_id: string | null;
  method: string | null;
  path: string | null;
  error_type: string;
  message: string;
  source: "api" | "worker" | string;
}

export interface FlagRow {
  key: string;
  enabled: boolean;
  description: string | null;
  is_public: boolean;
  updated_at: string | null;
}

export interface VerificationQueueRow {
  org: OrgMini;
  website_url: string | null;
  email_domains: string[];
  requested_note: string | null;
  created_at: string;
}

export interface RevenueView {
  plans: { key: string; name: string; price_cents_monthly: number; active_subscriptions: number; mrr_cents: number }[];
  mrr_cents: number;
  payment_failures: number;
  provider: string;
  note: string;
}

export interface EmailRow {
  id: string;
  to: string;
  template: string;
  template_version: number | null;
  subject: string | null;
  status: "queued" | "sent" | "failed";
  attempts: number;
  error: string | null;
  created_at: string;
  sent_at: string | null;
}

export interface BadgeDef {
  id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  color: string;
  is_manual: boolean;
  rarity_label: string | null;
  awarded_count: number;
}

export type ReportTargetType = "thread" | "comment" | "project" | "dataset" | "user" | "competition";

export interface QueueItem {
  target_type: ReportTargetType;
  target_id: string;
  report_count: number;
  first_reported_at: string;
  reasons: string[];
  details: string[];
  preview: { title: string; excerpt: string | null; url: string | null; hidden?: boolean };
  owner: UserMini | null;
  owner_status: string | null;
}
