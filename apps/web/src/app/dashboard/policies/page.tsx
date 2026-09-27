"use client";
import { useState } from "react";
import { Plus, ScrollText } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Badge, Button, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { NewPolicyDialog } from "@/components/dashboard/new-policy-dialog";
import { usePolicies } from "@/lib/queries";
import { timeAgo } from "@/lib/utils";

export default function PoliciesPage() {
  const [open, setOpen] = useState(false);
  const query = usePolicies();
  return (
    <div>
      <PageHeader title="Policies" description="Compile natural-language policies into executable controls with full source provenance." actions={<Button icon={Plus} onClick={() => setOpen(true)}>New Policy</Button>} />
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState icon={ScrollText} title="No policies yet" description="Create a policy from text, or upload a PDF/DOCX to compile it into controls." action={<Button size="sm" icon={Plus} onClick={() => setOpen(true)}>New Policy</Button>} />
          ) : (
            <DataTable>
              <THead><TR><TH>Policy</TH><TH>Key</TH><TH>Status</TH><TH>Category</TH><TH>Updated</TH></TR></THead>
              <tbody>
                {page.items.map((p) => (
                  <TR key={p.id} onClick={() => (window.location.href = `/dashboard/policies/${p.id}`)}>
                    <TD className="font-medium"><div className="flex items-center gap-2">{p.name}{p.is_demo ? <Badge tone="accent">Simulated</Badge> : null}</div></TD>
                    <TD className="font-mono text-xs">{p.key}</TD>
                    <TD><StatusBadge status={p.status} /></TD>
                    <TD className="text-[var(--color-text-muted)]">{p.category ?? "—"}</TD>
                    <TD className="text-[var(--color-text-subtle)]">{timeAgo(p.updated_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
      <NewPolicyDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}
