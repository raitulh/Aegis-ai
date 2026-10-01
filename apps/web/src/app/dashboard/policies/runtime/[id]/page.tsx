"use client";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Copy, FlaskConical, GitCompare, Play, Power, PowerOff, Rocket, RotateCcw, Save, Trash2, XCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { use, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog } from "@/components/ui/dialog";
import { CodeBlock, KeyValue } from "@/components/ui/display";
import { Field, Input, Select, Textarea } from "@/components/ui/forms";
import { TabPanel, Tabs, TabsList, TabTrigger } from "@/components/ui/overlays";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import { useCan, useInvalidate, useRuntimePolicy, useRuntimePolicyAssignments, useRuntimePolicyVersions, useSystems } from "@/lib/queries";
import type { RuntimePolicy, RuntimePolicyVersion, Simulation } from "@/lib/types";
import { cn, formatDateTime, titleCase } from "@/lib/utils";

type Validation = { valid: boolean; errors: string[]; rules?: number };

export default function PolicyStudioPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const policy = useRuntimePolicy(id);
  const versions = useRuntimePolicyVersions(id);
  return (
    <QueryBoundary query={policy} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(p) => (
        <QueryBoundary query={versions} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
          {(vs) => <Studio key={vs[0]?.id ?? "none"} policy={p} versions={vs} />}
        </QueryBoundary>
      )}
    </QueryBoundary>
  );
}

function Studio({ policy, versions }: { policy: RuntimePolicy; versions: RuntimePolicyVersion[] }) {
  const router = useRouter();
  const can = useCan();
  const invalidate = useInvalidate();
  const latest = versions[0];
  const published = versions.find((v) => v.id === policy.published_version_id) ?? null;
  const [source, setSource] = useState(latest?.source_yaml ?? "");
  const [note, setNote] = useState("");
  const [checked, setValidation] = useState<Validation | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState("versions");
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "publish" | "rollback"; version: number } | null>(null);
  const dirty = source !== (latest?.source_yaml ?? "");
  const validation: Validation | null = source.trim() ? checked : { valid: false, errors: ["Policy source is empty"] };
  const canWrite = can("policies:write");
  const canPublish = can("policies:publish");

  // Live validation (debounced). Validation never records anything.
  useEffect(() => {
    if (!source.trim()) return;
    const t = setTimeout(async () => {
      try {
        setValidation(await api.post<Validation>("/runtime-policies/validate", { source_yaml: source }));
      } catch (e) {
        setValidation({ valid: false, errors: [errorMessage(e)] });
      }
    }, 400);
    return () => clearTimeout(t);
  }, [source]);

  function refresh() {
    invalidate("runtime-policy", policy.id);
    invalidate("runtime-policies");
  }

  async function saveVersion() {
    setSaving(true);
    try {
      const v = await api.post<RuntimePolicyVersion>(path`/runtime-policies/${policy.id}/versions`, { source_yaml: source, change_note: note || undefined });
      toast.success(`Saved version ${v.version} as a draft`);
      setNote("");
      refresh();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function doPublish() {
    if (!confirm) return;
    try {
      await api.post(path`/runtime-policies/${policy.id}/${confirm.kind}`, { version: confirm.version });
      toast.success(confirm.kind === "publish" ? `Version ${confirm.version} published` : `Rolled back to version ${confirm.version}`);
      refresh();
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  async function toggleEnabled() {
    try {
      await api.post(path`/runtime-policies/${policy.id}/${policy.status === "disabled" ? "enable" : "disable"}`);
      refresh();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function clone() {
    try {
      const copy = await api.post<RuntimePolicy>(path`/runtime-policies/${policy.id}/clone`);
      invalidate("runtime-policies");
      router.push(`/dashboard/policies/runtime/${copy.id}`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <div>
      <PageHeader
        title={policy.name}
        description={policy.description ?? undefined}
        breadcrumbs={[{ label: "Policies", href: "/dashboard/policies?tab=runtime" }, { label: policy.key }]}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={policy.status} />
            {published ? <Badge tone="success">Live: v{published.version}</Badge> : <Badge>Not published</Badge>}
            {canWrite ? (
              <Button variant="ghost" size="sm" icon={Copy} onClick={clone}>
                Clone
              </Button>
            ) : null}
            {canPublish && policy.published_version_id ? (
              <Button variant="secondary" size="sm" icon={policy.status === "disabled" ? Power : PowerOff} onClick={toggleEnabled}>
                {policy.status === "disabled" ? "Enable" : "Disable"}
              </Button>
            ) : null}
          </div>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[1.15fr_1fr]">
        <Card className="flex flex-col">
          <CardHeader>
            <CardTitle>Source{latest ? ` · editing from v${latest.version}` : ""}</CardTitle>
            <ValidationBadge validation={validation} />
          </CardHeader>
          <CardBody className="flex flex-1 flex-col gap-3">
            <label htmlFor="policy-source" className="sr-only">
              Policy source (YAML)
            </label>
            <Textarea
              id="policy-source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              readOnly={!canWrite}
              spellCheck={false}
              aria-invalid={validation ? !validation.valid : undefined}
              aria-describedby="policy-errors"
              className="min-h-[420px] flex-1 resize-y font-mono text-xs leading-5"
            />
            <div id="policy-errors" aria-live="polite">
              {validation && !validation.valid ? (
                <ul className="space-y-1 text-xs text-[var(--color-critical)]">
                  {validation.errors.slice(0, 10).map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              ) : null}
            </div>
            {canWrite ? (
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Change note" className="min-w-48 flex-1">
                  {(p) => <Input {...p} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="What changed and why" />}
                </Field>
                <Button icon={Save} onClick={saveVersion} loading={saving} disabled={!dirty || !validation?.valid}>
                  Save as new version
                </Button>
              </div>
            ) : null}
            <p className="text-xs text-[var(--color-text-subtle)]">Versions are immutable once saved. Publishing makes a version live for the systems it is assigned to; rollback re-publishes an earlier version.</p>
          </CardBody>
        </Card>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabTrigger value="versions">Versions</TabTrigger>
            <TabTrigger value="simulate">Simulate</TabTrigger>
            <TabTrigger value="test">Test event</TabTrigger>
            <TabTrigger value="assignments">Assignments</TabTrigger>
          </TabsList>
          <TabPanel value="versions" className="pt-4">
            <VersionsPanel
              policy={policy}
              versions={versions}
              canPublish={canPublish}
              onPublish={(version, kind) => setConfirm({ kind, version })}
            />
          </TabPanel>
          <TabPanel value="simulate" className="pt-4">
            <SimulatePanel source={source} valid={!!validation?.valid} result={simulation} onResult={setSimulation} />
          </TabPanel>
          <TabPanel value="test" className="pt-4">
            {tab === "test" ? <TestPanel source={source} valid={!!validation?.valid} /> : null}
          </TabPanel>
          <TabPanel value="assignments" className="pt-4">
            {tab === "assignments" ? <AssignmentsPanel policy={policy} canManage={canPublish} /> : null}
          </TabPanel>
        </Tabs>
      </div>

      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        tone="primary"
        title={confirm?.kind === "rollback" ? `Roll back to version ${confirm?.version}?` : `Publish version ${confirm?.version}?`}
        confirmLabel={confirm?.kind === "rollback" ? "Roll back" : "Publish"}
        description={
          <>
            Version {confirm?.version} becomes live for every system this policy is assigned to. Systems in <strong>enforce</strong> mode will block or hold actions it matches; <strong>observe</strong> and <strong>audit</strong> systems only record.
          </>
        }
        onConfirm={doPublish}
      >
        {simulation ? (
          <p className="rounded-[var(--radius)] border border-[var(--color-border)] p-2 text-xs">
            Last simulation of the editor source over {simulation.window_days} days: {simulation.blocked} blocked, {simulation.require_approval} needing approval, {simulation.flagged} flagged of {simulation.events_evaluated} events.
          </p>
        ) : (
          <p className="text-xs">Tip: run a simulation first to see the impact on recorded traffic.</p>
        )}
      </ConfirmDialog>
    </div>
  );
}

function ValidationBadge({ validation }: { validation: Validation | null }) {
  if (!validation) return <span className="text-xs text-[var(--color-text-subtle)]">Validating…</span>;
  return validation.valid ? (
    <span className="inline-flex items-center gap-1 text-xs text-[var(--color-success)]">
      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Valid · {validation.rules} rule{validation.rules === 1 ? "" : "s"}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-xs text-[var(--color-critical)]">
      <XCircle className="h-3.5 w-3.5" aria-hidden /> {validation.errors.length} problem{validation.errors.length === 1 ? "" : "s"}
    </span>
  );
}

function VersionsPanel({
  policy,
  versions,
  canPublish,
  onPublish,
}: {
  policy: RuntimePolicy;
  versions: RuntimePolicyVersion[];
  canPublish: boolean;
  onPublish: (version: number, kind: "publish" | "rollback") => void;
}) {
  const [from, setFrom] = useState(versions[1]?.version ?? versions[0]?.version ?? 1);
  const [to, setTo] = useState(versions[0]?.version ?? 1);
  const live = versions.find((v) => v.id === policy.published_version_id);
  const diff = useQuery({
    queryKey: ["runtime-policy", policy.id, "diff", from, to],
    queryFn: () => api.get<{ unified: string; rules: { added: string[]; removed: string[]; changed: string[] } }>(path`/runtime-policies/${policy.id}/diff`, { from, to }),
    enabled: versions.length > 1 && from !== to,
  });

  return (
    <div className="space-y-4">
      <ol className="space-y-2" aria-label="Versions">
        {versions.map((v) => {
          const isLive = v.id === policy.published_version_id;
          const kind = live && v.version < live.version ? "rollback" : "publish";
          return (
            <li key={v.id} className={cn("rounded-[var(--radius)] border p-3", isLive ? "border-[color-mix(in_srgb,var(--color-success)_45%,transparent)]" : "border-[var(--color-border)]")}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold">v{v.version}</span>
                <StatusBadge status={isLive ? "published" : v.status} />
                <span className="text-xs text-[var(--color-text-subtle)]">{v.compiled.rules.length} rules · {formatDateTime(v.created_at)}</span>
                {canPublish && !isLive ? (
                  <Button size="sm" variant={kind === "rollback" ? "secondary" : "primary"} icon={kind === "rollback" ? RotateCcw : Rocket} className="ml-auto" onClick={() => onPublish(v.version, kind)}>
                    {kind === "rollback" ? "Roll back" : "Publish"}
                  </Button>
                ) : null}
              </div>
              {v.change_note ? <p className="mt-1 text-xs text-[var(--color-text-muted)]">{v.change_note}</p> : null}
              {v.published_at ? <p className="mt-0.5 text-xs text-[var(--color-text-subtle)]">Published {formatDateTime(v.published_at)}</p> : null}
            </li>
          );
        })}
      </ol>
      {versions.length > 1 ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <GitCompare className="h-4 w-4" aria-hidden /> Compare versions
            </CardTitle>
            <div className="flex items-center gap-1.5 text-xs">
              <Select aria-label="From version" className="h-8 w-20 text-xs" value={from} onChange={(e) => setFrom(Number(e.target.value))}>
                {versions.map((v) => (
                  <option key={v.id} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </Select>
              →
              <Select aria-label="To version" className="h-8 w-20 text-xs" value={to} onChange={(e) => setTo(Number(e.target.value))}>
                {versions.map((v) => (
                  <option key={v.id} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </Select>
            </div>
          </CardHeader>
          <CardBody>
            {from === to ? (
              <p className="text-sm text-[var(--color-text-subtle)]">Pick two different versions.</p>
            ) : (
              <QueryBoundary query={diff} skeleton={<div className="h-32 skeleton" />}>
                {(d) => (
                  <div className="space-y-3">
                    <p className="text-xs text-[var(--color-text-muted)]">
                      Rules added: {d.rules.added.join(", ") || "none"} · removed: {d.rules.removed.join(", ") || "none"} · changed: {d.rules.changed.join(", ") || "none"}
                    </p>
                    <pre className="max-h-80 overflow-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] p-3 font-mono text-[11px] leading-5">
                      {d.unified
                        ? d.unified.split("\n").map((line, i) => (
                            <div key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "text-[var(--color-success)]" : line.startsWith("-") && !line.startsWith("---") ? "text-[var(--color-critical)]" : "text-[var(--color-text-muted)]"}>
                              {line || " "}
                            </div>
                          ))
                        : "No textual differences."}
                    </pre>
                  </div>
                )}
              </QueryBoundary>
            )}
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}

function SimulatePanel({ source, valid, result, onResult }: { source: string; valid: boolean; result: Simulation | null; onResult: (s: Simulation) => void }) {
  const systems = useSystems({ page_size: 100 });
  const [days, setDays] = useState(7);
  const [systemId, setSystemId] = useState("");
  const [busy, setBusy] = useState(false);
  const names = useMemo(() => new Map((systems.data?.items ?? []).map((s) => [s.id, s.name])), [systems.data]);

  async function run() {
    setBusy(true);
    try {
      onResult(await api.post<Simulation>("/runtime-policies/simulate", { source_yaml: source, days, system_ids: systemId ? [systemId] : [] }));
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-muted)]">Replays recorded runtime events through the editor source. Nothing is enforced or written; every number below is counted from your real events.</p>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Window">
          {(p) => (
            <Select {...p} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-32">
              {[1, 7, 14, 30, 90].map((d) => (
                <option key={d} value={d}>
                  Last {d} day{d === 1 ? "" : "s"}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Systems">
          {(p) => (
            <Select {...p} value={systemId} onChange={(e) => setSystemId(e.target.value)} className="w-48">
              <option value="">All systems</option>
              {(systems.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Button icon={Play} onClick={run} loading={busy} disabled={!valid}>
          Run simulation
        </Button>
      </div>
      {result ? (
        result.events_available === 0 ? (
          <EmptyState icon={FlaskConical} title="No recorded runtime events in this window" description="Send events with the SDK, MCP server or HTTP API, then simulate again." />
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(
                [
                  ["Allowed", result.allowed, "var(--color-success)"],
                  ["Flagged", result.flagged, "var(--color-medium)"],
                  ["Needs approval", result.require_approval, "var(--color-high)"],
                  ["Blocked", result.blocked, "var(--color-critical)"],
                ] as const
              ).map(([label, value, color]) => (
                <div key={label} className="rounded-[var(--radius)] border border-[var(--color-border)] p-3">
                  <p className="text-xs text-[var(--color-text-muted)]">{label}</p>
                  <p className="font-mono text-xl font-semibold" style={{ color: value ? color : undefined }}>
                    {value.toLocaleString()}
                  </p>
                </div>
              ))}
            </div>
            <KeyValue
              items={[
                ["Events evaluated", `${result.events_evaluated.toLocaleString()} of ${result.events_available.toLocaleString()}${result.truncated ? " (sample limit reached)" : ""}`],
                ["Workflows needing approval", result.workflows_requiring_approval],
                ["Workflows blocked", result.workflows_blocked],
                ["Decisions different from recorded", result.decisions_changed_vs_recorded],
              ]}
            />
            {Object.keys(result.by_rule).length ? (
              <div>
                <p className="mb-1 text-xs font-medium text-[var(--color-text-muted)]">Matches by rule</p>
                <ul className="space-y-1 text-xs">
                  {Object.entries(result.by_rule).map(([rule, n]) => (
                    <li key={rule} className="flex justify-between">
                      <span className="font-mono">{rule}</span>
                      <span className="font-mono">{n}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {result.samples.length ? (
              <div>
                <p className="mb-1 text-xs font-medium text-[var(--color-text-muted)]">Sample affected events</p>
                <ul className="max-h-56 space-y-1 overflow-y-auto text-xs">
                  {result.samples.slice(0, 25).map((s) => (
                    <li key={s.event_id} className="flex flex-wrap items-center gap-2 rounded border border-[var(--color-border)] px-2 py-1">
                      <StatusBadge status={s.decision} />
                      <span className="font-mono">{s.event_type}</span>
                      {s.tool ? <span className="font-mono text-[var(--color-text-muted)]">{s.tool}</span> : null}
                      <span className="text-[var(--color-text-subtle)]">{names.get(s.system_id) ?? s.system_id.slice(0, 8)}</span>
                      <span className="ml-auto text-[var(--color-text-subtle)]">{formatDateTime(s.occurred_at)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )
      ) : null}
    </div>
  );
}

const SAMPLE_EVENT = JSON.stringify(
  {
    event_type: "tool.call",
    agent: "support-agent",
    tool: "send_email",
    payload: { destination: "external", data_classification: "confidential", arguments: { to: "someone@example.org" } },
  },
  null,
  2,
);

function TestPanel({ source, valid }: { source: string; valid: boolean }) {
  const systems = useSystems({ page_size: 1 });
  const [event, setEvent] = useState(SAMPLE_EVENT);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(event) as Record<string, unknown>;
      const systemId = (parsed.system_id as string) || systems.data?.items[0]?.id || "00000000-0000-0000-0000-000000000000";
      setResult(await api.post<Record<string, unknown>>("/runtime-policies/test", { source_yaml: source, event: { ...parsed, system_id: systemId } }));
    } catch (e) {
      setResult(null);
      setError(e instanceof SyntaxError ? "The event is not valid JSON." : errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">Evaluate the editor source against one sample event. Nothing is recorded.</p>
      <label htmlFor="test-event" className="sr-only">
        Sample event (JSON)
      </label>
      <Textarea id="test-event" value={event} onChange={(e) => setEvent(e.target.value)} rows={12} spellCheck={false} className="font-mono text-xs" />
      <Button icon={Play} onClick={run} loading={busy} disabled={!valid}>
        Evaluate
      </Button>
      {error ? (
        <p role="alert" className="text-sm text-[var(--color-critical)]">
          {error}
        </p>
      ) : null}
      {result ? (
        <div className="space-y-2" aria-live="polite">
          <div className="flex items-center gap-2 text-sm">
            Decision: <StatusBadge status={String(result.decision)} />
          </div>
          <CodeBlock language="result" code={JSON.stringify({ matches: result.matches, risk_level: result.risk_level, reason: result.reason, signals: result.signals }, null, 2)} />
        </div>
      ) : null}
    </div>
  );
}

function AssignmentsPanel({ policy, canManage }: { policy: RuntimePolicy; canManage: boolean }) {
  const query = useRuntimePolicyAssignments(policy.id);
  const systems = useSystems({ page_size: 100 });
  const invalidate = useInvalidate();
  const [scope, setScope] = useState<"organization" | "environment" | "system">("system");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const names = useMemo(() => new Map((systems.data?.items ?? []).map((s) => [s.id, s.name])), [systems.data]);

  async function add() {
    setBusy(true);
    try {
      await api.post(path`/runtime-policies/${policy.id}/assignments`, {
        scope_type: scope,
        system_id: scope === "system" ? target : undefined,
        environment: scope === "environment" ? target : undefined,
      });
      setTarget("");
      invalidate("runtime-policy", policy.id, "assignments");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    try {
      await api.delete(path`/runtime-policies/${policy.id}/assignments/${removing}`);
      invalidate("runtime-policy", policy.id, "assignments");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-muted)]">The published version applies to every system matched by an assignment. Each system&apos;s runtime mode decides whether matches are recorded or enforced.</p>
      <QueryBoundary query={query} skeleton={<div className="h-24 skeleton" />}>
        {(items) =>
          items.length === 0 ? (
            <p className="rounded-[var(--radius)] border border-dashed border-[var(--color-border-strong)] p-4 text-sm text-[var(--color-text-subtle)]">Not assigned — this policy is not evaluated for any system yet.</p>
          ) : (
            <ul className="space-y-2">
              {items.map((a) => (
                <li key={a.id} className="flex items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] px-3 py-2 text-sm">
                  <Badge>{titleCase(a.scope_type)}</Badge>
                  <span>{a.scope_type === "system" ? (names.get(a.system_id ?? "") ?? a.scope_key) : a.scope_type === "organization" ? "Whole workspace" : titleCase(a.scope_key)}</span>
                  {canManage ? (
                    <button type="button" aria-label="Remove assignment" onClick={() => setRemoving(a.id)} className="ml-auto rounded-md p-1 text-[var(--color-text-subtle)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-critical)] focus-ring">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )
        }
      </QueryBoundary>
      {canManage ? (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Scope">
            {(p) => (
              <Select
                {...p}
                value={scope}
                onChange={(e) => {
                  setScope(e.target.value as typeof scope);
                  setTarget("");
                }}
                className="w-40"
              >
                <option value="system">System</option>
                <option value="environment">Environment</option>
                <option value="organization">Whole workspace</option>
              </Select>
            )}
          </Field>
          {scope === "system" ? (
            <Field label="System">
              {(p) => (
                <Select {...p} value={target} onChange={(e) => setTarget(e.target.value)} className="w-52">
                  <option value="">Choose…</option>
                  {(systems.data?.items ?? []).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          ) : scope === "environment" ? (
            <Field label="Environment">
              {(p) => (
                <Select {...p} value={target} onChange={(e) => setTarget(e.target.value)} className="w-40">
                  <option value="">Choose…</option>
                  {["development", "staging", "production"].map((env) => (
                    <option key={env} value={env}>
                      {titleCase(env)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          ) : null}
          <Button onClick={add} loading={busy} disabled={scope !== "organization" && !target}>
            Assign
          </Button>
        </div>
      ) : null}
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Remove this assignment?"
        description="The policy stops being evaluated for the systems in this scope immediately. Recorded events and evidence are kept."
        confirmLabel="Remove"
        onConfirm={remove}
      />
    </div>
  );
}
