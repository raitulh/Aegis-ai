/** Domain types mirroring the Aegis API responses. */

export type Session = {
  user: { id: string; email: string; full_name: string | null; is_guest: boolean; auth_provider: string };
  organization: { id: string; name: string; slug: string; plan: string; is_demo: boolean; is_sandbox: boolean; expires_at: string | null };
  role: string;
  permissions: string[];
  is_guest: boolean;
  expires_at?: string | null;
};

export type Overview = {
  active_systems: number;
  audits_today: number;
  open_findings: number;
  critical_risks: number;
  policy_coverage: number;
  posture: string;
  dimensions: Record<string, number>;
  previous_dimensions: Record<string, number>;
  risk_trend: { date: string; scores: Record<string, number>; posture: string; test_volume: number }[];
  findings_by_severity: Record<string, number>;
  recent_audits: { id: string; name: string; status: string; system_id: string; findings: number; created_at: string }[];
  recent_incidents: { id: string; number: number; title: string; risk_level: string; created_at: string }[];
  test_volume: number;
  is_demo: boolean;
};

export type SystemSummary = {
  id: string;
  name: string;
  slug: string;
  system_type: string;
  environment: string;
  risk_tier: string;
  model_name: string | null;
  is_demo: boolean;
  version: string;
};

export type System = SystemSummary & {
  runtime_mode?: "observe" | "audit" | "enforce";
  baseline_audit_id?: string | null;
  endpoint_url?: string | null;
  description: string | null;
  owner_name: string | null;
  business_purpose: string | null;
  provider_id: string | null;
  model_version: string | null;
  data_classification: string;
  config: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Provider = {
  id: string;
  kind: string;
  name: string;
  base_url: string | null;
  default_model: string | null;
  allow_data_processing: boolean;
  status: string;
  last_checked_at: string | null;
  last_error: string | null;
  has_credentials: boolean;
};

export type Audit = {
  id: string;
  system_id: string;
  name: string;
  kind: string;
  status: string;
  intensity: string;
  categories: string[];
  policy_version_ids: string[];
  progress: number;
  stage: string | null;
  test_count: number;
  tests_completed: number;
  findings_count: number;
  evidence_count: number;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  missing_categories: { category: string; reason: string }[];
  summary: {
    dimensions?: Record<string, number>;
    posture?: string;
    test_matrix?: TestMatrixRow[];
    findings?: { total: number; new?: number; by_severity: Record<string, number>; by_dimension: Record<string, number> };
    tests?: { total: number; failed: number; errors: number };
    high_risk?: number;
    regression?: RegressionReport;
  };
  config?: Record<string, unknown>;
  evidence_head_hash?: string | null;
  cost: Record<string, unknown>;
  is_demo: boolean;
  created_at: string;
};

export type TestMatrixRow = {
  category: string;
  test_type: string;
  samples: number;
  failures: number;
  errors: number;
  status: string;
  severity: string;
  confidence: number | null;
};

export type Finding = {
  id: string;
  number: number;
  title: string;
  category: string;
  dimension: string;
  severity: string;
  status: string;
  description: string;
  system_id: string;
  system_version: string | null;
  model_version: string | null;
  audit_id: string | null;
  control_ref: string | null;
  policy_id: string | null;
  test_type: string | null;
  evaluator_key: string | null;
  evaluator_version: string | null;
  confidence: number;
  impact: string | null;
  risk_level: string;
  risk_score: number;
  risk_reasons: string[];
  risk_factors: { key: string; label: string; value: number; weight: number; contribution: number; detail: string }[];
  occurrences: number;
  sample_size: number;
  details: Record<string, unknown>;
  evidence_unavailable_reason: string | null;
  assignee_id: string | null;
  due_date: string | null;
  last_audit_id: string | null;
  last_seen_at: string | null;
  source: string;
  tags: string[];
  priority: string | null;
  sla_due_at: string | null;
  risk_acceptance: { reason: string; expires_at: string; approved_by: string; approved_at: string; owner_id: string } | null;
  risk_accepted_until: string | null;
  created_at: string;
  updated_at: string;
};

export type FindingSummary = Pick<
  Finding,
  | "id"
  | "number"
  | "title"
  | "category"
  | "dimension"
  | "severity"
  | "status"
  | "risk_level"
  | "risk_score"
  | "system_id"
  | "control_ref"
  | "confidence"
  | "occurrences"
  | "sample_size"
  | "created_at"
  | "source"
  | "tags"
  | "priority"
  | "sla_due_at"
  | "audit_id"
  | "assignee_id"
  | "last_seen_at"
>;

export type AuditFinding = FindingSummary & {
  observed_severity: string;
  observed_risk_level: string | null;
  observed_occurrences: number;
  observed_sample_size: number;
  first_detected_here: boolean;
};

export type FindingComment = { id: string; finding_id: string; author_label: string | null; body: string; created_at: string };

export type FindingEvent = {
  id: string;
  type: string;
  actor_label: string | null;
  from_status: string | null;
  to_status: string | null;
  note: string | null;
  created_at: string;
};

export type Explanation = {
  generated_by: string;
  disclaimer: string;
  what_happened: string;
  why_it_matters: string;
  risk: { level: string; score: number; reasons: string[] };
  affected: { system: string | null; environment: string | null; system_version: string | null; model: string | null; control: string | null };
  evidence: { id: string; kind: string; title: string; content_hash: string; confidence: string }[];
  evidence_unavailable_reason: string | null;
  remediation: { category: string; title: string; description: string };
  next_steps: string[];
};

export type Evidence = {
  id: string;
  seq: number;
  kind: string;
  title: string;
  content: Record<string, unknown>;
  sensitive: boolean;
  content_hash: string;
  chain_hash: string;
  confidence_level: string;
  confidence_reasons: string[];
  created_at: string;
};

export type Control = {
  id: string;
  control_id: string;
  name: string;
  description: string | null;
  domain: string;
  test_type: string;
  threshold: Record<string, unknown>;
  severity: string;
  automation: string;
  required_evidence: string[];
  status: string;
  confidence: number;
  needs_human_review: boolean;
  source: string;
  requirement_id?: string | null;
  provenance?: {
    requirement_key: string;
    text: string;
    modality: string;
    page_number: number | null;
    section: string | null;
    source_excerpt: string;
    source_hash: string;
    confidence: number;
  } | null;
};

export type Requirement = {
  id: string;
  requirement_key: string;
  text: string;
  modality: string;
  page_number: number | null;
  section: string | null;
  source_excerpt: string;
  confidence: number;
  needs_human_review: boolean;
};

export type Policy = {
  id: string;
  name: string;
  key: string;
  description: string | null;
  category: string | null;
  status: string;
  owner_name: string | null;
  current_version_id: string | null;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
};

export type Framework = {
  id: string;
  key: string;
  name: string;
  version: string;
  version_date: string | null;
  publisher: string | null;
  description: string | null;
  source_url: string | null;
  kind: string;
  disclaimer: string | null;
  controls: { id: string; ref: string; title: string; description: string | null; group: string | null; domains: string[]; test_types: string[] }[];
};

export type AgentTrace = {
  id: string;
  trace_id: string;
  system_id: string;
  name: string | null;
  status: string;
  model: string | null;
  source: string;
  started_at: string;
  ended_at: string | null;
  span_count: number;
  tool_call_count: number;
  violation_count: number;
  risk_level: string | null;
  input_summary: string | null;
  output_summary: string | null;
  finding_ids: string[];
};

export type TraceEvent = {
  id: string;
  span_id: string;
  parent_span_id: string | null;
  seq: number;
  kind: string;
  name: string;
  timestamp: string;
  duration_ms: number | null;
  model: string | null;
  input_preview: string | null;
  output_preview: string | null;
  policy_checks: Record<string, unknown>[];
  finding_ids: string[];
  status: string;
};

export type ToolCall = {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  authorization: string;
  requires_human_approval: boolean;
  human_approved: boolean;
  sensitive_data_detected: boolean;
  sensitive_types: string[];
  allowed: boolean;
  risk_level: string | null;
  control_ref: string | null;
  violations: { code: string; message: string }[];
};

export type AgentTraceDetail = AgentTrace & { events: TraceEvent[]; tool_calls: ToolCall[] };

export type RedTeamRun = {
  id: string;
  system_id: string;
  audit_id: string | null;
  name: string;
  status: string;
  config: Record<string, unknown>;
  summary: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  created_at: string;
};

export type RedTeamProbe = {
  id: string;
  parent_id: string | null;
  root_id: string | null;
  depth: number;
  probe_key: string;
  category: string;
  technique: string;
  payload: string;
  expected_behavior: string;
  observed_behavior: string | null;
  result: string;
  severity: string;
  confidence: number;
  finding_id: string | null;
};

export type MonitoringOverview = {
  window_days: number;
  requests: number;
  evaluated: number;
  policy_violations: number;
  hallucination_alerts: number;
  safety_alerts: number;
  pii_incidents: number;
  risk_trend: { date: string; scores: Record<string, number>; test_volume: number }[];
  model_events: { date: string; type: string; title: string }[];
  recent_alerts: Alert[];
};

export type Alert = {
  id: string;
  system_id: string | null;
  severity: string;
  title: string;
  event_type: string;
  control_ref: string | null;
  metric: string | null;
  observed: number | null;
  threshold: number | null;
  evidence_count: number;
  action: string | null;
  status: string;
  triggered_at: string;
};

export type Report = {
  id: string;
  audit_id: string;
  title: string;
  status: string;
  content: Record<string, any>;
  content_hash: string;
  created_at: string;
};

export type Notification = {
  id: string;
  type: string;
  title: string;
  body: string | null;
  link: string | null;
  severity: string | null;
  read_at: string | null;
  created_at: string;
};

export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  role: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
};

export type Member = {
  membership_id: string;
  user_id: string;
  name: string | null;
  email: string;
  role: string;
  status: string;
  last_active_at: string | null;
};

export type Integration = {
  id: string;
  kind: string;
  name: string;
  status: string;
  config: Record<string, unknown>;
  last_checked_at: string | null;
  last_error: string | null;
  connected_at: string | null;
};

export type SearchResponse = {
  query: string;
  groups: Record<string, { type: string; id: string; title: string; subtitle: string | null; url: string; score: number }[]>;
  total: number;
};

export type ChainVerification = {
  audit_id?: string;
  status: "VERIFIED" | "TAMPERED" | "EMPTY" | "PENDING" | "INCOMPLETE" | "UNSIGNED";
  records: number;
  content_checked: number;
  purged: number;
  head: string | null;
  recorded_head?: string | null;
  problems: { index?: number; check: string; detail: string }[];
  checked_at?: string;
};

export type IntegritySummary = {
  status: string;
  audits_checked: number;
  audits_verified: number;
  evidence_total: number;
  results: { audit_id: string; audit_name: string; status: string; records: number; head: string | null; completed_at: string | null }[];
  signing_key_id: string;
  signing_key_development: boolean;
  checked_at: string;
};

export type PackageVerification = {
  format: string;
  status: string;
  checks: { check: string; [k: string]: unknown }[];
  problems: { check: string; detail: string }[];
  audit?: { id: string; name: string; system_name: string | null } | null;
  generated_at?: string;
  root_hash?: string;
  chain?: ChainVerification;
  signature?: { algorithm: string; key_id: string; trusted_key_supplied: boolean; valid: boolean | null };
};

export type EvidenceExport = {
  id: string;
  audit_id: string | null;
  scope: string;
  root_hash: string;
  artifact_count: number;
  integrity_status: string;
  key_id: string | null;
  created_at: string;
  counts: Record<string, number>;
};

export type RuntimeDecision = "allow" | "flag" | "require_approval" | "block";

export type RuntimeEvent = {
  id: string;
  event_id: string;
  system_id: string;
  event_type: string;
  source: string;
  environment: string | null;
  agent_name: string | null;
  actor: string | null;
  session_id: string | null;
  trace_id: string | null;
  tool_name: string | null;
  occurred_at: string;
  payload: Record<string, unknown>;
  signals: Record<string, unknown>;
  mode: string;
  decision: RuntimeDecision;
  effective_decision: RuntimeDecision;
  decision_reason: string | null;
  policy_matches: { policy_key: string; rule_id: string; action: string; severity: string; message: string; version: number | string }[];
  risk_level: string | null;
  finding_id: string | null;
  approval_id: string | null;
  evidence_id: string | null;
};

export type RuntimeOverview = {
  window_hours: number;
  events: number;
  decisions: Record<RuntimeDecision, number>;
  event_types: Record<string, number>;
  timeline: { t: string; events: number; violations: number }[];
  agents: { agent: string; events: number; violations: number }[];
  pending_approvals: number;
  systems: { id: string; name: string; mode: string }[];
};

export type Approval = {
  id: string;
  system_id: string;
  runtime_event_id: string;
  status: "pending" | "approved" | "denied" | "expired";
  summary: string;
  rule_ref: string | null;
  request: Record<string, unknown>;
  expires_at: string;
  decided_by_label: string | null;
  decided_at: string | null;
  decision_note: string | null;
  created_at: string;
};

export type RuntimePolicy = {
  id: string;
  key: string;
  name: string;
  description: string | null;
  category: string | null;
  status: "draft" | "published" | "disabled";
  published_version_id: string | null;
  latest_version: number;
  template_key: string | null;
  created_at: string;
  updated_at: string;
};

export type RuntimePolicyVersion = {
  id: string;
  policy_id: string;
  version: number;
  source_yaml: string;
  compiled: { name: string; rules: { id: string; action: string; severity: string; message?: string; description?: string }[] };
  checksum: string;
  status: "draft" | "published" | "superseded";
  change_note: string | null;
  published_at: string | null;
  created_at: string;
};

export type PolicyAssignment = { id: string; policy_id: string; scope_type: string; scope_key: string; system_id: string | null; enabled: boolean; created_at: string };

export type PolicyTemplate = { key: string; name: string; category: string; summary: string; source_yaml: string };

export type Simulation = {
  window_days: number;
  events_available: number;
  events_evaluated: number;
  truncated: boolean;
  allowed: number;
  flagged: number;
  require_approval: number;
  blocked: number;
  by_rule: Record<string, number>;
  by_system: Record<string, { events: number; flag: number; require_approval: number; block: number }>;
  workflows_requiring_approval: number;
  workflows_blocked: number;
  decisions_changed_vs_recorded: number;
  samples: { event_id: string; event_type: string; system_id: string; agent: string | null; tool: string | null; occurred_at: string; decision: string; rules: string[] }[];
};

export type Schedule = {
  id: string;
  system_id: string;
  name: string;
  enabled: boolean;
  interval_hours: number | null;
  trigger_on: string[];
  categories: string[];
  intensity: string;
  policy_version_ids: string[];
  next_run_at: string | null;
  last_run_at: string | null;
  last_audit_id: string | null;
  created_at: string;
};

export type Trigger = {
  id: string;
  system_id: string;
  schedule_id: string | null;
  event_type: string;
  ref: string;
  source: string;
  categories: string[];
  selection_reason: string | null;
  audit_id: string | null;
  created_at: string;
};

export type RegressionReport = {
  audit_id: string;
  regression: boolean;
  comparisons: Record<
    string,
    {
      audit_id: string;
      new_findings: number;
      resolved_findings: number;
      severity_regressions: number;
      severe_new_findings: { id: string; number: number; title: string; severity: string }[];
      score_drops: Record<string, number>;
      regression: boolean;
    }
  >;
};

export type Quota = {
  metric: string;
  label: string;
  used: number;
  limit: number | null;
  remaining: number | null;
  percent: number | null;
  periodic: boolean;
  projected: number | null;
  projected_over_limit: boolean;
  threshold_reached: number | null;
};

export type PlanPublic = {
  key: string;
  name: string;
  audience: string;
  limits: Record<string, number | null>;
  features: Record<string, { included: boolean; roadmap: boolean; label: string }>;
  retention_days: number | null;
  support: string;
  price_display: string | null;
  self_serve: boolean;
};

export type Usage = {
  organization_id: string;
  plan: PlanPublic;
  plan_key: string;
  sandbox: boolean;
  subscription: { status: string; provider: string; cancel_at_period_end: boolean };
  period: { start: string; end: string };
  quotas: Quota[];
  billing_provider: string;
  self_serve_checkout: boolean;
};

export type PlanCatalogue = { plans: PlanPublic[]; quotas: Record<string, string>; features: Record<string, string>; roadmap_features: string[] };

export type Webhook = { id: string; url: string; description: string | null; events: string[]; active: boolean; failure_count: number; last_delivery_at: string | null; created_at: string };

export type WebhookDelivery = {
  id: string;
  event_type: string;
  event_id: string;
  status: string;
  attempts: number;
  next_attempt_at: string | null;
  response_status: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
};

export type GraphNode = {
  id: string;
  type: "system" | "agent" | "model" | "tool" | "runtime_policy" | "policy" | "control" | "test" | "finding" | "evidence" | "remediation" | "retest";
  label: string;
  href?: string | null;
  severity?: string;
  status?: string;
  risk_level?: string;
  [k: string]: unknown;
};

export type GraphEdge = { source: string; target: string; relation: string; [k: string]: unknown };

export type AssuranceGraph = { nodes: GraphNode[]; edges: GraphEdge[]; counts: Record<string, number>; generated_at: string };

export type AuditLogEntry = { id: string; action: string; resource_type: string; resource_id: string | null; actor: string | null; created_at: string; request_id: string | null };

export type RoleInfo = { role: string; rank: number; assignable: boolean; description: string; permissions: string[] };

export type OrganizationInfo = {
  id: string;
  name: string;
  slug: string;
  plan: string;
  is_demo: boolean;
  is_sandbox: boolean;
  expires_at: string | null;
  retention: { runtime_events_days: number; custom_allowed: boolean; evidence: string };
  finding_sla_days: Record<string, number>;
  features: Record<string, boolean>;
};

export type JobInfo = { id: string; job: string; status: string; attempts: number; max_attempts: number; error_class: string | null; error: string | null; duration_ms: number | null; created_at: string; finished_at: string | null };
