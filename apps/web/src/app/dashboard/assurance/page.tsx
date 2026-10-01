"use client";
import { CalendarClock, GitPullRequest, Plus, RefreshCcw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { CodeBlock, PlanLimitNotice, SectionTitle } from "@/components/ui/display";
import { Field, Input, Select, Switch } from "@/components/ui/forms";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import { useCan, useInvalidate, useSchedules, useSystems, useTriggers } from "@/lib/queries";
import type { Schedule } from "@/lib/types";
import { formatDateTime, timeAgo, titleCase } from "@/lib/utils";

const CHANGE_EVENTS = ["deployment", "pull_request", "model_change", "prompt_change", "policy_change", "tool_change", "agent_version", "config_change"];
const INTERVALS = [
  { value: 0, label: "Only on changes" },
  { value: 24, label: "Daily" },
  { value: 24 * 7, label: "Weekly" },
  { value: 24 * 30, label: "Every 30 days" },
];

export default function AssurancePage() {
  const can = useCan();
  const invalidate = useInvalidate();
  const systems = useSystems({ page_size: 100 });
  const schedules = useSchedules();
  const triggers = useTriggers({ page_size: 25 });
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Schedule | null>(null);
  const names = useMemo(() => new Map((systems.data?.items ?? []).map((s) => [s.id, s.name])), [systems.data]);
  const canManage = can("assurance:manage");

  async function toggle(s: Schedule, enabled: boolean) {
    try {
      await api.patch(path`/assurance/schedules/${s.id}`, { enabled });
      invalidate("assurance");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function remove() {
    if (!deleting) return;
    try {
      await api.delete(path`/assurance/schedules/${deleting.id}`);
      invalidate("assurance");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div>
      <PageHeader
        title="Continuous assurance"
        description="Re-test systems on a schedule and whenever they change. Each run is compared with the previous audit and the known-good baseline to catch regressions."
        actions={
          canManage ? (
            <Button icon={Plus} onClick={() => setCreating(true)}>
              New schedule
            </Button>
          ) : null
        }
      />

      <SectionTitle>Schedules</SectionTitle>
      <QueryBoundary query={schedules} skeleton={<div className="h-40 skeleton rounded-[var(--radius-lg)]" />}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState
              icon={CalendarClock}
              title="No schedules yet"
              description="Create a schedule per system: run on an interval, on specific change events, or both. Runs respect your plan's audit quota."
              action={
                canManage ? (
                  <Button size="sm" icon={Plus} onClick={() => setCreating(true)}>
                    New schedule
                  </Button>
                ) : null
              }
            />
          ) : (
            <DataTable caption="Assurance schedules">
              <THead>
                <tr>
                  <TH>Schedule</TH>
                  <TH>System</TH>
                  <TH>Runs</TH>
                  <TH>Next run</TH>
                  <TH>Last run</TH>
                  <TH>Enabled</TH>
                  <TH />
                </tr>
              </THead>
              <tbody>
                {rows.map((s) => (
                  <TR key={s.id}>
                    <TD>
                      <p className="font-medium">{s.name}</p>
                      <p className="text-xs text-[var(--color-text-subtle)]">
                        {titleCase(s.intensity)} · {s.categories.length ? s.categories.map(titleCase).join(", ") : "risk-selected categories"}
                      </p>
                    </TD>
                    <TD className="text-[var(--color-text-muted)]">{names.get(s.system_id) ?? "—"}</TD>
                    <TD className="text-xs text-[var(--color-text-muted)]">
                      {s.interval_hours ? `Every ${s.interval_hours % 24 === 0 ? `${s.interval_hours / 24} day${s.interval_hours === 24 ? "" : "s"}` : `${s.interval_hours}h`}` : "No interval"}
                      {s.trigger_on.length ? <div>On: {s.trigger_on.map(titleCase).join(", ")}</div> : null}
                    </TD>
                    <TD className="text-xs text-[var(--color-text-muted)]">{s.enabled && s.next_run_at ? formatDateTime(s.next_run_at) : "—"}</TD>
                    <TD className="text-xs">
                      {s.last_audit_id ? (
                        <Link href={`/dashboard/audits/${s.last_audit_id}`} className="text-[var(--color-accent-bright)] hover:underline">
                          {timeAgo(s.last_run_at)}
                        </Link>
                      ) : (
                        "Never"
                      )}
                    </TD>
                    <TD>
                      <Switch checked={s.enabled} onChange={(v) => toggle(s, v)} label={`Enable ${s.name}`} disabled={!canManage} />
                    </TD>
                    <TD>
                      {canManage ? (
                        <button type="button" aria-label={`Delete ${s.name}`} onClick={() => setDeleting(s)} className="rounded-md p-1 text-[var(--color-text-subtle)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-critical)] focus-ring">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>

      <div className="mt-8 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div>
          <SectionTitle>Recent change triggers</SectionTitle>
          <QueryBoundary query={triggers} skeleton={<div className="h-40 skeleton rounded-[var(--radius-lg)]" />}>
            {(page) =>
              page.items.length === 0 ? (
                <EmptyState icon={GitPullRequest} title="No changes reported yet" description="Report deployments, prompt or model changes from CI/CD. Each one starts a risk-selected audit." />
              ) : (
                <DataTable caption="Change triggers">
                  <THead>
                    <tr>
                      <TH>Change</TH>
                      <TH>System</TH>
                      <TH>Selected tests</TH>
                      <TH>Audit</TH>
                      <TH>When</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {page.items.map((t) => (
                      <TR key={t.id}>
                        <TD>
                          <p className="font-medium">{titleCase(t.event_type)}</p>
                          <p className="max-w-48 truncate font-mono text-xs text-[var(--color-text-subtle)]">
                            {t.ref} · {t.source}
                          </p>
                        </TD>
                        <TD className="text-[var(--color-text-muted)]">{names.get(t.system_id) ?? "—"}</TD>
                        <TD className="max-w-64 text-xs text-[var(--color-text-muted)]" title={t.selection_reason ?? undefined}>
                          {t.categories.map(titleCase).join(", ") || "—"}
                        </TD>
                        <TD className="text-xs">
                          {t.audit_id ? (
                            <Link href={`/dashboard/audits/${t.audit_id}`} className="text-[var(--color-accent-bright)] hover:underline">
                              Open
                            </Link>
                          ) : (
                            <Badge>Not started</Badge>
                          )}
                        </TD>
                        <TD className="text-[var(--color-text-subtle)]">{timeAgo(t.created_at)}</TD>
                      </TR>
                    ))}
                  </tbody>
                </DataTable>
              )
            }
          </QueryBoundary>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <RefreshCcw className="h-4 w-4" aria-hidden /> Report a change from CI/CD
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-3 text-sm text-[var(--color-text-muted)]">
            <p>Call this after a deploy with an API key that has the developer role. Re-sending the same ref is idempotent.</p>
            <CodeBlock
              language="shell"
              code={`curl -X POST "$AEGIS_BASE_URL/api/v1/assurance/triggers" \\
  -H "Authorization: Bearer $AEGIS_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"system_id":"<system-id>","event_type":"deployment","ref":"'$GIT_SHA'"}'`}
            />
            <CodeBlock
              language="python"
              code={`import os
from aegis_ai import Aegis

aegis = Aegis(os.environ["AEGIS_API_KEY"], base_url=os.environ["AEGIS_BASE_URL"])
aegis.assurance.trigger(system_id=SYSTEM_ID, event_type="prompt_change", ref=git_sha)`}
            />
            <p className="text-xs">
              Event types: {CHANGE_EVENTS.join(", ")}. Set a known-good baseline from any completed audit to compare against it.
            </p>
          </CardBody>
        </Card>
      </div>

      <NewScheduleDialog open={creating} onClose={() => setCreating(false)} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name}”?`}
        description="No further runs will be scheduled. Audits it already ran, and their evidence, are kept."
        confirmLabel="Delete schedule"
        onConfirm={remove}
      />
    </div>
  );
}

function NewScheduleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const systems = useSystems({ page_size: 100 });
  const invalidate = useInvalidate();
  const [systemId, setSystemId] = useState("");
  const [name, setName] = useState("");
  const [interval, setInterval] = useState(24);
  const [intensity, setIntensity] = useState("quick");
  const [events, setEvents] = useState<string[]>(["deployment"]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/assurance/schedules", { system_id: systemId, name: name || undefined, interval_hours: interval || undefined, trigger_on: events, intensity });
      invalidate("assurance");
      toast.success("Schedule created");
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="New assurance schedule" description="Runs count toward your plan's audit quota. Change-triggered runs pick test categories from the type of change.">
      <form onSubmit={submit} className="space-y-4">
        <Field label="System">
          {(p) => (
            <Select {...p} required value={systemId} onChange={(e) => setSystemId(e.target.value)}>
              <option value="">Choose a system…</option>
              {(systems.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Name (optional)">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />}</Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Interval">
            {(p) => (
              <Select {...p} value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
                {INTERVALS.map((i) => (
                  <option key={i.value} value={i.value}>
                    {i.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Intensity">
            {(p) => (
              <Select {...p} value={intensity} onChange={(e) => setIntensity(e.target.value)}>
                {["quick", "standard", "deep"].map((i) => (
                  <option key={i} value={i}>
                    {titleCase(i)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-xs font-medium text-[var(--color-text-muted)]">Also run when these changes are reported</legend>
          <div className="grid grid-cols-2 gap-1.5">
            {CHANGE_EVENTS.map((ev) => (
              <label key={ev} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="accent-[var(--color-accent)]"
                  checked={events.includes(ev)}
                  onChange={(e) => setEvents((prev) => (e.target.checked ? [...prev, ev] : prev.filter((x) => x !== ev)))}
                />
                {titleCase(ev)}
              </label>
            ))}
          </div>
        </fieldset>
        {error ? <PlanLimitNotice error={error} /> : null}
        {error && !(error as { isPlanLimit?: boolean }).isPlanLimit ? (
          <p role="alert" className="text-sm text-[var(--color-critical)]">
            {errorMessage(error)}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={!systemId || (!interval && !events.length)}>
            Create schedule
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
