"use client";
import { useQuery } from "@tanstack/react-query";
import { Flag, Gauge, Radar, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog } from "@/components/ui/dialog";
import { CodeBlock, Hash, KeyValue } from "@/components/ui/display";
import { TabPanel, TabTrigger, Tabs, TabsList } from "@/components/ui/overlays";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, RiskBadge, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path, type Page } from "@/lib/api";
import { RUNTIME_MODE_META } from "@/lib/format";
import { useCan, useInvalidate, useRuntimeOverview, useSystem, useSystemAudits } from "@/lib/queries";
import type { ChainVerification, FindingSummary, System } from "@/lib/types";
import { formatDateTime, timeAgo, titleCase } from "@/lib/utils";

export default function SystemDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const system = useSystem(id);
  return (
    <QueryBoundary query={system} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(s) => <SystemView s={s} />}
    </QueryBoundary>
  );
}

function SystemView({ s }: { s: System }) {
  const can = useCan();
  const router = useRouter();
  const invalidate = useInvalidate();
  const [tab, setTab] = useState("overview");
  const [deleting, setDeleting] = useState(false);
  const mode = s.runtime_mode ?? "observe";

  async function remove() {
    try {
      await api.delete(path`/systems/${s.id}`);
      toast.success(`${s.name} deleted`);
      invalidate("systems");
      router.push("/dashboard/systems");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div>
      <PageHeader
        title={s.name}
        breadcrumbs={[{ label: "AI systems", href: "/dashboard/systems" }, { label: s.name }]}
        description={s.business_purpose ?? s.description ?? undefined}
        actions={
          <div className="flex items-center gap-2">
            {can("systems:delete") ? (
              <Button variant="ghost" size="sm" icon={Trash2} onClick={() => setDeleting(true)}>
                Delete
              </Button>
            ) : null}
            {can("audits:run") ? (
              <Link href={`/dashboard/audits/new?system=${encodeURIComponent(s.id)}`}>
                <Button icon={Gauge}>Run audit</Button>
              </Link>
            ) : null}
          </div>
        }
      />
      <div className="mb-5 flex flex-wrap gap-2">
        <Badge>{titleCase(s.system_type)}</Badge>
        <Badge>{titleCase(s.environment)}</Badge>
        <Badge>Risk tier: {titleCase(s.risk_tier)}</Badge>
        {s.model_name ? <Badge>{s.model_name}</Badge> : null}
        <Badge>v{s.version}</Badge>
        <Badge tone={mode === "enforce" ? "warning" : "neutral"}>Runtime: {RUNTIME_MODE_META[mode]?.label ?? mode}</Badge>
        {s.is_demo ? <Badge tone="warning">DEMO</Badge> : null}
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabTrigger value="overview">Overview</TabTrigger>
          <TabTrigger value="audits">Audits</TabTrigger>
          <TabTrigger value="findings">Findings</TabTrigger>
          {can("runtime:read") ? <TabTrigger value="runtime">Runtime</TabTrigger> : null}
          <TabTrigger value="config">Configuration</TabTrigger>
        </TabsList>

        <TabPanel value="overview" className="pt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Details</CardTitle>
              </CardHeader>
              <CardBody>
                <KeyValue
                  items={[
                    ["Owner", s.owner_name ?? "—"],
                    ["Data classification", titleCase(s.data_classification)],
                    ["Model version", s.model_version ?? "—"],
                    ["Status", titleCase(s.status)],
                    ["Created", formatDateTime(s.created_at)],
                    ["Updated", formatDateTime(s.updated_at)],
                  ]}
                />
              </CardBody>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Assurance</CardTitle>
              </CardHeader>
              <CardBody>
                <KeyValue
                  items={[
                    ["Runtime mode", <span key="m">{RUNTIME_MODE_META[mode]?.label ?? mode}</span>],
                    [
                      "Known-good baseline",
                      s.baseline_audit_id ? (
                        <Link key="b" href={`/dashboard/audits/${s.baseline_audit_id}`} className="inline-flex items-center gap-1 text-[var(--color-accent-bright)] hover:underline">
                          <Flag className="h-3 w-3" aria-hidden /> Open baseline audit
                        </Link>
                      ) : (
                        "Not set — mark a completed audit as baseline"
                      ),
                    ],
                  ]}
                />
                <p className="mt-3 text-xs text-[var(--color-text-subtle)]">{RUNTIME_MODE_META[mode]?.description}</p>
              </CardBody>
            </Card>
          </div>
        </TabPanel>

        <TabPanel value="audits" className="pt-4">
          {tab === "audits" ? <SystemAudits systemId={s.id} /> : null}
        </TabPanel>
        <TabPanel value="findings" className="pt-4">
          {tab === "findings" ? <SystemFindings systemId={s.id} /> : null}
        </TabPanel>
        <TabPanel value="runtime" className="pt-4">
          {tab === "runtime" ? <SystemRuntime systemId={s.id} /> : null}
        </TabPanel>
        <TabPanel value="config" className="pt-4">
          <CodeBlock language="configuration (json)" code={JSON.stringify(s.config, null, 2)} />
        </TabPanel>
      </Tabs>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${s.name}?`}
        description="The system is removed from your workspace and can no longer be audited. Audits, findings and evidence already recorded are retained for the audit trail."
        confirmLabel="Delete system"
        onConfirm={remove}
      />
    </div>
  );
}

function SystemAudits({ systemId }: { systemId: string }) {
  const audits = useSystemAudits(systemId);
  return (
    <QueryBoundary query={audits} skeleton={<div className="h-32 skeleton" />}>
      {(list) =>
        list.length ? (
          <ul className="space-y-2">
            {list.map((a) => (
              <li key={a.id}>
                <Link href={`/dashboard/audits/${a.id}`} className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 hover:border-[var(--color-border-strong)]">
                  <div>
                    <p className="text-sm font-medium">{a.name}</p>
                    <p className="text-xs text-[var(--color-text-subtle)]">
                      {timeAgo(a.created_at)} · {a.findings_count} findings
                    </p>
                  </div>
                  <StatusBadge status={a.status} />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={Gauge} title="No audits yet" description="Run an audit to build this system's history and set a baseline." />
        )
      }
    </QueryBoundary>
  );
}

function SystemFindings({ systemId }: { systemId: string }) {
  const findings = useQuery({ queryKey: ["findings", { system_id: systemId, open_only: true }], queryFn: () => api.get<Page<FindingSummary>>("/findings", { system_id: systemId, open_only: true, page_size: 100 }) });
  return (
    <QueryBoundary query={findings} skeleton={<div className="h-32 skeleton" />}>
      {(data) =>
        data.items.length ? (
          <ul className="space-y-2">
            {data.items.map((f) => (
              <li key={f.id}>
                <Link href={`/dashboard/findings/${f.id}`} className="flex items-center justify-between gap-3 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 hover:border-[var(--color-border-strong)]">
                  <span className="truncate text-sm">
                    #{f.number} {f.title}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <StatusBadge status={f.status} />
                    <RiskBadge level={f.risk_level} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={ShieldCheck} title="No open findings" description="Nothing open for this system. Resolved and accepted findings remain in the findings list." />
        )
      }
    </QueryBoundary>
  );
}

function SystemRuntime({ systemId }: { systemId: string }) {
  const overview = useRuntimeOverview({ hours: 24 * 7, system_id: systemId });
  const chain = useQuery({ queryKey: ["system", systemId, "runtime-verify"], queryFn: () => api.get<ChainVerification>(path`/systems/${systemId}/runtime/verify`) });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Radar className="h-4 w-4" aria-hidden /> Last 7 days
          </CardTitle>
          <Link href="/dashboard/runtime" className="text-xs text-[var(--color-accent-bright)] hover:underline">
            Runtime Guard
          </Link>
        </CardHeader>
        <CardBody>
          <QueryBoundary query={overview} skeleton={<div className="h-24 skeleton" />}>
            {(o) => (
              <KeyValue
                items={[
                  ["Events", o.events.toLocaleString()],
                  ["Flagged", o.decisions.flag.toLocaleString()],
                  ["Held for approval", o.decisions.require_approval.toLocaleString()],
                  ["Blocked", o.decisions.block.toLocaleString()],
                  ["Pending approvals", o.pending_approvals],
                ]}
              />
            )}
          </QueryBoundary>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Runtime evidence chain</CardTitle>
        </CardHeader>
        <CardBody>
          <QueryBoundary query={chain} skeleton={<div className="h-24 skeleton" />}>
            {(v) => (
              <div className="space-y-2">
                <StatusBadge status={v.status} />
                <KeyValue
                  items={[
                    ["Records", v.records],
                    ["Head", <Hash key="h" value={v.head} />],
                  ]}
                />
                {v.problems.length ? <p className="text-xs text-[var(--color-critical)]">{v.problems.length} integrity problems — see Evidence.</p> : null}
              </div>
            )}
          </QueryBoundary>
        </CardBody>
      </Card>
    </div>
  );
}
