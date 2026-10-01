"use client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, path, type Page } from "./api";
import type * as T from "./types";

type Params = Record<string, unknown> | undefined;

export const keys = {
  session: ["session"] as const,
  overview: ["overview"] as const,
  systems: (p?: Params) => ["systems", p] as const,
  system: (id: string) => ["system", id] as const,
  systemAudits: (id: string) => ["system", id, "audits"] as const,
  providers: ["providers"] as const,
  audits: (p?: Params) => ["audits", p] as const,
  audit: (id: string) => ["audit", id] as const,
  auditEvidence: (id: string) => ["audit", id, "evidence"] as const,
  auditFindings: (id: string) => ["audit", id, "findings"] as const,
  auditMatrix: (id: string) => ["audit", id, "matrix"] as const,
  auditReport: (id: string) => ["audit", id, "report"] as const,
  auditVerify: (id: string) => ["audit", id, "verify"] as const,
  auditRegression: (id: string) => ["audit", id, "regression"] as const,
  findings: (p?: Params) => ["findings", p] as const,
  finding: (id: string) => ["finding", id] as const,
  findingEvidence: (id: string) => ["finding", id, "evidence"] as const,
  findingEvents: (id: string) => ["finding", id, "events"] as const,
  findingComments: (id: string) => ["finding", id, "comments"] as const,
  findingTransitions: (id: string) => ["finding", id, "transitions"] as const,
  findingExplanation: (id: string) => ["finding", id, "explanation"] as const,
  policies: ["policies"] as const,
  policy: (id: string) => ["policy", id] as const,
  policyControls: (id: string) => ["policy", id, "controls"] as const,
  runtimePolicies: ["runtime-policies"] as const,
  runtimePolicy: (id: string) => ["runtime-policy", id] as const,
  runtimePolicyVersions: (id: string) => ["runtime-policy", id, "versions"] as const,
  runtimePolicyAssignments: (id: string) => ["runtime-policy", id, "assignments"] as const,
  templates: ["runtime-policy-templates"] as const,
  runtimeOverview: (p?: Params) => ["runtime", "overview", p] as const,
  runtimeEvents: (p?: Params) => ["runtime", "events", p] as const,
  approvals: (p?: Params) => ["runtime", "approvals", p] as const,
  traces: (sid: string) => ["traces", sid] as const,
  trace: (id: string) => ["trace", id] as const,
  monitoring: (p?: Params) => ["monitoring", p] as const,
  notifications: ["notifications"] as const,
  apiKeys: ["apiKeys"] as const,
  team: ["team"] as const,
  roles: ["roles"] as const,
  integrations: ["integrations"] as const,
  webhooks: ["webhooks"] as const,
  redteam: ["redteam"] as const,
  integrity: ["evidence", "integrity"] as const,
  exports: ["evidence", "exports"] as const,
  signingKey: ["evidence", "signing-key"] as const,
  schedules: (p?: Params) => ["assurance", "schedules", p] as const,
  triggers: (p?: Params) => ["assurance", "triggers", p] as const,
  usage: ["usage"] as const,
  plans: ["plans"] as const,
  organization: ["organization"] as const,
  auditLog: (p?: Params) => ["audit-log", p] as const,
  graph: (p?: Params) => ["graph", p] as const,
  jobs: (p?: Params) => ["jobs", p] as const,
};

export const useSession = () => useQuery({ queryKey: keys.session, queryFn: () => api.get<T.Session>("/auth/session"), retry: false, staleTime: 60_000 });
export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: () => api.get<T.Overview>("/overview"), refetchInterval: 60_000 });
export const useSystems = (params?: Params) =>
  useQuery({ queryKey: keys.systems(params), queryFn: () => api.get<Page<T.SystemSummary>>("/systems", params), placeholderData: keepPreviousData });
export const useSystem = (id: string) => useQuery({ queryKey: keys.system(id), queryFn: () => api.get<T.System>(path`/systems/${id}`), enabled: !!id });
export const useSystemAudits = (id: string) => useQuery({ queryKey: keys.systemAudits(id), queryFn: () => api.get<T.Audit[]>(path`/systems/${id}/audits`), enabled: !!id });
export const useProviders = () => useQuery({ queryKey: keys.providers, queryFn: () => api.get<T.Provider[]>("/providers") });
export const useAudits = (params?: Params) =>
  useQuery({ queryKey: keys.audits(params), queryFn: () => api.get<Page<T.Audit>>("/audits", params), placeholderData: keepPreviousData });
export const useAudit = (id: string, poll = false) =>
  useQuery({ queryKey: keys.audit(id), queryFn: () => api.get<T.Audit>(path`/audits/${id}`), enabled: !!id, refetchInterval: poll ? 4000 : false });
export const useAuditEvidence = (id: string) => useQuery({ queryKey: keys.auditEvidence(id), queryFn: () => api.get<T.Evidence[]>(path`/audits/${id}/evidence`), enabled: !!id });
export const useAuditFindings = (id: string) => useQuery({ queryKey: keys.auditFindings(id), queryFn: () => api.get<T.AuditFinding[]>(path`/audits/${id}/findings`), enabled: !!id });
export const useAuditMatrix = (id: string) => useQuery({ queryKey: keys.auditMatrix(id), queryFn: () => api.get<T.TestMatrixRow[]>(path`/audits/${id}/matrix`), enabled: !!id });
export const useAuditReport = (id: string) => useQuery({ queryKey: keys.auditReport(id), queryFn: () => api.get<T.Report>(path`/audits/${id}/report`), enabled: !!id, retry: false });
export const useAuditVerify = (id: string, enabled = true) =>
  useQuery({ queryKey: keys.auditVerify(id), queryFn: () => api.get<T.ChainVerification>(path`/audits/${id}/evidence/verify`), enabled: !!id && enabled });
export const useAuditRegression = (id: string, enabled = true) =>
  useQuery({ queryKey: keys.auditRegression(id), queryFn: () => api.get<T.RegressionReport>(path`/audits/${id}/regression`), enabled: !!id && enabled });
export const useFindings = (params?: Params) =>
  useQuery({ queryKey: keys.findings(params), queryFn: () => api.get<Page<T.FindingSummary>>("/findings", params), placeholderData: keepPreviousData });
export const useFinding = (id: string) => useQuery({ queryKey: keys.finding(id), queryFn: () => api.get<T.Finding>(path`/findings/${id}`), enabled: !!id });
export const useFindingEvidence = (id: string) => useQuery({ queryKey: keys.findingEvidence(id), queryFn: () => api.get<T.Evidence[]>(path`/findings/${id}/evidence`), enabled: !!id });
export const useFindingEvents = (id: string) => useQuery({ queryKey: keys.findingEvents(id), queryFn: () => api.get<T.FindingEvent[]>(path`/findings/${id}/events`), enabled: !!id });
export const useFindingComments = (id: string) => useQuery({ queryKey: keys.findingComments(id), queryFn: () => api.get<T.FindingComment[]>(path`/findings/${id}/comments`), enabled: !!id });
export const useFindingTransitions = (id: string) =>
  useQuery({ queryKey: keys.findingTransitions(id), queryFn: () => api.get<{ status: string; allowed: string[] }>(path`/findings/${id}/transitions`), enabled: !!id });
export const useFindingExplanation = (id: string, enabled: boolean) =>
  useQuery({ queryKey: keys.findingExplanation(id), queryFn: () => api.get<T.Explanation>(path`/findings/${id}/explanation`), enabled: !!id && enabled });
export const usePolicies = () => useQuery({ queryKey: keys.policies, queryFn: () => api.get<Page<T.Policy>>("/policies", { page_size: 100 }) });
export const usePolicy = (id: string) => useQuery({ queryKey: keys.policy(id), queryFn: () => api.get<T.Policy>(path`/policies/${id}`), enabled: !!id });
export const usePolicyControls = (id: string) => useQuery({ queryKey: keys.policyControls(id), queryFn: () => api.get<T.Control[]>(path`/policies/${id}/controls`), enabled: !!id });
export const useRuntimePolicies = () => useQuery({ queryKey: keys.runtimePolicies, queryFn: () => api.get<Page<T.RuntimePolicy>>("/runtime-policies", { page_size: 100 }) });
export const useRuntimePolicy = (id: string) => useQuery({ queryKey: keys.runtimePolicy(id), queryFn: () => api.get<T.RuntimePolicy>(path`/runtime-policies/${id}`), enabled: !!id });
export const useRuntimePolicyVersions = (id: string) =>
  useQuery({ queryKey: keys.runtimePolicyVersions(id), queryFn: () => api.get<T.RuntimePolicyVersion[]>(path`/runtime-policies/${id}/versions`), enabled: !!id });
export const useRuntimePolicyAssignments = (id: string) =>
  useQuery({ queryKey: keys.runtimePolicyAssignments(id), queryFn: () => api.get<T.PolicyAssignment[]>(path`/runtime-policies/${id}/assignments`), enabled: !!id });
export const usePolicyTemplates = () => useQuery({ queryKey: keys.templates, queryFn: () => api.get<T.PolicyTemplate[]>("/runtime-policies/templates"), staleTime: 10 * 60_000 });
export const useRuntimeOverview = (params?: Params, enabled = true) =>
  useQuery({ queryKey: keys.runtimeOverview(params), queryFn: () => api.get<T.RuntimeOverview>("/runtime/overview", params), refetchInterval: 15_000, enabled });
export const useRuntimeEvents = (params?: Params) =>
  useQuery({ queryKey: keys.runtimeEvents(params), queryFn: () => api.get<Page<T.RuntimeEvent>>("/runtime/events", params), refetchInterval: 15_000, placeholderData: keepPreviousData });
export const useApprovals = (params?: Params) =>
  useQuery({ queryKey: keys.approvals(params), queryFn: () => api.get<Page<T.Approval>>("/runtime/approvals", params), refetchInterval: 10_000 });
export const useTraces = (sid: string) => useQuery({ queryKey: keys.traces(sid), queryFn: () => api.get<Page<T.AgentTrace>>(path`/agents/${sid}/traces`, { page_size: 50 }), enabled: !!sid });
export const useTrace = (id: string) => useQuery({ queryKey: keys.trace(id), queryFn: () => api.get<T.AgentTraceDetail>(path`/traces/${id}`), enabled: !!id });
export const useMonitoring = (params?: Params) => useQuery({ queryKey: keys.monitoring(params), queryFn: () => api.get<T.MonitoringOverview>("/monitoring/overview", params) });
export const useNotifications = () =>
  useQuery({ queryKey: keys.notifications, queryFn: () => api.get<Page<T.Notification>>("/notifications", { page_size: 30 }), refetchInterval: 30_000 });
export const useApiKeys = () => useQuery({ queryKey: keys.apiKeys, queryFn: () => api.get<T.ApiKey[]>("/api-keys") });
export const useTeam = () => useQuery({ queryKey: keys.team, queryFn: () => api.get<T.Member[]>("/team") });
export const useRoles = () => useQuery({ queryKey: keys.roles, queryFn: () => api.get<T.RoleInfo[]>("/roles"), staleTime: 10 * 60_000 });
export const useIntegrations = () => useQuery({ queryKey: keys.integrations, queryFn: () => api.get<T.Integration[]>("/integrations") });
export const useWebhooks = () => useQuery({ queryKey: keys.webhooks, queryFn: () => api.get<T.Webhook[]>("/webhooks") });
export const useRedTeamRuns = () => useQuery({ queryKey: keys.redteam, queryFn: () => api.get<Page<T.RedTeamRun>>("/redteam/runs") });
export const useIntegrity = () => useQuery({ queryKey: keys.integrity, queryFn: () => api.get<T.IntegritySummary>("/evidence/integrity"), staleTime: 60_000 });
export const useExports = () => useQuery({ queryKey: keys.exports, queryFn: () => api.get<Page<T.EvidenceExport>>("/evidence/exports") });
export const useSigningKey = () =>
  useQuery({ queryKey: keys.signingKey, queryFn: () => api.get<{ algorithm: string; key_id: string; public_key: string; development_key: boolean }>("/evidence/signing-key") });
export const useSchedules = (params?: Params) => useQuery({ queryKey: keys.schedules(params), queryFn: () => api.get<T.Schedule[]>("/assurance/schedules", params) });
export const useTriggers = (params?: Params) => useQuery({ queryKey: keys.triggers(params), queryFn: () => api.get<Page<T.Trigger>>("/assurance/triggers", params) });
export const useUsage = () => useQuery({ queryKey: keys.usage, queryFn: () => api.get<T.Usage>("/usage"), staleTime: 60_000 });
export const usePlans = () => useQuery({ queryKey: keys.plans, queryFn: () => api.get<T.PlanCatalogue>("/plans"), staleTime: 10 * 60_000 });
export const useOrganization = () => useQuery({ queryKey: keys.organization, queryFn: () => api.get<T.OrganizationInfo>("/organization") });
export const useAuditLog = (params?: Params) =>
  useQuery({ queryKey: keys.auditLog(params), queryFn: () => api.get<Page<T.AuditLogEntry>>("/audit-log", params), placeholderData: keepPreviousData });
export const useGraph = (params?: Params) => useQuery({ queryKey: keys.graph(params), queryFn: () => api.get<T.AssuranceGraph>("/graph", params), placeholderData: keepPreviousData });
export const useJobs = (params?: Params) => useQuery({ queryKey: keys.jobs(params), queryFn: () => api.get<Page<T.JobInfo>>("/jobs", params) });

export function useInvalidate() {
  const qc = useQueryClient();
  return (...k: unknown[]) => qc.invalidateQueries({ queryKey: k });
}

export function useUpdateFinding(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<T.Finding>(path`/findings/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["finding", id] });
      qc.invalidateQueries({ queryKey: ["findings"] });
    },
  });
}

/** Server-enforced permissions mirrored for UX (hiding controls is never the security boundary). */
export function useCan() {
  const { data } = useSession();
  const perms = new Set(data?.permissions ?? []);
  return (permission: string) => perms.has(permission);
}
