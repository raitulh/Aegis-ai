"use client";
import { Check, Minus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { KeyValue, PlanLimitNotice } from "@/components/ui/display";
import { Field, Input } from "@/components/ui/forms";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { useCan, useInvalidate, useOrganization, usePlans, useSession } from "@/lib/queries";
import type { OrganizationInfo } from "@/lib/types";
import { formatDateTime, titleCase } from "@/lib/utils";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];

export default function SettingsPage() {
  const org = useOrganization();
  return (
    <div className="max-w-3xl">
      <PageHeader title="Settings" description="Workspace configuration and data governance. Changes are recorded in the audit log." />
      <QueryBoundary query={org} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
        {(o) => <SettingsForm key={`${o.name}:${o.retention.runtime_events_days}`} org={o} />}
      </QueryBoundary>
    </div>
  );
}

function SettingsForm({ org }: { org: OrganizationInfo }) {
  const can = useCan();
  const { data: session } = useSession();
  const plans = usePlans();
  const invalidate = useInvalidate();
  const manage = can("org:manage");
  const [name, setName] = useState(org.name);
  const [retention, setRetention] = useState(String(org.retention.runtime_events_days));
  const [sla, setSla] = useState<Record<string, string>>(Object.fromEntries(SEVERITIES.map((s) => [s, String(org.finding_sla_days[s] ?? "")])));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function save(section: string, body: Record<string, unknown>) {
    setBusy(section);
    setError(null);
    try {
      await api.patch("/organization", body);
      invalidate("organization");
      invalidate("session");
      toast.success("Settings saved");
    } catch (e) {
      setError(e);
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const featureLabels = plans.data?.features ?? {};

  return (
    <div className="space-y-4">
      {error ? <PlanLimitNotice error={error} /> : null}
      <Card>
        <CardHeader>
          <CardTitle>Workspace</CardTitle>
          {org.is_demo || org.is_sandbox ? <Badge tone="warning">DEMO</Badge> : null}
        </CardHeader>
        <CardBody className="space-y-4">
          <KeyValue
            items={[
              ["Plan", <Link key="p" href="/dashboard/billing" className="text-[var(--color-accent-bright)] hover:underline">{titleCase(org.plan)}</Link>],
              ["Your role", titleCase(session?.role)],
              ["Workspace id", <span key="id" className="font-mono text-xs">{org.id}</span>],
              ...(org.is_sandbox && org.expires_at ? ([["Sandbox expires", formatDateTime(org.expires_at)]] as [string, React.ReactNode][]) : []),
            ]}
          />
          {manage ? (
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void save("name", { name });
              }}
            >
              <Field label="Workspace name" className="flex-1">
                {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} minLength={1} maxLength={120} required />}
              </Field>
              <Button type="submit" variant="secondary" loading={busy === "name"} disabled={name === org.name}>
                Save
              </Button>
            </form>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Data retention</CardTitle>
        </CardHeader>
        <CardBody className="space-y-3 text-sm">
          <KeyValue
            items={[
              ["Runtime events", `${org.retention.runtime_events_days} days`],
              ["Evidence", titleCase(org.retention.evidence)],
            ]}
          />
          {manage && org.retention.custom_allowed ? (
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void save("retention", { runtime_events_retention_days: Number(retention) });
              }}
            >
              <Field label="Runtime event retention (days)" hint="1–3650. Events older than this are purged by the maintenance job; decisions already recorded as evidence are kept." className="flex-1">
                {(p) => <Input {...p} type="number" min={1} max={3650} value={retention} onChange={(e) => setRetention(e.target.value)} />}
              </Field>
              <Button type="submit" variant="secondary" loading={busy === "retention"}>
                Save
              </Button>
            </form>
          ) : (
            <p className="text-xs text-[var(--color-text-subtle)]">{org.retention.custom_allowed ? "Only workspace admins can change retention." : "Custom retention is available on plans that include it. The period above comes from your plan."}</p>
          )}
          <p className="text-xs text-[var(--color-text-subtle)]">Evidence is append-only and is never deleted by retention. Sensitive evidence content is masked by default; revealing it requires a permission and is audit-logged.</p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Finding SLAs</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="mb-3 text-sm text-[var(--color-text-muted)]">Days from detection until a finding should be resolved. New findings get a due date from these values; overdue findings are highlighted.</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const parsed = Object.fromEntries(Object.entries(sla).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)]));
              void save("sla", { finding_sla_days: parsed });
            }}
            className="space-y-3"
          >
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {SEVERITIES.map((s) => (
                <Field key={s} label={titleCase(s)}>
                  {(p) => <Input {...p} type="number" min={1} max={3650} value={sla[s]} disabled={!manage} onChange={(e) => setSla((prev) => ({ ...prev, [s]: e.target.value }))} />}
                </Field>
              ))}
            </div>
            {manage ? (
              <div className="flex justify-end">
                <Button type="submit" variant="secondary" loading={busy === "sla"}>
                  Save SLAs
                </Button>
              </div>
            ) : null}
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Plan features</CardTitle>
        </CardHeader>
        <CardBody>
          <ul className="space-y-1.5 text-sm">
            {Object.entries(org.features).map(([key, on]) => {
              const roadmap = plans.data?.roadmap_features.includes(key);
              return (
                <li key={key} className="flex items-center justify-between gap-3">
                  <span className="text-[var(--color-text-muted)]">{featureLabels[key] ?? titleCase(key)}</span>
                  {roadmap ? (
                    <Badge>Roadmap</Badge>
                  ) : on ? (
                    <span className="inline-flex items-center gap-1 text-xs text-[var(--color-success)]">
                      <Check className="h-3.5 w-3.5" aria-hidden /> Included
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-subtle)]">
                      <Minus className="h-3.5 w-3.5" aria-hidden /> Not in plan
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
