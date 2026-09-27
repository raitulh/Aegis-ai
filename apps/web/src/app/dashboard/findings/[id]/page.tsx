"use client";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, GitBranch, Scale, ShieldCheck, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Button, Card, CardBody, CardHeader, CardTitle, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { CounterfactualComparison } from "@/components/dashboard/counterfactual";
import { EvidenceList } from "@/components/dashboard/evidence-list";
import { api, ApiError } from "@/lib/api";
import { useFinding, useFindingEvidence, useInvalidate, useUpdateFinding } from "@/lib/queries";
import {titleCase} from "@/lib/utils";
import type { Finding } from "@/lib/types";

const STATUSES = ["open", "acknowledged", "in_remediation", "resolved", "accepted_risk", "false_positive"];

export default function FindingDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useFinding(id);
  const evidence = useFindingEvidence(id);
  const update = useUpdateFinding(id);
  const invalidate = useInvalidate();
  const [retesting, setRetesting] = useState(false);
  const [retestResult, setRetestResult] = useState<any>(null);

  async function remediate(f: Finding) {
    try {
      const rec = await api.get<any>(`/findings/${f.id}/recommendation`);
      const rem = await api.post<any>(`/findings/${f.id}/remediations`, { category: rec.category, title: rec.title, description: rec.description, change: rec.change });
      await api.post(`/remediations/${rem.id}/apply`);
      toast.success("Remediation applied to the system configuration");
      invalidate("finding", id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create remediation");
    }
  }

  async function retest(f: Finding) {
    setRetesting(true);
    try {
      const run = await api.post<any>(`/findings/${f.id}/retest`);
      for (let i = 0; i < 60; i++) {
        const rr = await api.get<any>(`/regression-runs/${run.id}`);
        if (["completed", "failed"].includes(rr.status)) {
          setRetestResult(rr);
          invalidate("finding", id);
          break;
        }
        await new Promise((r) => setTimeout(r, 800));
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Re-test failed");
    } finally {
      setRetesting(false);
    }
  }

  return (
    <QueryBoundary query={query} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(f) => {
        const cf = (f.details?.counterfactual ?? f.details?.observed) as any;
        const isCounterfactual = f.category === "fairness" && (cf?.example || cf?.case_a);
        return (
          <div>
            <PageHeader
              title={`#${f.number} · ${f.title}`}
              breadcrumbs={[{ label: "Findings", href: "/dashboard/findings" }, { label: `#${f.number}` }]}
              actions={
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={f.severity} />
                  <RiskBadge level={f.risk_level} />
                </div>
              }
            />

            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <div className="space-y-4">
                <Card>
                  <CardBody>
                    <p className="text-sm text-[var(--color-text)]">{f.description}</p>
                    {f.impact ? <p className="mt-2 text-sm text-[var(--color-text-muted)]">{f.impact}</p> : null}
                    <div className="mt-3 flex flex-wrap gap-2 text-xs">
                      {f.control_ref ? <Meta label="Control" value={f.control_ref} /> : null}
                      {f.test_type ? <Meta label="Test" value={titleCase(f.test_type)} /> : null}
                      {f.model_version ? <Meta label="Model" value={f.model_version} /> : null}
                      <Meta label="Evaluator" value={`${f.evaluator_key ?? "—"} v${f.evaluator_version ?? ""}`} />
                    </div>
                  </CardBody>
                </Card>

                {isCounterfactual ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2"><Scale className="h-4 w-4 text-[var(--color-accent-bright)]" /> Counterfactual Treatment Test</CardTitle>
                    </CardHeader>
                    <CardBody>
                      <CounterfactualComparison data={cf} />
                    </CardBody>
                  </Card>
                ) : null}

                <Card>
                  <CardHeader><CardTitle>Evidence</CardTitle></CardHeader>
                  <CardBody>
                    <QueryBoundary query={evidence} skeleton={<div className="h-32 skeleton" />}>
                      {(items) => (items.length ? <EvidenceList items={items} /> : <p className="text-sm text-[var(--color-text-subtle)]">{f.evidence_unavailable_reason ?? "No evidence recorded."}</p>)}
                    </QueryBoundary>
                  </CardBody>
                </Card>

                {retestResult ? (
                  <Card>
                    <CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-[var(--color-success)]" /> Re-test Result</CardTitle></CardHeader>
                    <CardBody>
                      <div className="mb-3 flex items-center gap-3">
                        <span className="text-sm">Verdict:</span>
                        <StatusBadge status={retestResult.verdict} />
                      </div>
                      {retestResult.results?.map((r: any, i: number) => (
                        <div key={i} className="flex items-center gap-3 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm">
                          <span className="flex-1 truncate">{r.name}</span>
                          <SeverityBadge severity={r.before?.severity ?? "high"} />
                          <ArrowRight className="h-4 w-4 text-[var(--color-text-subtle)]" />
                          {r.after?.after_status === "failed" ? <SeverityBadge severity={r.after?.severity ?? "high"} /> : <span className="rounded-md bg-[color-mix(in_srgb,var(--color-success)_14%,transparent)] px-2 py-0.5 text-xs font-medium text-[var(--color-success)]">Resolved</span>}
                        </div>
                      ))}
                      <p className="mt-2 text-xs text-[var(--color-text-subtle)]">Measured result from re-running the exact test after remediation.</p>
                    </CardBody>
                  </Card>
                ) : null}
              </div>

              <div className="space-y-4">
                <Card>
                  <CardHeader><CardTitle>Risk</CardTitle></CardHeader>
                  <CardBody>
                    <div className="mb-3 flex items-baseline gap-2">
                      <span className="font-mono text-3xl font-semibold" style={{ color: `var(--color-${f.risk_level === "informational" ? "info" : f.risk_level})` }}>{f.risk_score.toFixed(0)}</span>
                      <RiskBadge level={f.risk_level} />
                    </div>
                    <p className="mb-2 text-xs font-medium text-[var(--color-text-muted)]">Why this risk level</p>
                    <ul className="space-y-1 text-xs text-[var(--color-text-muted)]">
                      {f.risk_reasons.map((r, i) => <li key={i}>• {r}</li>)}
                    </ul>
                    <div className="mt-3 space-y-1.5">
                      {f.risk_factors.slice(0, 5).map((factor) => (
                        <div key={factor.key}>
                          <div className="flex justify-between text-[10px] text-[var(--color-text-subtle)]"><span>{factor.label}</span><span className="font-mono">{(factor.value * 100).toFixed(0)}%</span></div>
                          <div className="h-1 overflow-hidden rounded-full bg-[var(--color-surface-3)]"><div className="h-full rounded-full bg-[var(--color-accent)]" style={{ width: `${factor.value * 100}%` }} /></div>
                        </div>
                      ))}
                    </div>
                  </CardBody>
                </Card>

                <Card>
                  <CardHeader><CardTitle>Manage</CardTitle></CardHeader>
                  <CardBody className="space-y-3">
                    <label className="block">
                      <span className="mb-1 block text-xs text-[var(--color-text-muted)]">Status</span>
                      <select value={f.status} onChange={(e) => update.mutate({ status: e.target.value })} className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-2.5 py-1.5 text-sm outline-none focus:border-[var(--color-accent)]">
                        {STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                      </select>
                    </label>
                    <Button className="w-full" variant="secondary" icon={Sparkles} onClick={() => remediate(f)}>
                      Apply recommended fix
                    </Button>
                    <Button className="w-full" icon={GitBranch} loading={retesting} onClick={() => retest(f)}>
                      Create regression test & re-test
                    </Button>
                    <p className="text-[10px] text-[var(--color-text-subtle)]">Fairness results describe controlled behavioural differences, not proof of real-world discrimination.</p>
                  </CardBody>
                </Card>
              </div>
            </div>
          </div>
        );
      }}
    </QueryBoundary>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1">
      <span className="text-[var(--color-text-subtle)]">{label}:</span>
      <span className="font-mono text-[var(--color-text)]">{value}</span>
    </span>
  );
}
