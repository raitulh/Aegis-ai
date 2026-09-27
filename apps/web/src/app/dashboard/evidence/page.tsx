"use client";
import { FileText } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Badge, EmptyState } from "@/components/ui/primitives";
import { useQuery } from "@tanstack/react-query";
import { api, type Page } from "@/lib/api";
import type { Evidence } from "@/lib/types";
import { timeAgo, titleCase } from "@/lib/utils";

export default function EvidencePage() {
  const query = useQuery({ queryKey: ["evidence-list"], queryFn: () => api.get<Page<Evidence>>("/evidence", { page_size: 100 }) });
  return (
    <div>
      <PageHeader title="Evidence" description="Immutable, hash-chained audit evidence. Every finding is backed by artifacts you can inspect." />
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState icon={FileText} title="No evidence yet" description="Evidence is captured automatically during audits." />
          ) : (
            <DataTable>
              <THead><TR><TH>#</TH><TH>Kind</TH><TH>Title</TH><TH>Confidence</TH><TH>Hash</TH><TH>Captured</TH></TR></THead>
              <tbody>
                {page.items.map((e) => (
                  <TR key={e.id} onClick={() => (window.location.href = `/dashboard/evidence/${e.id}`)}>
                    <TD className="font-mono text-xs">{e.seq}</TD>
                    <TD><Badge>{titleCase(e.kind)}</Badge></TD>
                    <TD className="font-medium">{e.title}</TD>
                    <TD className="capitalize text-[var(--color-text-muted)]">{e.confidence_level}</TD>
                    <TD className="font-mono text-xs text-[var(--color-text-subtle)]">{e.content_hash.slice(0, 12)}…</TD>
                    <TD className="text-[var(--color-text-subtle)]">{timeAgo(e.created_at)}</TD>
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
