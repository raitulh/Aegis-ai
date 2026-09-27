"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Sheet, SheetContent } from "@/components/ui/overlays";
import { Button } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useInvalidate, useProviders } from "@/lib/queries";
import type { System } from "@/lib/types";

const DEMO_PROFILES = [
  { value: "hiring_agent", label: "Hiring Agent" },
  { value: "support_rag", label: "Support RAG" },
  { value: "research_agent", label: "Research Agent" },
];

export function NewSystemDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const invalidate = useInvalidate();
  const { data: providers } = useProviders();
  const [loading, setLoading] = useState(false);
  const [useDemo, setUseDemo] = useState(true);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    const f = new FormData(e.currentTarget);
    try {
      let providerId = f.get("provider_id") as string | null;
      const demoProfile = f.get("demo_profile") as string;
      if (useDemo) {
        // ensure a demo provider exists
        const demoProvider = providers?.find((p) => p.kind === "demo");
        providerId = demoProvider?.id ?? (await api.post<{ id: string }>("/providers", { kind: "demo", name: "Demo simulators", settings: { profile: demoProfile } })).id;
      }
      const config: Record<string, unknown> = useDemo
        ? {
            demo_profile: demoProfile,
            guardrails: {},
            tools:
              demoProfile === "hiring_agent"
                ? { send_rejection_email: { requires_human_approval: true }, update_candidate_status: { requires_human_approval: true } }
                : demoProfile === "research_agent"
                  ? { send_email: { requires_human_approval: true, allowed_domains: ["aegis-demo.example"] }, read_file: { path_allowlist: ["/workspace/reports/"] } }
                  : {},
          }
        : {};
      const body = {
        name: f.get("name"),
        description: f.get("description") || null,
        system_type: useDemo ? (demoProfile === "support_rag" ? "rag" : "agent") : f.get("system_type"),
        environment: f.get("environment"),
        risk_tier: f.get("risk_tier"),
        business_purpose: f.get("business_purpose") || null,
        provider_id: providerId,
        model_name: useDemo ? `${demoProfile}-sim` : f.get("model_name"),
        endpoint_url: !useDemo ? f.get("endpoint_url") || null : null,
        config,
      };
      const system = await api.post<System>("/systems", body);
      invalidate("systems");
      onOpenChange(false);
      router.push(`/dashboard/systems/${system.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create system");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent title="Register AI System" description="Add a model or agent to assure. Use a built-in simulated system to explore without credentials.">
        <form onSubmit={submit} className="space-y-4 p-6">
          <div className="flex gap-2 rounded-[var(--radius)] border border-[var(--color-border)] p-1">
            <button type="button" onClick={() => setUseDemo(true)} className={`flex-1 rounded-[var(--radius-sm)] px-3 py-1.5 text-sm ${useDemo ? "bg-[var(--color-surface-2)] text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
              Simulated
            </button>
            <button type="button" onClick={() => setUseDemo(false)} className={`flex-1 rounded-[var(--radius-sm)] px-3 py-1.5 text-sm ${!useDemo ? "bg-[var(--color-surface-2)] text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
              Provider / endpoint
            </button>
          </div>
          <Field name="name" label="Name" placeholder="Hiring-Agent" required />
          <Field name="description" label="Description" placeholder="Candidate screening assistant" />
          {useDemo ? (
            <Select name="demo_profile" label="Simulated system" options={DEMO_PROFILES} />
          ) : (
            <>
              <Select name="system_type" label="Type" options={[["llm", "LLM"], ["rag", "RAG"], ["agent", "Agent"], ["ml_model", "ML Model"], ["multi_agent", "Multi-Agent"]].map(([value, label]) => ({ value, label }))} />
              <Select name="provider_id" label="Provider" options={(providers ?? []).map((p) => ({ value: p.id, label: `${p.name} (${p.kind})` }))} allowEmpty />
              <Field name="model_name" label="Model" placeholder="gpt-4.1-mini / qwen3:1.7b" />
              <Field name="endpoint_url" label="HTTP endpoint (optional)" placeholder="https://api.mycompany.com/agent" />
            </>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Select name="environment" label="Environment" options={[["production", "Production"], ["staging", "Staging"], ["development", "Development"]].map(([value, label]) => ({ value, label }))} />
            <Select name="risk_tier" label="Risk tier" defaultValue="high" options={[["critical", "Critical"], ["high", "High"], ["limited", "Limited"], ["minimal", "Minimal"]].map(([value, label]) => ({ value, label }))} />
          </div>
          <Field name="business_purpose" label="Business purpose" placeholder="Screens applicants for the Senior Data Analyst role" />
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={loading}>
              Create system
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function Field({ name, label, ...props }: { name: string; label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">{label}</span>
      <input name={name} {...props} className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)] focus-ring placeholder:text-[var(--color-text-subtle)]" />
    </label>
  );
}
function Select({ name, label, options, defaultValue, allowEmpty }: { name: string; label: string; options: { value: string; label: string }[]; defaultValue?: string; allowEmpty?: boolean }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">{label}</span>
      <select name={name} defaultValue={defaultValue} className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)] focus-ring">
        {allowEmpty ? <option value="">None</option> : null}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
