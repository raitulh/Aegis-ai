"use client";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";
import { FileText, Gauge, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import { useCan, usePolicy, usePolicyControls, useInvalidate } from "@/lib/queries";
import { titleCase } from "@/lib/utils";
import type { Control } from "@/lib/types";

export default function PolicyDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const policyQuery = usePolicy(id);
  const controlsQuery = usePolicyControls(id);
  const invalidate = useInvalidate();
  const can = useCan();
  const [compiling, setCompiling] = useState(false);

  async function compile() {
    setCompiling(true);
    try {
      const result = await api.post<{ requirements: unknown[]; controls: Control[]; report: Record<string, unknown> }>(path`/policies/${id}/compile`);
      invalidate("policy", id);
      invalidate("policy", id, "controls");
      toast.success(`Compiled ${result.controls.length} controls from ${result.requirements.length} requirements`);
    } catch (err) {
      toast.error(errorMessage(err, "Compile failed"));
    } finally {
      setCompiling(false);
    }
  }

  return (
    <QueryBoundary query={policyQuery} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(policy) => (
        <div>
          <PageHeader
            title={policy.name}
            breadcrumbs={[{ label: "Policies", href: "/dashboard/policies?tab=compliance" }, { label: policy.key }]}
            actions={
              <div className="flex items-center gap-2">
                <StatusBadge status={policy.status} />
                {can("policies:compile") ? <Button icon={Sparkles} loading={compiling} onClick={compile}>Compile</Button> : null}
              </div>
            }
          />

          {/* Policy Compiler pipeline visual */}
          <Card className="mb-4">
            <CardHeader><CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-[var(--color-accent-bright)]" /> Policy Compiler</CardTitle></CardHeader>
            <CardBody>
              <div className="flex flex-wrap items-center justify-center gap-2 text-xs sm:gap-3">
                {["Document", "Requirement", "Control", "Test", "Evidence"].map((step, i, arr) => (
                  <div key={step} className="flex items-center gap-2 sm:gap-3">
                    <div className="rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface-2)] px-3 py-1.5 font-medium">{step}</div>
                    {i < arr.length - 1 ? <span className="text-[var(--color-text-subtle)]">→</span> : null}
                  </div>
                ))}
              </div>
              <p className="mt-3 text-center text-xs text-[var(--color-text-subtle)]">Every requirement retains its source page and section. Compile to (re)generate controls.</p>
            </CardBody>
          </Card>

          <QueryBoundary query={controlsQuery} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
            {(controls) =>
              controls.length === 0 ? (
                <EmptyState icon={FileText} title="Not compiled yet" description="Click Compile to extract requirements and generate executable controls." action={can("policies:compile") ? <Button size="sm" icon={Sparkles} loading={compiling} onClick={compile}>Compile now</Button> : null} />
              ) : (
                <div className="grid gap-3 lg:grid-cols-2">
                  {controls.map((c) => (
                    <ControlCard key={c.id} control={c} />
                  ))}
                </div>
              )
            }
          </QueryBoundary>

          <div className="mt-4 flex items-center justify-between">
            <p className="text-xs text-[var(--color-text-subtle)]">Controls are mapped to NIST AI RMF, OWASP LLM Top 10 and ISO/IEC 42001 for reference. A mapping is not a compliance attestation.</p>
            <Link href={`/dashboard/audits/new`}><Button variant="secondary" size="sm" icon={Gauge}>Audit against this policy</Button></Link>
          </div>
        </div>
      )}
    </QueryBoundary>
  );
}

function ControlCard({ control }: { control: Control }) {
  const p = control.provenance;
  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between">
          <span className="font-mono text-sm font-semibold text-[var(--color-accent-bright)]">{control.control_id}</span>
          <div className="flex items-center gap-2">
            <SeverityBadge severity={control.severity} />
            {control.needs_human_review ? <Badge tone="warning">Review</Badge> : null}
          </div>
        </div>
        <p className="mt-2 text-sm font-medium">{control.name}</p>
        {control.description ? <p className="mt-1 text-xs text-[var(--color-text-muted)]">{control.description}</p> : null}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <Badge>{titleCase(control.test_type)}</Badge>
          <Badge>{titleCase(control.domain)}</Badge>
          <Badge>{titleCase(control.automation)}</Badge>
        </div>
        {p ? (
          <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-bg)]/40 p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-[var(--color-text-subtle)]">
              Source provenance · {p.requirement_key}
              {p.section ? ` · §${p.section}` : ""}
              {p.page_number ? ` · page ${p.page_number}` : ""}
            </p>
            <p className="mt-1 line-clamp-3 text-xs italic text-[var(--color-text-muted)]">“{p.source_excerpt || p.text}”</p>
            <p className="mt-1 font-mono text-[10px] text-[var(--color-text-subtle)]" title={p.source_hash}>
              sha256 {p.source_hash.slice(0, 12)}… · extraction confidence {(p.confidence * 100).toFixed(0)}%
            </p>
          </div>
        ) : control.source === "manual" ? (
          <p className="mt-3 text-[11px] text-[var(--color-text-subtle)]">Manually defined control (no source document).</p>
        ) : null}
      </CardBody>
    </Card>
  );
}
