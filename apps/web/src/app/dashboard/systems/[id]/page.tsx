"use client";
import Link from "next/link";
import { use, useState } from "react";
import { Gauge } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, RiskBadge, StatusBadge } from "@/components/ui/primitives";
import { TabPanel, TabTrigger, Tabs, TabsList } from "@/components/ui/overlays";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useSystem } from "@/lib/queries";
import { formatDateTime, timeAgo, titleCase } from "@/lib/utils";
import type { Audit, FindingSummary } from "@/lib/types";

export default function SystemDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tab, setTab] = useState("overview");
  const system = useSystem(id);
  const audits = useQuery({ queryKey: ["system", id, "audits"], queryFn: () => api.get<Audit[]>(`/systems/${id}/audits`) });
  const findings = useQuery({ queryKey: ["findings", { system_id: id }], queryFn: () => api.get<{ items: FindingSummary[] }>("/findings", { system_id: id, page_size: 100 }) });

  return (
    <QueryBoundary query={system} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(s) => (
        <div>
          <PageHeader
            title={s.name}
            breadcrumbs={[{ label: "AI Systems", href: "/dashboard/systems" }, { label: s.name }]}
            description={s.business_purpose ?? s.description ?? undefined}
            actions={<Link href={`/dashboard/audits/new?system=${s.id}`}><Button icon={Gauge}>Run Audit</Button></Link>}
          />
          <div className="mb-5 flex flex-wrap gap-2">
            <Badge>{titleCase(s.system_type)}</Badge>
            <Badge>{titleCase(s.environment)}</Badge>
            <Badge>Risk tier: {titleCase(s.risk_tier)}</Badge>
            <Badge>{s.model_name ?? "no model"}</Badge>
            <Badge>{s.version}</Badge>
            {s.is_demo ? <Badge tone="accent">Simulated</Badge> : null}
          </div>

          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabTrigger value="overview">Overview</TabTrigger>
              <TabTrigger value="audits">Audit History</TabTrigger>
              <TabTrigger value="findings">Findings</TabTrigger>
              <TabTrigger value="config">Configuration</TabTrigger>
            </TabsList>

            <TabPanel value="overview" className="pt-4">
              <Card><CardHeader><CardTitle>Details</CardTitle></CardHeader><CardBody>
                <dl className="grid gap-2 text-sm sm:grid-cols-2">
                  <Row label="Owner" value={s.owner_name ?? "—"} />
                  <Row label="Data classification" value={titleCase(s.data_classification)} />
                  <Row label="Model version" value={s.model_version ?? "—"} />
                  <Row label="Created" value={formatDateTime(s.created_at)} />
                </dl>
              </CardBody></Card>
            </TabPanel>

            <TabPanel value="audits" className="pt-4">
              <QueryBoundary query={audits} skeleton={<div className="h-32 skeleton" />}>
                {(list) => list.length ? (
                  <div className="space-y-2">
                    {list.map((a) => (
                      <Link key={a.id} href={`/dashboard/audits/${a.id}`} className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 hover:border-[var(--color-border-strong)]">
                        <div><p className="text-sm font-medium">{a.name}</p><p className="text-xs text-[var(--color-text-subtle)]">{timeAgo(a.created_at)} · {a.findings_count} findings</p></div>
                        <StatusBadge status={a.status} />
                      </Link>
                    ))}
                  </div>
                ) : <EmptyState icon={Gauge} title="No audits" description="Run an audit to build this system's history." />}
              </QueryBoundary>
            </TabPanel>

            <TabPanel value="findings" className="pt-4">
              <QueryBoundary query={findings} skeleton={<div className="h-32 skeleton" />}>
                {(data) => data.items.length ? (
                  <div className="space-y-2">
                    {data.items.map((f) => (
                      <Link key={f.id} href={`/dashboard/findings/${f.id}`} className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 hover:border-[var(--color-border-strong)]">
                        <span className="text-sm">#{f.number} {f.title}</span>
                        <RiskBadge level={f.risk_level} />
                      </Link>
                    ))}
                  </div>
                ) : <EmptyState title="No findings" description="This system has no open findings." />}
              </QueryBoundary>
            </TabPanel>

            <TabPanel value="config" className="pt-4">
              <Card><CardBody><pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-[var(--color-text-muted)]">{JSON.stringify(s.config, null, 2)}</pre></CardBody></Card>
            </TabPanel>
          </Tabs>
        </div>
      )}
    </QueryBoundary>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between border-b border-[var(--color-border)]/50 pb-1.5"><dt className="text-[var(--color-text-muted)]">{label}</dt><dd className="font-medium">{value}</dd></div>;
}
