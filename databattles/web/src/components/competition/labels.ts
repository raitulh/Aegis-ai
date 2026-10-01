/**
 * Human-readable labels for competition enums and viewer blockers (stable backend codes).
 */
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import type { CompetitionDetail } from "@/lib/types";

export const STATUS_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "upcoming", label: "Upcoming" },
  { value: "registration_closed", label: "Registration closed" },
  { value: "completed", label: "Completed" },
  { value: "archived", label: "Archived" },
];

export const TASK_TYPE_OPTIONS = [
  { value: "classification", label: "Classification" },
  { value: "regression", label: "Regression" },
  { value: "nlp", label: "NLP" },
  { value: "computer_vision", label: "Computer vision" },
  { value: "data_analysis", label: "Data analysis" },
  { value: "coding", label: "Coding" },
  { value: "research", label: "Research" },
  { value: "hackathon", label: "Hackathon" },
  { value: "other", label: "Other" },
];

export const EVENT_TYPE_OPTIONS = [
  { value: "ml_competition", label: "ML competition" },
  { value: "datathon", label: "Datathon" },
  { value: "hackathon", label: "Hackathon" },
  { value: "coding_contest", label: "Coding contest" },
  { value: "research_challenge", label: "Research challenge" },
  { value: "workshop", label: "Workshop" },
  { value: "demo_day", label: "Demo day" },
  { value: "practice", label: "Practice" },
];

export const DIFFICULTY_OPTIONS = [
  { value: "beginner", label: "Beginner" },
  { value: "intermediate", label: "Intermediate" },
  { value: "advanced", label: "Advanced" },
];

export const SORT_OPTIONS = [
  { value: "relevance", label: "Relevance" },
  { value: "end", label: "Ending soonest" },
  { value: "start", label: "Starting soonest" },
  { value: "popular", label: "Most participants" },
  { value: "updated", label: "Recently updated" },
];

const LABELS: Record<string, string> = Object.fromEntries(
  [...TASK_TYPE_OPTIONS, ...EVENT_TYPE_OPTIONS, ...DIFFICULTY_OPTIONS].map((o) => [o.value, o.label]),
);

export function enumLabel(value: string | null | undefined): string {
  if (!value) return "";
  return LABELS[value] ?? titleCase(value);
}

export const VISIBILITY_LABELS: Record<string, string> = {
  public: "Public",
  university: "University members only",
  invite_only: "Invite only",
  private: "Private",
};

export const FORMAT_LABELS: Record<string, string> = { online: "Online", offline: "In person", hybrid: "Hybrid" };

export const LEADERBOARD_VISIBILITY_LABELS: Record<string, string> = {
  visible: "Public scores and ranks",
  ranks_only: "Ranks only (scores hidden)",
  hidden: "Hidden until results are final",
};

export function directionLabel(direction: string | null | undefined): string {
  return direction === "minimize" ? "Lower is better" : "Higher is better";
}

export const FILE_KIND_LABELS: Record<string, string> = {
  data: "Data",
  sample_submission: "Sample submission",
  notebook: "Notebook",
  documentation: "Documentation",
};

/** Why the viewer cannot join (codes from competitions.service.join_blockers_for). */
export function describeJoinBlocker(code: string, comp: Pick<CompetitionDetail, "registration_opens_at" | "registration_closes_at" | "frozen" | "host" | "ends_at">): string {
  switch (code) {
    case "sign_in_required":
      return "Sign in to join this competition.";
    case "email_not_verified":
      return "Verify your email address before joining — use the link we emailed you.";
    case "registration_closed": {
      if (comp.frozen) return "Registration is paused while the competition is under review.";
      const now = Date.now();
      if (comp.registration_opens_at && new Date(comp.registration_opens_at).getTime() > now) {
        return `Registration opens ${formatDateTime(comp.registration_opens_at)} (${relativeTime(comp.registration_opens_at)}).`;
      }
      if (comp.registration_closes_at && new Date(comp.registration_closes_at).getTime() <= now) {
        return `Registration closed ${relativeTime(comp.registration_closes_at)}.`;
      }
      return "Registration is not open.";
    }
    case "staff_cannot_participate":
      return "Organizers and judges can't participate in a competition they run.";
    case "verified_membership_required":
      return `Only verified members of ${comp.host?.name ?? "the host organization"} can join. Verify your membership with an institutional email first.`;
    default:
      return titleCase(code);
  }
}

/** Why the viewer cannot submit (codes from competitions.service.submission_blockers). */
export function describeSubmitBlocker(code: string, comp: Pick<CompetitionDetail, "team_min_size" | "starts_at" | "ends_at" | "frozen">): string {
  switch (code) {
    case "not_automatically_scored":
      return "This event is judged by people, so prediction files aren't scored automatically.";
    case "submissions_closed": {
      if (comp.frozen) return "Submissions are paused while the competition is under review.";
      if (comp.starts_at && new Date(comp.starts_at).getTime() > Date.now()) {
        return `Submissions open ${formatDateTime(comp.starts_at)} (${relativeTime(comp.starts_at)}).`;
      }
      return "The submission window is closed.";
    }
    case "team_required":
      return "Create or join a team before submitting.";
    case "team_too_small":
      return `Teams need at least ${comp.team_min_size} members before they can submit.`;
    case "email_not_verified":
      return "Verify your email address before submitting.";
    default:
      return titleCase(code);
  }
}
