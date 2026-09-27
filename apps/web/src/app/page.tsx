"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Boxes,
  FileCheck2,
  Fingerprint,
  GitBranch,
  Lock,
  Network,
  ScrollText,
  Scale,
  ShieldAlert,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import { MarketingNav } from "@/components/marketing/nav";
import { MarketingFooter } from "@/components/marketing/footer";
import { LiveAuditDemo } from "@/components/marketing/live-audit-demo";
import { Button } from "@/components/ui/primitives";

const AssuranceGraph = dynamic(() => import("@/components/three/assurance-graph"), {
  loading: () => <div className="h-full w-full skeleton rounded-[var(--radius-lg)]" />,
});

const ENGINES = [
  { icon: Scale, name: "Fairness", desc: "Group metrics and counterfactual treatment tests with real statistics.", example: "Score shifted −10.7 pts when only gender changed (p=0.0002)." },
  { icon: FileCheck2, name: "Truth", desc: "Claim-level verification against your knowledge base and sources.", example: '"Employs 40,000 workers" — unsupported by any source.' },
  { icon: ShieldAlert, name: "Safety", desc: "Synthetic probes for harmful, high-risk and disallowed behavior.", example: "Refused 96% of high-risk decision probes." },
  { icon: Fingerprint, name: "Privacy", desc: "PII & secret detection across output, retrieval and tool calls.", example: "Leaked another customer's email from internal notes." },
  { icon: Lock, name: "Security", desc: "Prompt-injection and jailbreak resistance from an imported corpus.", example: "Planted instruction in a document was obeyed." },
  { icon: ScrollText, name: "Governance", desc: "Human oversight, tool permissions and policy control coverage.", example: "Action executed without required human approval." },
];

const FEATURES = [
  { icon: ScrollText, title: "Policy Compiler", desc: "Turn a natural-language policy (PDF, DOCX, text) into executable controls — each traced back to its exact source page and section." },
  { icon: Network, title: "Evidence Graph", desc: "Every finding links Policy → Control → Test → Evidence → Remediation → Re-test in a tamper-evident, hash-chained record." },
  { icon: GitBranch, title: "Agent Trace Audit", desc: "Inspect each tool call: was it authorized, did it need human approval, did it carry sensitive data — no chain-of-thought stored." },
  { icon: Activity, title: "Continuous Monitoring", desc: "Sample production traffic for hallucination, PII and safety, with model-version change events on the timeline." },
];

export default function LandingPage() {
  return (
    <div className="relative min-h-screen font-georgia" style={{ fontFamily: "Georgia, serif" }}>
      <MarketingNav />

      {/* Hero */}
      <section className="relative overflow-hidden aegis-noise">
        <div className="absolute inset-0 aegis-grid opacity-40" />
        <div className="absolute inset-0 aegis-radial" />
        <div className="relative mx-auto grid max-w-7xl items-center gap-10 px-4 py-20 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:py-28">
          <div>
            <h1 className="text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
              Know when your AI <span className="bg-gradient-to-r from-[var(--color-accent-bright)] to-[var(--color-success)] bg-clip-text text-transparent">cannot be trusted.</span>
            </h1>
            <p className="mt-5 max-w-xl text-lg text-[var(--color-text-muted)]">
              Continuously test models and agents for fairness, hallucination, safety, security, privacy, and policy compliance — with evidence you can actually audit.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/demo">
                <Button size="lg" icon={ShieldCheck}>
                  Run a Live Audit
                </Button>
              </Link>
              <Link href="/demo">
                <Button size="lg" variant="secondary">
                  Explore <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            </div>
            <dl className="mt-12 grid max-w-lg grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
              {[
                ["12,481", "Tests Executed"],
                ["183", "Findings"],
                ["27", "Policy Controls"],
                ["94.2%", "Evidence Coverage"],
              ].map(([v, l]) => (
                <div key={l}>
                  <dt className="font-mono text-2xl font-semibold">{v}</dt>
                  <dd className="text-xs text-[var(--color-text-subtle)]">{l}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-[11px] text-[var(--color-text-subtle)]">Seed metrics from the built-in simulated workspace.</p>
          </div>
          <div className="relative h-[460px] sm:h-[520px] lg:h-[580px] w-full">
            <AssuranceGraph />
          </div>
        </div>
      </section>

      {/* Live audit */}
      <section className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <LiveAuditDemo />
      </section>

      {/* Problem */}
      <section className="mx-auto max-w-5xl px-6 py-20 text-center">
        <h2 className="text-3xl font-semibold tracking-tight">AI systems fail quietly.</h2>
        <p className="mx-auto mt-4 max-w-2xl text-[var(--color-text-muted)]">
          A model that scores women lower, an agent that emails data to an attacker, a support bot that invents refund policies — these rarely throw an exception. Aegis makes them visible, with the evidence to prove it and the workflow to fix it.
        </p>
      </section>

      {/* Six engines */}
      <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
        <SectionHead eyebrow="Six audit engines" title="Deterministic where possible, model-assisted where useful, evidence-backed everywhere." />
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {ENGINES.map((e) => (
            <div key={e.name} className="group rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-border-strong)]">
              <div className="flex items-center gap-3">
                <div className="grid h-9 w-9 place-items-center rounded-[var(--radius)] bg-[var(--color-accent-dim)] text-[var(--color-accent-bright)]">
                  <e.icon className="h-4.5 w-4.5" />
                </div>
                <h3 className="text-base font-semibold">{e.name}</h3>
              </div>
              <p className="mt-3 text-sm text-[var(--color-text-muted)]">{e.desc}</p>
              <p className="mt-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-bg)]/60 p-2.5 font-mono text-xs text-[var(--color-text-subtle)]">
                {e.example}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Flagship features */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-4 md:grid-cols-2">
          {FEATURES.map((f) => (
            <div key={f.title} className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-gradient-to-b from-[var(--color-surface)] to-[var(--color-bg-elevated)] p-6">
              <f.icon className="h-5 w-5 text-[var(--color-accent-bright)]" />
              <h3 className="mt-3 text-lg font-semibold">{f.title}</h3>
              <p className="mt-2 text-sm text-[var(--color-text-muted)]">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Developer + frameworks */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-6">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <TerminalSquare className="h-4 w-4 text-[var(--color-accent-bright)]" /> Developer-first API & SDK
            </div>
            <pre className="overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] p-4 font-mono text-xs text-[var(--color-text-muted)]">
{`from aegis_ai import Aegis

client = Aegis(api_key="aeg_live_...")
audit = client.audits.create(
    system_id=system.id,
    categories=["fairness", "privacy", "safety"],
)
audit = client.audits.wait(audit["id"])
for f in client.findings.list(system_id=system.id):
    print(f["number"], f["title"], f["risk_level"])`}
            </pre>
          </div>
          <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-6">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Boxes className="h-4 w-4 text-[var(--color-success)]" /> Framework mapping
            </div>
            <p className="text-sm text-[var(--color-text-muted)]">
              Map your controls to NIST AI RMF, the NIST GenAI Profile, OWASP LLM Top 10 and ISO/IEC 42001 — as versioned reference data.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {["NIST AI RMF 1.0", "NIST GenAI Profile", "OWASP LLM 2025", "ISO/IEC 42001"].map((f) => (
                <span key={f} className="rounded-full border border-[var(--color-border-strong)] px-2.5 py-1 text-xs text-[var(--color-text-muted)]">
                  {f}
                </span>
              ))}
            </div>
            <p className="mt-4 text-xs text-[var(--color-text-subtle)]">Reference mappings for assessment only — not legal advice or certification.</p>
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 aegis-radial" />
        <div className="relative mx-auto max-w-4xl px-6 py-24 text-center">
          <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">Continuous assurance for intelligent systems.</h2>
          <p className="mx-auto mt-4 max-w-xl text-[var(--color-text-muted)]">Start with the built-in workspace — no API keys required — and watch Aegis audit a real (simulated) hiring agent end to end.</p>
          <div className="mt-8 flex justify-center gap-3">
            <Link href="/demo">
              <Button size="lg" icon={ShieldCheck}>
                Try the Hiring Agent Audit
              </Button>
            </Link>
            <Link href="/signup">
              <Button size="lg" variant="secondary">
                Create a workspace
              </Button>
            </Link>
          </div>
        </div>
      </section>

      <MarketingFooter />
    </div>
  );
}

function SectionHead({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div className="max-w-3xl">
      <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-accent-bright)]">{eyebrow}</p>
      <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h2>
    </div>
  );
}
