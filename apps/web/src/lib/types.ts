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
    findings?: { total: number; by_severity: Record<string, number>; by_dimension: Record<string, number> };
    high_risk?: number;
  };
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
  created_at: string;
  updated_at: string;
};

export type FindingSummary = Pick<
  Finding,
  "id" | "number" | "title" | "category" | "dimension" | "severity" | "status" | "risk_level" | "risk_score" | "system_id" | "control_ref" | "confidence" | "occurrences" | "sample_size" | "created_at"
>;

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
