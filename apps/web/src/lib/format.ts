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
  acknowledged: { label: "Acknowledged", tone: "medium" },
  in_remediation: { label: "In remediation", tone: "low" },
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
};
