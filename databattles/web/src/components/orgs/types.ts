/**
 * Hand-written types for organization endpoints that return ad-hoc dicts
 * (see backend/app/modules/orgs/service.py: detail(), admin_dashboard(), sponsor_dashboard(), subscription_view()).
 */
import type { OrgCard, OrgMini, UserMini } from "@/lib/types";

export type OrgType = "university" | "club" | "community" | "sponsor" | "company";
export type OrgRole = "owner" | "admin" | "manager" | "member";
export type MembershipStatus = "active" | "pending" | "rejected" | "removed";

export interface OrgViewer {
  membership_status: MembershipStatus | null;
  role: OrgRole | null;
  verified: boolean;
  can_manage: boolean;
  can_manage_content: boolean;
}

export interface OrgDepartmentMini {
  id: string;
  slug: string;
  name: string;
}

export interface OrgDetail extends OrgCard {
  description_html: string;
  description_md: string | null;
  website_url: string | null;
  allow_membership_requests: boolean;
  domain_verification_available: boolean;
  email_domains: string[];
  departments: OrgDepartmentMini[];
  competitions: {
    slug: string;
    title: string;
    status: string;
    visibility: string;
    cover_style: string;
    ends_at: string | null;
    participant_count: number;
  }[];
  sponsored_competitions: { slug: string; title: string; tier: string }[];
  courses: { slug: string; title: string; difficulty: string }[];
  projects: { slug: string; title: string; summary: string }[];
  viewer: OrgViewer;
  plan_key: string | null;
}

export interface MyMembership {
  org: OrgMini;
  role: OrgRole;
  status: MembershipStatus;
  verified: boolean;
  verification_method: string;
  joined_at: string;
}

export interface RosterRow {
  id: string;
  user: UserMini;
  role: OrgRole;
  status: MembershipStatus;
  verified: boolean;
  verification_method: string;
  department: string | null;
  department_id: string | null;
  request_note: string | null;
  joined_at: string;
  competitions_joined: number;
  account_status: string;
}

export interface Department {
  id: string;
  slug: string;
  name: string;
  description: string | null;
}

export interface Invite {
  id: string;
  email: string | null;
  role: OrgRole;
  expires_at: string;
  max_uses: number;
  uses: number;
  revoked_at: string | null;
  created_at: string;
}

export interface InviteCreated extends Invite {
  link: string;
}

export interface InvitePreview {
  org: OrgMini;
  role: OrgRole;
  email_bound: boolean;
  expires_at: string;
}

export interface OrgAdminDashboard {
  org: OrgCard;
  members: { active: number; verified: number; pending_requests: number; active_last_30d: number };
  joins_by_month: { month: string; count: number }[];
  hosted_competitions: {
    slug: string;
    title: string;
    status: string;
    visibility: string;
    participants: number;
    member_participants: number;
    teams: number;
    submissions: number;
  }[];
  member_participation: { slug: string; title: string; members: number }[];
  departments: { name: string; members: number }[];
  course_completions: number;
  top_performers: { user: UserMini; top10_finishes: number; best_rank: number | null }[];
  privacy_note: string;
}

export interface SponsorDashboard {
  org: OrgCard;
  sponsored: {
    slug: string;
    title: string;
    tier: string;
    status: string;
    participants: number;
    teams: number;
    submissions: number;
    views: number;
  }[];
  totals: { events: number; participants: number; submissions: number };
  talent: {
    user: UserMini;
    best_rank: number | null;
    sponsored_events: number;
    headline: string | null;
    skills: string[];
  }[];
  talent_note: string;
}

export interface PlanInfo {
  key: string;
  name: string;
  description: string | null;
  price_cents_monthly: number;
  entitlements: Record<string, unknown>;
}

export interface SubscriptionView {
  plan: { key: string; name: string; entitlements: Record<string, unknown> } | null;
  status: string;
  provider: string;
  current_period_end: string | null;
  payment_failed: boolean;
  available_plans: PlanInfo[];
  billing_note: string;
}
