export const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"] as const;
export type Severity = (typeof SEVERITY_ORDER)[number];

export const SEVERITY_META: Record<string, { label: string; color: string; icon: string }> = {
  critical: { label: "Critical", color: "var(--color-critical)", icon: "octagon" },
  high: { label: "High", color: "var(--color-high)", icon: "alert-triangle" },
  medium: { label: "Medium", color: "var(--color-medium)", icon: "alert-circle" },
  low: { label: "Low", color: "var(--color-low)", icon: "info" },
  info: { label: "Informational", color: "var(--color-info)", icon: "circle" },
};

export const RISK_META: Record<string, { label: string; color: string }> = {
  critical: { label: "Critical", color: "var(--color-critical)" },
  high: { label: "High", color: "var(--color-high)" },
  medium: { label: "Medium", color: "var(--color-medium)" },
  low: { label: "Low", color: "var(--color-low)" },
  informational: { label: "Informational", color: "var(--color-info)" },
};

export const DIMENSIONS = ["fairness", "truthfulness", "safety", "privacy", "security", "governance"] as const;

export const STATUS_META: Record<string, { label: string; tone: string }> = {
  open: { label: "Open", tone: "high" },
  acknowledged: { label: "Triaged", tone: "medium" },
  triaged: { label: "Triaged", tone: "medium" },
  in_remediation: { label: "In remediation", tone: "low" },
  fixed: { label: "Fixed", tone: "low" },
  retesting: { label: "Retesting", tone: "low" },
  resolved: { label: "Resolved", tone: "success" },
  accepted_risk: { label: "Accepted risk", tone: "info" },
  false_positive: { label: "False positive", tone: "info" },
  completed: { label: "Completed", tone: "success" },
  partially_completed: { label: "Partial", tone: "medium" },
  running: { label: "Running", tone: "low" },
  queued: { label: "Queued", tone: "info" },
  failed: { label: "Failed", tone: "critical" },
  draft: { label: "Draft", tone: "info" },
  cancelled: { label: "Cancelled", tone: "info" },
  pass: { label: "Pass", tone: "success" },
  fail: { label: "Fail", tone: "critical" },
  partial: { label: "Partial", tone: "medium" },
  error: { label: "Error", tone: "critical" },
  // membership
  active: { label: "Active", tone: "success" },
  invited: { label: "Invited", tone: "info" },
  suspended: { label: "Suspended", tone: "critical" },
  // runtime decisions & approvals
  allow: { label: "Allowed", tone: "success" },
  flag: { label: "Flagged", tone: "medium" },
  require_approval: { label: "Needs approval", tone: "high" },
  block: { label: "Blocked", tone: "critical" },
  pending: { label: "Pending", tone: "medium" },
  approved: { label: "Approved", tone: "success" },
  denied: { label: "Denied", tone: "critical" },
  expired: { label: "Expired", tone: "info" },
  // policies
  published: { label: "Published", tone: "success" },
  superseded: { label: "Superseded", tone: "info" },
  disabled: { label: "Disabled", tone: "info" },
  // integrity
  VERIFIED: { label: "Verified", tone: "success" },
  TAMPERED: { label: "Tampered", tone: "critical" },
  INCOMPLETE: { label: "Incomplete", tone: "high" },
  UNSIGNED: { label: "Unsigned", tone: "medium" },
  EMPTY: { label: "No evidence", tone: "info" },
  PENDING: { label: "Pending", tone: "info" },
  // deliveries & jobs
  succeeded: { label: "Succeeded", tone: "success" },
  retrying: { label: "Retrying", tone: "medium" },
  dead: { label: "Dead-lettered", tone: "critical" },
};

export const RUNTIME_MODE_META: Record<string, { label: string; description: string; tone: string }> = {
  observe: { label: "Observe", description: "Record telemetry and decisions; never interfere.", tone: "info" },
  audit: { label: "Audit", description: "Record, and turn policy violations into findings with evidence.", tone: "medium" },
  enforce: { label: "Enforce", description: "Block or hold actions for approval before they happen.", tone: "high" },
};
