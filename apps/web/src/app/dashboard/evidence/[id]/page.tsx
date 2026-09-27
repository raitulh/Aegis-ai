"use client";
import { use } from "react";
import { Lock } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Badge, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { EvidenceGraph } from "@/components/dashboard/evidence-graph";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Evidence } from "@/lib/types";
import { formatDateTime, titleCase } from "@/lib/utils";

export default function EvidenceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useQuery({ queryKey: ["evidence", id], queryFn: () => api.get<Evidence>(`/evidence/${id}`) });
  const graph = useQuery({ queryKey: ["evidence", id, "graph"], queryFn: () => api.get<any>(`/evidence/${id}/graph`) });
  return (
    <QueryBoundary query={query} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(e) => (
        <div>
          <PageHeader title={e.title} breadcrumbs={[{ label: "Evidence", href: "/dashboard/evidence" }, { label: `#${e.seq}` }]} actions={<div className="flex items-center gap-2"><Badge>{titleCase(e.kind)}</Badge>{e.sensitive ? <span className="inline-flex items-center gap-1 text-xs text-[var(--color-medium)]"><Lock className="h-3.5 w-3.5" /> Sensitive</span> : null}</div>} />
          <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
            <Card>
              <CardHeader><CardTitle>Artifact Content {e.sensitive ? "(masked)" : ""}</CardTitle></CardHeader>
              <CardBody><pre className="max-h-[480px] overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-[var(--color-text-muted)]">{JSON.stringify(e.content, null, 2)}</pre></CardBody>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader><CardTitle>Integrity</CardTitle></CardHeader>
                <CardBody className="space-y-2 text-xs">
                  <Row label="Confidence" value={titleCase(e.confidence_level)} />
                  <Row label="Content hash" value={`${e.content_hash.slice(0, 20)}…`} mono />
                  <Row label="Chain hash" value={`${e.chain_hash.slice(0, 20)}…`} mono />
                  <Row label="Captured" value={formatDateTime(e.created_at)} />
                  {e.confidence_reasons.length ? <p className="pt-1 text-[var(--color-text-subtle)]">{e.confidence_reasons.join(" · ")}</p> : null}
                </CardBody>
              </Card>
              <Card>
                <CardHeader><CardTitle>Evidence Graph</CardTitle></CardHeader>
                <CardBody><QueryBoundary query={graph} skeleton={<div className="h-40 skeleton" />}>{(g) => <EvidenceGraph data={g} />}</QueryBoundary></CardBody>
              </Card>
            </div>
          </div>
        </div>
      )}
    </QueryBoundary>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return <div className="flex justify-between gap-3"><span className="text-[var(--color-text-subtle)]">{label}</span><span className={mono ? "font-mono text-[var(--color-text-muted)]" : "text-[var(--color-text-muted)]"}>{value}</span></div>;
}
