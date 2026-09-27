"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Rocket } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button, Card, CardBody } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { usePolicies, useSystems } from "@/lib/queries";
import { cn, titleCase } from "@/lib/utils";
import type { Audit } from "@/lib/types";

const CATEGORIES = [
  { key: "fairness", label: "Fairness", desc: "Counterfactual treatment tests" },
  { key: "hallucination", label: "Hallucination", desc: "Claim verification" },
  { key: "groundedness", label: "Groundedness", desc: "Source-grounded answers" },
  { key: "safety", label: "Safety", desc: "Refusal & harm probes" },
  { key: "privacy", label: "Privacy", desc: "PII & secret leakage" },
  { key: "prompt_injection", label: "Prompt Injection", desc: "Requires imported corpus" },
  { key: "jailbreak", label: "Jailbreak", desc: "Requires imported corpus" },
  { key: "policy", label: "Policy", desc: "Policy control coverage" },
  { key: "agent_action", label: "Agent Actions", desc: "Tool authorization & oversight" },
  { key: "tool_abuse", label: "Tool Abuse", desc: "Requires imported corpus" },
];

const INTENSITIES = [
  { key: "quick", label: "Quick", desc: "3 cases/category · 2 reps" },
  { key: "standard", label: "Standard", desc: "6 cases/category · 3 reps" },
  { key: "deep", label: "Deep", desc: "12 cases/category · 5 reps" },
  { key: "adversarial", label: "Adversarial", desc: "20 cases/category · 5 reps" },
];

const STEPS = ["System", "Categories", "Policies", "Intensity", "Review"];

export default function NewAuditPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: systems } = useSystems({ page_size: 100 });
  const { data: policies } = usePolicies();
  const [step, setStep] = useState(0);
  const [systemId, setSystemId] = useState(params.get("system") ?? "");
  const [categories, setCategories] = useState<string[]>(["fairness", "privacy", "agent_action"]);
  const [policyVersionIds, setPolicyVersionIds] = useState<string[]>([]);
  const [intensity, setIntensity] = useState("standard");
  const [launching, setLaunching] = useState(false);

  const system = useMemo(() => systems?.items.find((s) => s.id === systemId), [systems, systemId]);
  const canNext = [!!systemId, categories.length > 0, true, !!intensity, true][step];

  async function launch() {
    setLaunching(true);
    try {
      const audit = await api.post<Audit>("/audits", { system_id: systemId, categories, policy_version_ids: policyVersionIds, intensity, config: { seed: 7 }, start: true });
      router.push(`/dashboard/audits/${audit.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to launch audit");
      setLaunching(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="New Audit" breadcrumbs={[{ label: "Audits", href: "/dashboard/audits" }, { label: "New" }]} />
      <div className="mb-6 flex items-center gap-2">
        {STEPS.map((s, i) => (
          <div key={s} className="flex flex-1 items-center gap-2">
            <div className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold", i < step ? "bg-[var(--color-success)] text-black" : i === step ? "bg-[var(--color-accent)] text-white" : "bg-[var(--color-surface-2)] text-[var(--color-text-subtle)]")}>
              {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
            </div>
            <span className={cn("hidden text-xs sm:block", i === step ? "text-[var(--color-text)]" : "text-[var(--color-text-subtle)]")}>{s}</span>
            {i < STEPS.length - 1 ? <div className="h-px flex-1 bg-[var(--color-border)]" /> : null}
          </div>
        ))}
      </div>

      <Card>
        <CardBody className="min-h-[280px]">
          {step === 0 && (
            <Step title="Select AI system">
              <div className="grid gap-2">
                {(systems?.items ?? []).map((s) => (
                  <button key={s.id} onClick={() => setSystemId(s.id)} className={cn("flex items-center justify-between rounded-[var(--radius)] border px-4 py-3 text-left transition-colors", systemId === s.id ? "border-[var(--color-accent)] bg-[var(--color-accent-dim)]" : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]")}>
                    <div>
                      <p className="text-sm font-medium">{s.name}</p>
                      <p className="text-xs text-[var(--color-text-subtle)]">{titleCase(s.system_type)} · {titleCase(s.environment)} · {s.model_name}</p>
                    </div>
                    {systemId === s.id ? <Check className="h-4 w-4 text-[var(--color-accent-bright)]" /> : null}
                  </button>
                ))}
                {systems && systems.items.length === 0 ? <p className="text-sm text-[var(--color-text-subtle)]">No systems yet. Create one first.</p> : null}
              </div>
            </Step>
          )}
          {step === 1 && (
            <Step title="Select evaluation categories" hint="Injection/jailbreak/tool-abuse require an imported red-team corpus.">
              <div className="grid gap-2 sm:grid-cols-2">
                {CATEGORIES.map((c) => {
                  const on = categories.includes(c.key);
                  return (
                    <button key={c.key} onClick={() => setCategories((prev) => (on ? prev.filter((k) => k !== c.key) : [...prev, c.key]))} className={cn("flex items-start gap-2.5 rounded-[var(--radius)] border px-3.5 py-2.5 text-left", on ? "border-[var(--color-accent)] bg-[var(--color-accent-dim)]" : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]")}>
                      <div className={cn("mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border", on ? "border-[var(--color-accent)] bg-[var(--color-accent)]" : "border-[var(--color-border-strong)]")}>{on ? <Check className="h-3 w-3 text-white" /> : null}</div>
                      <div>
                        <p className="text-sm font-medium">{c.label}</p>
                        <p className="text-xs text-[var(--color-text-subtle)]">{c.desc}</p>
                      </div>
                    </button>
                  );
                })}
              </div>
            </Step>
          )}
          {step === 2 && (
            <Step title="Select policies (optional)" hint="Auditing against a policy maps results to its controls.">
              <div className="grid gap-2">
                {(policies?.items ?? []).filter((p) => p.current_version_id).map((p) => {
                  const vid = p.current_version_id!;
                  const on = policyVersionIds.includes(vid);
                  return (
                    <button key={p.id} onClick={() => setPolicyVersionIds((prev) => (on ? prev.filter((v) => v !== vid) : [...prev, vid]))} className={cn("flex items-center justify-between rounded-[var(--radius)] border px-4 py-3 text-left", on ? "border-[var(--color-accent)] bg-[var(--color-accent-dim)]" : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]")}>
                      <div>
                        <p className="text-sm font-medium">{p.name}</p>
                        <p className="text-xs text-[var(--color-text-subtle)]">{p.key} · {titleCase(p.status)}</p>
                      </div>
                      {on ? <Check className="h-4 w-4 text-[var(--color-accent-bright)]" /> : null}
                    </button>
                  );
                })}
                {policies && policies.items.length === 0 ? <p className="text-sm text-[var(--color-text-subtle)]">No policies yet — you can audit without one.</p> : null}
              </div>
            </Step>
          )}
          {step === 3 && (
            <Step title="Test intensity">
              <div className="grid gap-2 sm:grid-cols-2">
                {INTENSITIES.map((i) => (
                  <button key={i.key} onClick={() => setIntensity(i.key)} className={cn("rounded-[var(--radius)] border px-4 py-3 text-left", intensity === i.key ? "border-[var(--color-accent)] bg-[var(--color-accent-dim)]" : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]")}>
                    <p className="text-sm font-medium">{i.label}</p>
                    <p className="text-xs text-[var(--color-text-subtle)]">{i.desc}</p>
                  </button>
                ))}
              </div>
            </Step>
          )}
          {step === 4 && (
            <Step title="Review & launch">
              <dl className="space-y-2 text-sm">
                <Row label="System" value={system?.name ?? "—"} />
                <Row label="Categories" value={categories.map(titleCase).join(", ")} />
                <Row label="Policies" value={policyVersionIds.length ? `${policyVersionIds.length} selected` : "None"} />
                <Row label="Intensity" value={titleCase(intensity)} />
              </dl>
              <p className="mt-4 text-xs text-[var(--color-text-subtle)]">The audit runs asynchronously. You'll watch live progress and can inspect evidence for every finding.</p>
            </Step>
          )}
        </CardBody>
      </Card>

      <div className="mt-4 flex items-center justify-between">
        <Button variant="ghost" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0} icon={ChevronLeft}>
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button onClick={() => setStep((s) => s + 1)} disabled={!canNext}>
            Continue <ChevronRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button onClick={launch} loading={launching} icon={Rocket}>
            Launch Audit
          </Button>
        )}
      </div>
    </div>
  );
}

function Step({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-semibold">{title}</h3>
      {hint ? <p className="mt-0.5 mb-3 text-xs text-[var(--color-text-subtle)]">{hint}</p> : <div className="mb-3" />}
      {children}
    </div>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-[var(--color-border)]/60 pb-2">
      <dt className="text-[var(--color-text-muted)]">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
