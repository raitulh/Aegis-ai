"use client";
import { Activity, Ban, CheckCircle2, Clock, Hand, Radar, ShieldAlert, SquareTerminal } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Metric } from "@/components/dashboard/metric";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { CodeBlock, KeyValue, Pagination } from "@/components/ui/display";
import { Field, Select, Textarea } from "@/components/ui/forms";
import { TabPanel, Tabs, TabsList, TabTrigger } from "@/components/ui/overlays";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import { RUNTIME_MODE_META } from "@/lib/format";
import { useApprovals, useCan, useInvalidate, useRuntimeEvents, useRuntimeOverview, useSystems } from "@/lib/queries";
import type { Approval, RuntimeEvent } from "@/lib/types";
import { cn, formatDateTime, timeAgo, titleCase } from "@/lib/utils";

const RuntimeTimelineChart = dynamic(() => import("@/components/charts/charts").then((m) => m.RuntimeTimelineChart), {
  ssr: false,
  loading: () => <div className="h-[220px] skeleton rounded-[var(--radius)]" />,
});

const TABS = ["overview", "events", "approvals", "modes"] as const;
type Tab = (typeof TABS)[number];
const WINDOWS = [
  { hours: 24, label: "24 hours" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
  { hours: 720, label: "30 days" },
];

export default function RuntimePage() {
  const search = useSearchParams();
  const router = useRouter();
  const initial = (TABS as readonly string[]).includes(search.get("tab") ?? "") ? (search.get("tab") as Tab) : "overview";
  const [tab, setTab] = useState<Tab>(initial);
  const [hours, setHours] = useState(24);
  const [systemId, setSystemId] = useState("");
  const systems = useSystems({ page_size: 100 });
  const overview = useRuntimeOverview({ hours, system_id: systemId || undefined });
  const pending = overview.data?.pending_approvals ?? 0;

  return (
    <div>
      <PageHeader
        title="Runtime Guard"
        description="Every agent action your systems report is checked against published runtime policies — observed, audited or enforced per system."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="System" value={systemId} onChange={(e) => setSystemId(e.target.value)} className="h-8 w-48 text-xs">
              <option value="">All systems</option>
              {(systems.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
            <Select aria-label="Time window" value={hours} onChange={(e) => setHours(Number(e.target.value))} className="h-8 w-32 text-xs">
              {WINDOWS.map((w) => (
                <option key={w.hours} value={w.hours}>
                  {w.label}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as Tab);
          router.replace(`/dashboard/runtime?tab=${v}`);
        }}
      >
        <TabsList className="mb-5">
          <TabTrigger value="overview">Overview</TabTrigger>
          <TabTrigger value="events">Events</TabTrigger>
          <TabTrigger value="approvals">
            Approvals
            {pending ? (
              <span className="ml-1.5 rounded-full bg-[var(--color-high)] px-1.5 py-px text-[10px] font-semibold text-white" aria-label={`${pending} pending`}>
                {pending}
              </span>
            ) : null}
          </TabTrigger>
          <TabTrigger value="modes">System modes</TabTrigger>
        </TabsList>
        <TabPanel value="overview">
          <QueryBoundary query={overview}>
            {(o) =>
              o.events === 0 ? (
                <NoEvents />
              ) : (
                <div className="space-y-4">
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    <Metric label="Events" value={o.events.toLocaleString()} icon={Activity} hint={`Last ${WINDOWS.find((w) => w.hours === hours)?.label}`} />
                    <Metric label="Allowed" value={o.decisions.allow.toLocaleString()} icon={CheckCircle2} />
                    <Metric label="Flagged" value={o.decisions.flag.toLocaleString()} icon={ShieldAlert} tone={o.decisions.flag ? "var(--color-medium)" : undefined} />
                    <Metric label="Held for approval" value={o.decisions.require_approval.toLocaleString()} icon={Hand} tone={o.decisions.require_approval ? "var(--color-high)" : undefined} />
                    <Metric label="Blocked" value={o.decisions.block.toLocaleString()} icon={Ban} tone={o.decisions.block ? "var(--color-critical)" : undefined} />
                  </div>
                  <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
                    <Card>
                      <CardHeader>
                        <CardTitle>Events and policy matches</CardTitle>
                        <span className="text-xs text-[var(--color-text-subtle)]">Per {hours <= 72 ? "hour" : "day"}</span>
                      </CardHeader>
                      <CardBody>
                        <RuntimeTimelineChart data={o.timeline} hourly={hours <= 72} />
                      </CardBody>
                    </Card>
                    <Card>
                      <CardHeader>
                        <CardTitle>Agents</CardTitle>
                      </CardHeader>
                      <CardBody>
                        {o.agents.length === 0 ? (
                          <p className="text-sm text-[var(--color-text-subtle)]">No agent names reported.</p>
                        ) : (
                          <ul className="space-y-2 text-sm">
                            {o.agents.slice(0, 8).map((a) => (
                              <li key={a.agent} className="flex items-center justify-between gap-3">
                                <span className="truncate font-mono text-xs">{a.agent}</span>
                                <span className="shrink-0 text-xs text-[var(--color-text-muted)]">
                                  {a.events.toLocaleString()} events
                                  {a.violations ? <span className="ml-2 text-[var(--color-high)]">{a.violations} matched</span> : null}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </CardBody>
                    </Card>
                  </div>
                  <Card>
                    <CardHeader>
                      <CardTitle>Event types</CardTitle>
                    </CardHeader>
                    <CardBody className="flex flex-wrap gap-2">
                      {Object.entries(o.event_types)
                        .sort((a, b) => b[1] - a[1])
                        .map(([type, n]) => (
                          <Badge key={type}>
                            <span className="font-mono">{type}</span> · {n.toLocaleString()}
                          </Badge>
                        ))}
                    </CardBody>
                  </Card>
                </div>
              )
            }
          </QueryBoundary>
        </TabPanel>
        <TabPanel value="events">{tab === "events" ? <EventsTab hours={hours} systemId={systemId} /> : null}</TabPanel>
        <TabPanel value="approvals">{tab === "approvals" ? <ApprovalsTab /> : null}</TabPanel>
        <TabPanel value="modes">{tab === "modes" ? <ModesTab /> : null}</TabPanel>
      </Tabs>
    </div>
  );
}

function NoEvents() {
  return (
    <EmptyState
      icon={Radar}
      title="No runtime events in this window"
      description="Instrument an agent with the Python SDK, the MCP server or the HTTP API. Events appear here within seconds; nothing is simulated."
      action={
        <Link href="/dashboard/developers?tab=runtime">
          <Button size="sm" icon={SquareTerminal}>
            Connect an agent
          </Button>
        </Link>
      }
    />
  );
}

const DECISIONS = ["allow", "flag", "require_approval", "block"];
const EVENT_TYPES = ["agent.start", "agent.step", "agent.stop", "tool.call", "tool.result", "model.request", "model.response", "policy.check", "permission.request", "network.request", "file.read", "file.write", "database.query", "mcp.tool.call", "human.approval"];

function EventsTab({ hours, systemId }: { hours: number; systemId: string }) {
  const [page, setPage] = useState(1);
  const [decision, setDecision] = useState("");
  const [eventType, setEventType] = useState("");
  const [open, setOpen] = useState<RuntimeEvent | null>(null);
  const systems = useSystems({ page_size: 100 });
  const names = useMemo(() => new Map((systems.data?.items ?? []).map((s) => [s.id, s.name])), [systems.data]);
  const query = useRuntimeEvents({ page, page_size: 50, hours, system_id: systemId || undefined, decision: decision || undefined, event_type: eventType || undefined });

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <Select aria-label="Decision" value={decision} onChange={(e) => {
            setDecision(e.target.value);
            setPage(1);
          }} className="h-8 w-auto text-xs">
          <option value="">Decision: all</option>
          {DECISIONS.map((d) => (
            <option key={d} value={d}>
              {titleCase(d)}
            </option>
          ))}
        </Select>
        <Select aria-label="Event type" value={eventType} onChange={(e) => {
            setEventType(e.target.value);
            setPage(1);
          }} className="h-8 w-auto text-xs">
          <option value="">Type: all</option>
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            decision || eventType ? <EmptyState title="No events match" description="Widen the filters or the time window." /> : <NoEvents />
          ) : (
            <>
              <DataTable caption="Runtime events">
                <THead>
                  <tr>
                    <TH>Event</TH>
                    <TH>System</TH>
                    <TH>Agent / tool</TH>
                    <TH>Mode</TH>
                    <TH>Decision</TH>
                    <TH>When</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((e) => (
                    <TR key={e.id} className="cursor-pointer hover:bg-[var(--color-surface)]/70">
                      <TD>
                        <button type="button" onClick={() => setOpen(e)} className="text-left font-mono text-xs font-medium hover:text-[var(--color-accent-bright)] focus-ring after:absolute after:inset-0">
                          {e.event_type}
                        </button>
                      </TD>
                      <TD className="text-[var(--color-text-muted)]">{names.get(e.system_id) ?? "—"}</TD>
                      <TD className="max-w-48 truncate font-mono text-xs text-[var(--color-text-muted)]">
                        {e.agent_name ?? "—"}
                        {e.tool_name ? ` · ${e.tool_name}` : ""}
                      </TD>
                      <TD className="text-xs text-[var(--color-text-muted)]">{titleCase(e.mode)}</TD>
                      <TD>
                        <StatusBadge status={e.effective_decision} />
                        {e.decision !== e.effective_decision ? <span className="ml-1 text-[10px] text-[var(--color-text-subtle)]">(would {titleCase(e.decision).toLowerCase()})</span> : null}
                      </TD>
                      <TD className="text-[var(--color-text-subtle)]">{timeAgo(e.occurred_at)}</TD>
                    </TR>
                  ))}
                </tbody>
              </DataTable>
              <Pagination page={data.meta.page} totalPages={data.meta.total_pages} onPage={setPage} />
            </>
          )
        }
      </QueryBoundary>
      <EventDialog event={open} onClose={() => setOpen(null)} systemName={open ? names.get(open.system_id) : undefined} />
    </div>
  );
}

function EventDialog({ event, onClose, systemName }: { event: RuntimeEvent | null; onClose: () => void; systemName?: string }) {
  return (
    <Dialog open={!!event} onOpenChange={(o) => !o && onClose()} title={event ? event.event_type : ""} description={event ? formatDateTime(event.occurred_at) : undefined} className="max-w-2xl">
      {event ? (
        <div className="space-y-4">
          <KeyValue
            items={[
              ["System", systemName ?? event.system_id],
              ["Decision", <StatusBadge key="d" status={event.effective_decision} />],
              ["Mode", titleCase(event.mode)],
              ["Reason", event.decision_reason ?? "—"],
              ["Agent", event.agent_name ?? "—"],
              ["Tool", event.tool_name ?? "—"],
              ["Trace", event.trace_id ? <span key="t" className="font-mono text-xs">{event.trace_id}</span> : "—"],
              ["Finding", event.finding_id ? <Link key="f" className="text-[var(--color-accent-bright)] hover:underline" href={`/dashboard/findings/${event.finding_id}`}>Open finding</Link> : "—"],
              ["Evidence", event.evidence_id ? <Link key="e" className="text-[var(--color-accent-bright)] hover:underline" href={`/dashboard/evidence/${event.evidence_id}`}>Open evidence</Link> : "—"],
            ]}
          />
          {event.policy_matches.length ? (
            <div>
              <p className="mb-1 text-xs font-medium text-[var(--color-text-muted)]">Policy matches</p>
              <ul className="space-y-1 text-xs">
                {event.policy_matches.map((m, i) => (
                  <li key={i} className="rounded border border-[var(--color-border)] px-2 py-1">
                    <span className="font-mono">
                      {m.policy_key}@{m.version} · {m.rule_id}
                    </span>{" "}
                    → <StatusBadge status={m.action} /> <span className="text-[var(--color-text-muted)]">{m.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <CodeBlock language="signals" code={JSON.stringify(event.signals, null, 2)} />
          <CodeBlock language="payload (redacted at ingest)" code={JSON.stringify(event.payload, null, 2)} />
        </div>
      ) : null}
    </Dialog>
  );
}

function ApprovalsTab() {
  const can = useCan();
  const invalidate = useInvalidate();
  const [status, setStatus] = useState("pending");
  const [deciding, setDeciding] = useState<{ approval: Approval; approve: boolean } | null>(null);
  const [note, setNote] = useState("");
  const query = useApprovals({ status: status || undefined, page_size: 50 });
  const systems = useSystems({ page_size: 100 });
  const names = useMemo(() => new Map((systems.data?.items ?? []).map((s) => [s.id, s.name])), [systems.data]);

  async function decide() {
    if (!deciding) return;
    try {
      await api.post(path`/runtime/approvals/${deciding.approval.id}/decision`, { approve: deciding.approve, note: note || undefined });
      toast.success(deciding.approve ? "Approved — the agent may proceed" : "Denied — the agent is told not to proceed");
      setNote("");
      invalidate("runtime");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Select aria-label="Approval status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-8 w-40 text-xs">
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="denied">Denied</option>
          <option value="expired">Expired</option>
          <option value="">All</option>
        </Select>
        <p className="text-xs text-[var(--color-text-subtle)]">Agents in enforce mode wait for these decisions. Unanswered requests expire and are treated as denied.</p>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState icon={Clock} title={status === "pending" ? "Nothing waiting for a decision" : "No approvals"} description="When a policy rule with action require_approval matches in enforce mode, the request appears here." />
          ) : (
            <ul className="space-y-2">
              {data.items.map((a) => {
                const expired = a.status === "pending" && new Date(a.expires_at) < new Date();
                return (
                  <li key={a.id}>
                    <Card>
                      <CardBody className="flex flex-wrap items-start gap-3">
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <StatusBadge status={expired ? "expired" : a.status} />
                            <span className="text-sm font-medium">{a.summary}</span>
                          </div>
                          <p className="text-xs text-[var(--color-text-subtle)]">
                            {names.get(a.system_id) ?? "System"} · rule <span className="font-mono">{a.rule_ref ?? "—"}</span> · requested {timeAgo(a.created_at)}
                            {a.status === "pending" ? ` · expires ${formatDateTime(a.expires_at)}` : ""}
                          </p>
                          {a.decided_at ? (
                            <p className="text-xs text-[var(--color-text-muted)]">
                              {titleCase(a.status)} by {a.decided_by_label ?? "—"} {timeAgo(a.decided_at)}
                              {a.decision_note ? ` — “${a.decision_note}”` : ""}
                            </p>
                          ) : null}
                          <details className="text-xs">
                            <summary className="cursor-pointer text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Request details</summary>
                            <pre className="mt-1 max-h-48 overflow-auto rounded bg-[var(--color-bg)] p-2 font-mono text-[11px]">{JSON.stringify(a.request, null, 2)}</pre>
                          </details>
                        </div>
                        {a.status === "pending" && !expired && can("approvals:decide") ? (
                          <div className="flex gap-2">
                            <Button size="sm" variant="secondary" onClick={() => setDeciding({ approval: a, approve: false })}>
                              Deny
                            </Button>
                            <Button size="sm" onClick={() => setDeciding({ approval: a, approve: true })}>
                              Approve
                            </Button>
                          </div>
                        ) : null}
                      </CardBody>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )
        }
      </QueryBoundary>
      <ConfirmDialog
        open={!!deciding}
        onOpenChange={(o) => !o && setDeciding(null)}
        tone={deciding?.approve ? "primary" : "danger"}
        title={deciding?.approve ? "Approve this action?" : "Deny this action?"}
        confirmLabel={deciding?.approve ? "Approve" : "Deny"}
        description={deciding ? <>“{deciding.approval.summary}”. Your decision and note are recorded as evidence and in the audit log.</> : null}
        onConfirm={decide}
      >
        <Field label="Note (optional)">{(p) => <Textarea {...p} value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={1000} />}</Field>
      </ConfirmDialog>
    </div>
  );
}

function ModesTab() {
  const can = useCan();
  const invalidate = useInvalidate();
  const overview = useRuntimeOverview({ hours: 24 });
  const [change, setChange] = useState<{ id: string; name: string; mode: string } | null>(null);

  async function apply() {
    if (!change) return;
    try {
      await api.put(path`/systems/${change.id}/runtime-mode`, { mode: change.mode });
      toast.success(`${change.name} is now in ${titleCase(change.mode)} mode`);
      invalidate("runtime");
      invalidate("system", change.id);
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {Object.entries(RUNTIME_MODE_META).map(([key, m]) => (
          <Card key={key}>
            <CardBody>
              <p className="text-sm font-semibold">{m.label}</p>
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{m.description}</p>
            </CardBody>
          </Card>
        ))}
      </div>
      <QueryBoundary query={overview} skeleton={<div className="h-48 skeleton" />}>
        {(o) =>
          o.systems.length === 0 ? (
            <EmptyState title="No systems yet" description="Register an AI system first." />
          ) : (
            <DataTable caption="Runtime mode per system">
              <THead>
                <tr>
                  <TH>System</TH>
                  <TH>Mode</TH>
                </tr>
              </THead>
              <tbody>
                {o.systems.map((s) => (
                  <TR key={s.id}>
                    <TD>
                      <Link href={`/dashboard/systems/${s.id}`} className="font-medium hover:text-[var(--color-accent-bright)]">
                        {s.name}
                      </Link>
                    </TD>
                    <TD>
                      {can("runtime:manage") ? (
                        <Select aria-label={`Runtime mode for ${s.name}`} value={s.mode} onChange={(e) => setChange({ id: s.id, name: s.name, mode: e.target.value })} className={cn("h-8 w-40 text-xs")}>
                          {Object.entries(RUNTIME_MODE_META).map(([key, m]) => (
                            <option key={key} value={key}>
                              {m.label}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        <Badge>{titleCase(s.mode)}</Badge>
                      )}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
      <ConfirmDialog
        open={!!change}
        onOpenChange={(o) => !o && setChange(null)}
        tone={change?.mode === "enforce" ? "danger" : "primary"}
        title={`Switch ${change?.name} to ${titleCase(change?.mode)} mode?`}
        confirmLabel="Switch mode"
        description={
          change?.mode === "enforce"
            ? "Published policies will block or hold matching actions before they happen. Make sure the agent calls the synchronous check endpoint and handles “block” and “require_approval” responses."
            : change?.mode === "audit"
              ? "Matching actions will create findings with evidence but will not be stopped."
              : "Events and decisions are recorded only; nothing is blocked and no findings are created."
        }
        onConfirm={apply}
      />
    </div>
  );
}
