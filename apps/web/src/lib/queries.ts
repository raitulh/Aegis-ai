"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Page } from "./api";
import type * as T from "./types";

export const keys = {
  session: ["session"] as const,
  overview: ["overview"] as const,
  systems: (p?: Record<string, unknown>) => ["systems", p] as const,
  system: (id: string) => ["system", id] as const,
  providers: ["providers"] as const,
  audits: (p?: Record<string, unknown>) => ["audits", p] as const,
  audit: (id: string) => ["audit", id] as const,
  auditEvidence: (id: string) => ["audit", id, "evidence"] as const,
  auditMatrix: (id: string) => ["audit", id, "matrix"] as const,
  auditReport: (id: string) => ["audit", id, "report"] as const,
  findings: (p?: Record<string, unknown>) => ["findings", p] as const,
  finding: (id: string) => ["finding", id] as const,
  findingEvidence: (id: string) => ["finding", id, "evidence"] as const,
  policies: ["policies"] as const,
  policy: (id: string) => ["policy", id] as const,
  policyControls: (id: string) => ["policy", id, "controls"] as const,
  controls: ["controls"] as const,
  frameworks: ["frameworks"] as const,
  traces: (sid: string) => ["traces", sid] as const,
  trace: (id: string) => ["trace", id] as const,
  monitoring: (p?: Record<string, unknown>) => ["monitoring", p] as const,
  alerts: ["alerts"] as const,
  notifications: ["notifications"] as const,
  apiKeys: ["apiKeys"] as const,
  team: ["team"] as const,
  integrations: ["integrations"] as const,
  redteam: ["redteam"] as const,
};

export const useSession = () => useQuery({ queryKey: keys.session, queryFn: () => api.get<T.Session>("/auth/session"), retry: false, staleTime: 60_000 });
export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: () => api.get<T.Overview>("/overview") });
export const useSystems = (params?: Record<string, unknown>) => useQuery({ queryKey: keys.systems(params), queryFn: () => api.get<Page<T.SystemSummary>>("/systems", params) });
export const useSystem = (id: string) => useQuery({ queryKey: keys.system(id), queryFn: () => api.get<T.System>(`/systems/${id}`), enabled: !!id });
export const useProviders = () => useQuery({ queryKey: keys.providers, queryFn: () => api.get<T.Provider[]>("/providers") });
export const useAudits = (params?: Record<string, unknown>) => useQuery({ queryKey: keys.audits(params), queryFn: () => api.get<Page<T.Audit>>("/audits", params) });
export const useAudit = (id: string, poll = false) =>
  useQuery({ queryKey: keys.audit(id), queryFn: () => api.get<T.Audit>(`/audits/${id}`), enabled: !!id, refetchInterval: poll ? 1500 : false });
export const useAuditEvidence = (id: string) => useQuery({ queryKey: keys.auditEvidence(id), queryFn: () => api.get<T.Evidence[]>(`/audits/${id}/evidence`), enabled: !!id });
export const useAuditMatrix = (id: string) => useQuery({ queryKey: keys.auditMatrix(id), queryFn: () => api.get<T.TestMatrixRow[]>(`/audits/${id}/matrix`), enabled: !!id });
export const useAuditReport = (id: string) => useQuery({ queryKey: keys.auditReport(id), queryFn: () => api.get<T.Report>(`/audits/${id}/report`), enabled: !!id, retry: false });
export const useFindings = (params?: Record<string, unknown>) => useQuery({ queryKey: keys.findings(params), queryFn: () => api.get<Page<T.FindingSummary>>("/findings", params) });
export const useFinding = (id: string) => useQuery({ queryKey: keys.finding(id), queryFn: () => api.get<T.Finding>(`/findings/${id}`), enabled: !!id });
export const useFindingEvidence = (id: string) => useQuery({ queryKey: keys.findingEvidence(id), queryFn: () => api.get<T.Evidence[]>(`/findings/${id}/evidence`), enabled: !!id });
export const usePolicies = () => useQuery({ queryKey: keys.policies, queryFn: () => api.get<Page<T.Policy>>("/policies") });
export const usePolicy = (id: string) => useQuery({ queryKey: keys.policy(id), queryFn: () => api.get<T.Policy>(`/policies/${id}`), enabled: !!id });
export const usePolicyControls = (id: string) => useQuery({ queryKey: keys.policyControls(id), queryFn: () => api.get<T.Control[]>(`/policies/${id}/controls`), enabled: !!id });
export const useControls = () => useQuery({ queryKey: keys.controls, queryFn: () => api.get<Page<T.Control>>("/controls", { page_size: 100 }) });
export const useFrameworks = () => useQuery({ queryKey: keys.frameworks, queryFn: () => api.get<T.Framework[]>("/frameworks") });
export const useTraces = (sid: string) => useQuery({ queryKey: keys.traces(sid), queryFn: () => api.get<Page<T.AgentTrace>>(`/agents/${sid}/traces`, { page_size: 50 }), enabled: !!sid });
export const useTrace = (id: string) => useQuery({ queryKey: keys.trace(id), queryFn: () => api.get<T.AgentTraceDetail>(`/traces/${id}`), enabled: !!id });
export const useMonitoring = (params?: Record<string, unknown>) => useQuery({ queryKey: keys.monitoring(params), queryFn: () => api.get<T.MonitoringOverview>("/monitoring/overview", params) });
export const useAlerts = () => useQuery({ queryKey: keys.alerts, queryFn: () => api.get<Page<T.Alert>>("/alerts", { page_size: 50 }) });
export const useNotifications = () => useQuery({ queryKey: keys.notifications, queryFn: () => api.get<Page<T.Notification>>("/notifications", { page_size: 30 }), refetchInterval: 30_000 });
export const useApiKeys = () => useQuery({ queryKey: keys.apiKeys, queryFn: () => api.get<T.ApiKey[]>("/api-keys") });
export const useTeam = () => useQuery({ queryKey: keys.team, queryFn: () => api.get<T.Member[]>("/team") });
export const useIntegrations = () => useQuery({ queryKey: keys.integrations, queryFn: () => api.get<T.Integration[]>("/integrations") });
export const useRedTeamRuns = () => useQuery({ queryKey: keys.redteam, queryFn: () => api.get<Page<T.RedTeamRun>>("/redteam/runs") });

export function useInvalidate() {
  const qc = useQueryClient();
  return (...k: unknown[]) => qc.invalidateQueries({ queryKey: k });
}

export function useUpdateFinding(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<T.Finding>(`/findings/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.finding(id) });
      qc.invalidateQueries({ queryKey: ["findings"] });
    },
  });
}
