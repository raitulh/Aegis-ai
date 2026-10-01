"use client";
import { use } from "react";
import { ShieldCheck, ShieldX } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Card, CardBody, CardHeader, CardTitle, EmptyState, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { useQuery } from "@tanstack/react-query";
import { api, path } from "@/lib/api";
import { cn, titleCase } from "@/lib/utils";
import type { RedTeamProbe, RedTeamRun } from "@/lib/types";

const TERMINAL = ["completed", "failed", "cancelled"];

export default function RedTeamRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const runQuery = useQuery({
    queryKey: ["redteam", id],
    queryFn: () => api.get<RedTeamRun>(path`/redteam/runs/${id}`),
    refetchInterval: (q) => (TERMINAL.includes((q.state.data as RedTeamRun | undefined)?.status ?? "") ? false : 2000),
  });
  const running = !!runQuery.data && !TERMINAL.includes(runQuery.data.status);
  // Probes stream in while the run is active: poll them too, then stop once the run is terminal.
  const probesQuery = useQuery({
    queryKey: ["redteam", id, "probes"],
    queryFn: () => api.get<RedTeamProbe[]>(path`/redteam/runs/${id}/probes`),
    enabled: !!runQuery.data,
    refetchInterval: running ? 2000 : false,
  });

  return (
    <QueryBoundary query={runQuery} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
      {(run) => {
        const s = run.summary ?? {};
        return (
          <div>
            <PageHeader title={run.name} breadcrumbs={[{ label: "Red Team", href: "/dashboard/red-team" }, { label: run.name }]} actions={<StatusBadge status={run.status} />} />
            {(s.empty_corpus as boolean) ? (
              <EmptyState icon={ShieldCheck} title="No probes executed" description="This run used an empty corpus. Import an adversarial dataset to populate the attack tree." />
            ) : (
              <>
                <div className="mb-5 grid gap-3 sm:grid-cols-4">
                  <Stat label="Probes executed" value={(s.probes_executed as number) ?? 0} />
                  <Stat label="Attack chains" value={(s.attack_chains as number) ?? 0} />
                  <Stat label="Bypasses" value={(s.bypasses as number) ?? 0} tone={(s.bypasses as number) ? "var(--color-high)" : "var(--color-success)"} />
                  <Stat label="Max depth" value={(s.max_depth_reached as number) ?? 0} />
                </div>
                <Card>
                  <CardHeader><CardTitle>Attack Lineage</CardTitle><span className="text-xs text-[var(--color-text-subtle)]">corpus: {(run.config?.corpus_name as string) ?? "custom"}</span></CardHeader>
                  <CardBody>
                    <QueryBoundary query={probesQuery} skeleton={<div className="h-40 skeleton" />}>
                      {(probes) => (probes.length ? <AttackTree probes={probes} /> : <p className="text-sm text-[var(--color-text-subtle)]">{running ? "Waiting for the first probe result…" : "No probe results were recorded for this run."}</p>)}
                    </QueryBoundary>
                  </CardBody>
                </Card>
              </>
            )}
          </div>
        );
      }}
    </QueryBoundary>
  );
}

function AttackTree({ probes }: { probes: RedTeamProbe[] }) {
  const roots = probes.filter((p) => !p.parent_id);
  const childrenOf = (pid: string) => probes.filter((p) => p.parent_id === pid);
  return (
    <div className="space-y-3">
      {roots.map((root) => (
        <div key={root.id} className="rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <ProbeNode probe={root} />
          <TreeChildren probe={root} childrenOf={childrenOf} depth={1} />
        </div>
      ))}
    </div>
  );
}

function TreeChildren({ probe, childrenOf, depth }: { probe: RedTeamProbe; childrenOf: (id: string) => RedTeamProbe[]; depth: number }) {
  const children = childrenOf(probe.id);
  if (!children.length) return null;
  return (
    <div className="ml-4 mt-2 space-y-2 border-l border-[var(--color-border)] pl-4">
      {children.map((c) => (
        <div key={c.id}>
          <ProbeNode probe={c} />
          <TreeChildren probe={c} childrenOf={childrenOf} depth={depth + 1} />
        </div>
      ))}
    </div>
  );
}

function ProbeNode({ probe }: { probe: RedTeamProbe }) {
  const bypassed = probe.result === "bypassed";
  return (
    <div className={cn("flex items-start gap-2.5 rounded-[var(--radius-sm)] px-2 py-1.5", bypassed && "bg-[color-mix(in_srgb,var(--color-critical)_8%,transparent)]")}>
      {bypassed ? <ShieldX className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-critical)]" /> : <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-success)]" />}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{titleCase(probe.technique)}</span>
          <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-subtle)]">{titleCase(probe.category)}</span>
          {bypassed ? <SeverityBadge severity={probe.severity} /> : <span className="text-xs text-[var(--color-success)]">blocked</span>}
        </div>
        <p className="mt-0.5 line-clamp-1 font-mono text-[11px] text-[var(--color-text-subtle)]">{probe.payload}</p>
        {probe.observed_behavior ? <p className="mt-0.5 line-clamp-2 text-xs text-[var(--color-text-muted)]">{probe.observed_behavior}</p> : null}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <p className="text-xs text-[var(--color-text-muted)]">{label}</p>
      <p className="mt-1 font-mono text-2xl font-semibold" style={{ color: tone }}>{value}</p>
    </div>
  );
}
