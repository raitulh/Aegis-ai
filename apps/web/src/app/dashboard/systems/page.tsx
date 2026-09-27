"use client";
import Link from "next/link";
import { Boxes, Plus } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Badge, Button, EmptyState } from "@/components/ui/primitives";
import { NewSystemDialog } from "@/components/dashboard/new-system-dialog";
import { useSystems } from "@/lib/queries";
import { titleCase } from "@/lib/utils";

const TIER_TONE: Record<string, string> = { critical: "var(--color-critical)", high: "var(--color-high)", limited: "var(--color-medium)", minimal: "var(--color-info)" };

export default function SystemsPage() {
  const [open, setOpen] = useState(false);
  const query = useSystems({ page_size: 100 });
  return (
    <div>
      <PageHeader title="AI Systems" description="Register the models and agents you want to continuously assure." actions={<Button icon={Plus} onClick={() => setOpen(true)}>Add AI System</Button>} />
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState icon={Boxes} title="Register your first AI system" description="Connect a model provider or endpoint, then run audits against it." action={<Button size="sm" icon={Plus} onClick={() => setOpen(true)}>Add AI System</Button>} />
          ) : (
            <DataTable>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Type</TH>
                  <TH>Environment</TH>
                  <TH>Risk tier</TH>
                  <TH>Model</TH>
                  <TH>Version</TH>
                </TR>
              </THead>
              <tbody>
                {page.items.map((s) => (
                  <TR key={s.id} onClick={() => (window.location.href = `/dashboard/systems/${s.id}`)}>
                    <TD className="font-medium">
                      <div className="flex items-center gap-2">
                        {s.name}
                        {s.is_demo ? <Badge tone="accent">Simulated</Badge> : null}
                      </div>
                    </TD>
                    <TD className="text-[var(--color-text-muted)]">{titleCase(s.system_type)}</TD>
                    <TD className="text-[var(--color-text-muted)]">{titleCase(s.environment)}</TD>
                    <TD><span className="inline-flex items-center gap-1.5 text-xs"><span className="h-1.5 w-1.5 rounded-full" style={{ background: TIER_TONE[s.risk_tier] }} />{titleCase(s.risk_tier)}</span></TD>
                    <TD className="font-mono text-xs text-[var(--color-text-muted)]">{s.model_name ?? "—"}</TD>
                    <TD className="font-mono text-xs">{s.version}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
      <NewSystemDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}
