"use client";
import { PageHeader } from "@/components/dashboard/page-header";
import { Card, CardBody, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { useSession } from "@/lib/queries";
import { titleCase } from "@/lib/utils";

export default function SettingsPage() {
  const { data: session } = useSession();
  return (
    <div className="max-w-2xl">
      <PageHeader title="Settings" description="Workspace configuration and data governance." />
      <div className="space-y-4">
        <Card>
          <CardHeader><CardTitle>Workspace</CardTitle></CardHeader>
          <CardBody className="space-y-2 text-sm">
            <Row label="Name" value={session?.organization.name ?? "—"} />
            <Row label="Plan" value={titleCase(session?.organization.plan ?? "free")} />
            <Row label="Your role" value={titleCase(session?.role ?? "")} />
            {session?.organization.is_demo ? <div className="pt-1"><Badge tone="accent">Simulated workspace — data is simulated</Badge></div> : null}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>Data Retention</CardTitle></CardHeader>
          <CardBody>
            <label className="block max-w-xs"><span className="mb-1 block text-xs text-[var(--color-text-muted)]">Evidence retention</span>
              <select className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none"><option>30 days</option><option>90 days</option><option>1 year</option><option>Indefinite</option></select>
            </label>
            <p className="mt-2 text-xs text-[var(--color-text-subtle)]">Active investigation evidence is never deleted automatically. Evidence is masked by default and revealing it requires a permission and is audit-logged.</p>
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>Feature Flags</CardTitle></CardHeader>
          <CardBody className="space-y-2">
            {["adaptive_redteam", "agent_audit", "continuous_monitoring", "external_verification", "enterprise_controls"].map((f) => (
              <div key={f} className="flex items-center justify-between text-sm"><span className="text-[var(--color-text-muted)]">{titleCase(f)}</span><Badge tone={["adaptive_redteam", "agent_audit", "continuous_monitoring"].includes(f) ? "success" : "neutral"}>{["adaptive_redteam", "agent_audit", "continuous_monitoring"].includes(f) ? "Enabled" : "Off"}</Badge></div>
            ))}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between border-b border-[var(--color-border)]/50 pb-1.5"><span className="text-[var(--color-text-muted)]">{label}</span><span className="font-medium">{value}</span></div>;
}
