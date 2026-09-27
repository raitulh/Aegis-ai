"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { EmptyState, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { useFindings } from "@/lib/queries";
import { timeAgo, titleCase } from "@/lib/utils";

const FILTERS: { param: string; label: string; options: string[] }[] = [
  { param: "severity", label: "Severity", options: ["critical", "high", "medium", "low", "info"] },
  { param: "status", label: "Status", options: ["open", "acknowledged", "in_remediation", "resolved", "accepted_risk", "false_positive"] },
  { param: "category", label: "Category", options: ["fairness", "hallucination", "groundedness", "privacy", "safety", "prompt_injection", "policy", "agent_action"] },
];

export default function FindingsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const filters = Object.fromEntries(FILTERS.map((f) => [f.param, params.get(f.param) ?? ""]).filter(([, v]) => v));
  const query = useFindings({ page_size: 100, ...filters });

  function setFilter(param: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(param, value);
    else next.delete(param);
    router.replace(`/dashboard/findings?${next.toString()}`);
  }

  return (
    <div>
      <PageHeader title="Findings" description="Evidence-backed risks across your AI systems, ranked by explainable risk score." />
      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <select key={f.param} value={(filters as Record<string, string>)[f.param] ?? ""} onChange={(e) => setFilter(f.param, e.target.value)} className="rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs outline-none focus:border-[var(--color-accent)]">
            <option value="">{f.label}: All</option>
            {f.options.map((o) => (
              <option key={o} value={o}>{titleCase(o)}</option>
            ))}
          </select>
        ))}
        {Object.keys(filters).length > 0 ? (
          <button onClick={() => router.replace("/dashboard/findings")} className="text-xs text-[var(--color-accent-bright)] hover:underline">Clear filters</button>
        ) : null}
        {/* Download of an API/BFF endpoint, not a Next.js page route — an anchor with `download` is correct here. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a href="/bff/api/v1/findings/export" download className="ml-auto text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Export CSV</a>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState icon={ShieldAlert} title="No findings" description="Run an audit to discover risks in your AI systems." />
          ) : (
            <DataTable>
              <THead><TR><TH>#</TH><TH>Finding</TH><TH>Category</TH><TH>Severity</TH><TH>Risk</TH><TH>Status</TH><TH>Occurrences</TH><TH>Age</TH></TR></THead>
              <tbody>
                {page.items.map((f) => (
                  <TR key={f.id} onClick={() => (window.location.href = `/dashboard/findings/${f.id}`)}>
                    <TD className="font-mono text-xs text-[var(--color-text-subtle)]">{f.number}</TD>
                    <TD className="max-w-xs font-medium"><span className="line-clamp-1">{f.title}</span>{f.control_ref ? <span className="ml-1 font-mono text-xs text-[var(--color-text-subtle)]">{f.control_ref}</span> : null}</TD>
                    <TD className="text-[var(--color-text-muted)]">{titleCase(f.category)}</TD>
                    <TD><SeverityBadge severity={f.severity} /></TD>
                    <TD><RiskBadge level={f.risk_level} /></TD>
                    <TD><StatusBadge status={f.status} /></TD>
                    <TD className="font-mono text-xs">{f.occurrences}/{f.sample_size}</TD>
                    <TD className="text-[var(--color-text-subtle)]">{timeAgo(f.created_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
    </div>
  );
}
