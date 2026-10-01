"use client";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState } from "react";
import { ArrowRight, FileText, FlaskConical, Gauge, Network, ScrollText, ShieldAlert, ShieldCheck, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/primitives";
import { api } from "@/lib/api";

const FLOW = [
  { icon: Network, label: "Connect System", desc: "A simulated AI hiring agent" },
  { icon: Gauge, label: "Audit", desc: "Fairness, privacy, safety, policy" },
  { icon: ShieldAlert, label: "Find Risk", desc: "Counterfactual disparity, PII leak" },
  { icon: FileText, label: "Open Evidence", desc: "Hash-chained artifacts" },
  { icon: ScrollText, label: "Show Policy", desc: "Compiled controls" },
  { icon: Wrench, label: "Remediate", desc: "Apply a guardrail fix" },
  { icon: ShieldCheck, label: "Re-test", desc: "Measured before/after" },
];

export default function DemoPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function start() {
    setLoading(true);
    try {
      await api.post("/auth/guest");
      router.push("/dashboard");
    } catch {
      toast.error("Could not start the sandbox. Please try again in a moment.");
      setLoading(false);
    }
  }

  return (
    <div className="relative min-h-screen aegis-noise">
      <div className="absolute inset-0 aegis-grid opacity-30" />
      <div className="absolute inset-0 aegis-radial" />
      <div className="relative mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <div className="flex items-center justify-between">
          <Link href="/" className="focus-ring rounded-md"><Logo /></Link>
          <Link href="/login"><Button variant="ghost" size="sm">Sign in</Button></Link>
        </div>

        <div className="mt-16 text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-[var(--color-border-strong)] bg-[var(--color-surface)]/60 px-3 py-1 text-xs text-[var(--color-text-muted)]">
            <FlaskConical className="h-3.5 w-3.5 text-[var(--color-accent-bright)]" /> DEMO sandbox — no signup
          </div>
          <h1 className="mt-5 text-balance text-4xl font-semibold tracking-tight sm:text-5xl">See Aegis audit an AI hiring agent.</h1>
          <p className="mx-auto mt-4 max-w-xl text-lg text-[var(--color-text-muted)]">
            Start a temporary sandbox with a simulated hiring agent: a completed audit, runtime decisions waiting for your approval, findings and signed evidence — all produced by the real assurance engines.
          </p>
          <div className="mt-8 flex justify-center gap-3">
            <Button size="lg" loading={loading} onClick={start} icon={ShieldCheck}>
              Start the sandbox
            </Button>
            <Link href="/"><Button size="lg" variant="secondary">Back to home</Button></Link>
          </div>
        </div>

        <div className="mx-auto mt-14 max-w-5xl">
          <div className="grid gap-2 sm:grid-cols-7">
            {FLOW.map((s, i) => (
              <div key={s.label} className="relative rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-center">
                <div className="mx-auto grid h-9 w-9 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[var(--color-accent-bright)]">
                  <s.icon className="h-4 w-4" />
                </div>
                <p className="mt-2 text-xs font-semibold">{s.label}</p>
                <p className="mt-0.5 text-[10px] text-[var(--color-text-subtle)]">{s.desc}</p>
                {i < FLOW.length - 1 ? <ArrowRight className="absolute -right-2.5 top-1/2 hidden h-4 w-4 -translate-y-1/2 text-[var(--color-text-subtle)] sm:block" /> : null}
              </div>
            ))}
          </div>
        </div>

        <div className="mx-auto mt-14 grid max-w-5xl gap-3 sm:grid-cols-3">
          {[
            ["Real engines, simulated systems", "Audits, findings, runtime decisions and evidence are produced by the same code that runs on your systems. The AI systems themselves are simulators."],
            ["Clearly marked", "Everything in the sandbox carries a DEMO marker. Nothing in it describes a real organization, person or model."],
            ["Temporary and isolated", "Each sandbox is its own workspace, expires automatically and is deleted with its data. Create a workspace to keep your work."],
          ].map(([title, body]) => (
            <div key={title} className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
              <p className="text-sm font-semibold">{title}</p>
              <p className="mt-1.5 text-sm text-[var(--color-text-muted)]">{body}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
