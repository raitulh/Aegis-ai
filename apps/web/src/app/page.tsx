import { ArrowRight, Boxes, FileCheck2, Fingerprint, GitBranch, Lock, Radar, RefreshCcw, Scale, ScrollText, ShieldAlert, ShieldCheck, TerminalSquare } from "lucide-react";
import Link from "next/link";
import { MarketingFooter } from "@/components/marketing/footer";
import { MarketingNav } from "@/components/marketing/nav";
import { TrustCoreLazy } from "@/components/marketing/trust-core-lazy";
import { Button } from "@/components/ui/primitives";

const LOOP = [
  { icon: ScrollText, label: "Policy", text: "Written requirements compiled into testable controls; runtime rules versioned and simulated before they go live." },
  { icon: ShieldAlert, label: "Test", text: "Fairness, grounding, safety, privacy, injection and agent-action audits, on demand, on a schedule or on every change." },
  { icon: Radar, label: "Guard", text: "Agent actions checked at runtime — observe, audit, or enforce with blocking and human approval." },
  { icon: FileCheck2, label: "Prove", text: "Every result is backed by hash-chained evidence you can export as a signed package and verify offline." },
  { icon: RefreshCcw, label: "Fix & re-test", text: "Findings move through a tracked lifecycle; fixes are verified by re-running the exact failing tests." },
];

const ENGINES = [
  { icon: Scale, name: "Fairness", desc: "Counterfactual treatment tests: only a protected attribute changes, with repetitions and significance testing.", example: "Score changed by −10.7 points when only the stated gender changed (p = 0.0002)." },
  { icon: FileCheck2, name: "Grounding", desc: "Claim-level verification of answers against the sources the system was given.", example: "“Employs 40,000 workers” — no supporting source passage." },
  { icon: ShieldAlert, name: "Safety", desc: "Probes for disallowed and high-risk behaviour with deterministic refusal detection.", example: "Answered 2 of 50 high-risk decision probes without a refusal." },
  { icon: Fingerprint, name: "Privacy", desc: "Detection of personal data and secrets across outputs, retrieval and tool calls.", example: "Returned another customer's email address from internal notes." },
  { icon: Lock, name: "Injection & jailbreak", desc: "Resistance tests driven by a corpus you import — Aegis ships no attack payloads.", example: "A planted instruction inside a retrieved document was followed." },
  { icon: GitBranch, name: "Agent actions", desc: "Tool authorization, required human approval and data-flow checks on every action.", example: "A rejection email was sent without the required approval." },
];

export default function LandingPage() {
  return (
    <div className="relative min-h-screen">
      <MarketingNav />
      <main id="content">
        {/* Hero */}
        <section className="relative overflow-hidden aegis-noise">
          <div className="absolute inset-0 aegis-grid opacity-40" aria-hidden />
          <div className="absolute inset-0 aegis-radial" aria-hidden />
          <div className="relative mx-auto grid max-w-7xl items-center gap-10 px-4 py-16 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:py-24">
            <div>
              <p className="inline-flex items-center gap-2 rounded-full border border-[var(--color-border-strong)] bg-[var(--color-surface)]/70 px-3 py-1 text-xs text-[var(--color-text-muted)]">
                <ShieldCheck className="h-3.5 w-3.5 text-[var(--color-accent-bright)]" aria-hidden /> Continuous AI assurance
              </p>
              <h1 className="mt-5 text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
                Know how your AI behaves — <span className="bg-gradient-to-r from-[var(--color-accent-bright)] to-[var(--color-success)] bg-clip-text text-transparent">and prove it.</span>
              </h1>
              <p className="mt-5 max-w-xl text-lg text-[var(--color-text-muted)]">
                Aegis tests models and agents before release, guards their actions in production, and keeps verifiable evidence of every result — so risk, security and engineering teams work from the same facts.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <Link href="/demo">
                  <Button size="lg">
                    <ShieldCheck className="h-4 w-4" aria-hidden /> Try the sandbox
                  </Button>
                </Link>
                <Link href="/docs">
                  <Button size="lg" variant="secondary">
                    Read the docs <ArrowRight className="h-4 w-4" aria-hidden />
                  </Button>
                </Link>
              </div>
              <ul className="mt-10 grid max-w-xl gap-2 text-sm text-[var(--color-text-muted)] sm:grid-cols-2">
                {[
                  "Signed evidence packages, verifiable offline",
                  "Runtime guard: observe, audit or enforce",
                  "Policy simulation on your recorded traffic",
                  "Self-hostable; your models, your data",
                ].map((t) => (
                  <li key={t} className="flex items-start gap-2">
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-success)]" aria-hidden />
                    {t}
                  </li>
                ))}
              </ul>
            </div>
            <div className="relative h-[340px] w-full sm:h-[440px] lg:h-[520px]">
              <TrustCoreLazy />
            </div>
          </div>
        </section>

        {/* Problem */}
        <section className="mx-auto max-w-5xl px-6 py-20 text-center">
          <h2 className="text-3xl font-semibold tracking-tight">AI systems fail quietly.</h2>
          <p className="mx-auto mt-4 max-w-2xl text-[var(--color-text-muted)]">
            A screening model that scores candidates differently by gender, an agent that sends confidential data to an outside address, a support bot that invents a refund policy — none of these throw an exception. Aegis makes them visible, records the evidence, and tracks the fix.
          </p>
        </section>

        {/* The loop */}
        <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6" aria-labelledby="loop">
          <SectionHead id="loop" eyebrow="The assurance loop" title="One workflow from written policy to verified fix." />
          <ol className="mt-10 grid gap-3 md:grid-cols-5">
            {LOOP.map((s, i) => (
              <li key={s.label} className="relative rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
                <span className="font-mono text-xs text-[var(--color-text-subtle)]">0{i + 1}</span>
                <s.icon className="mt-2 h-5 w-5 text-[var(--color-accent-bright)]" aria-hidden />
                <h3 className="mt-2 text-base font-semibold">{s.label}</h3>
                <p className="mt-1.5 text-sm text-[var(--color-text-muted)]">{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Engines */}
        <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6" aria-labelledby="engines">
          <SectionHead id="engines" eyebrow="Audit engines" title="Deterministic where possible, model-assisted where useful, evidence-backed everywhere." />
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {ENGINES.map((e) => (
              <div key={e.name} className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
                <div className="flex items-center gap-3">
                  <div className="grid h-9 w-9 place-items-center rounded-[var(--radius)] bg-[var(--color-accent-dim)] text-[var(--color-accent-bright)]">
                    <e.icon className="h-4.5 w-4.5" aria-hidden />
                  </div>
                  <h3 className="text-base font-semibold">{e.name}</h3>
                </div>
                <p className="mt-3 text-sm text-[var(--color-text-muted)]">{e.desc}</p>
                <figure className="mt-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-bg)]/60 p-2.5">
                  <figcaption className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-subtle)]">Example finding · simulated system</figcaption>
                  <p className="mt-1 font-mono text-xs text-[var(--color-text-muted)]">{e.example}</p>
                </figure>
              </div>
            ))}
          </div>
        </section>

        {/* Runtime + evidence */}
        <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-6">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Radar className="h-4 w-4 text-[var(--color-accent-bright)]" aria-hidden /> Runtime Guard for agents
              </div>
              <p className="mb-4 text-sm text-[var(--color-text-muted)]">Ask before acting. Published policies decide; enforce mode blocks or holds the action for a person to approve. Every decision is recorded as evidence.</p>
              <pre className="overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] p-4 font-mono text-xs text-[var(--color-text-muted)]">
                {`with aegis.runtime.trace(system_id, agent="support-agent") as trace:
    decision = trace.check("tool.call", tool="send_email",
        payload={"destination": "external",
                 "data_classification": "confidential"})
    if decision.requires_approval:
        ok = aegis.runtime.wait_for_approval(decision.approval_id)
    elif decision.allowed:
        send_email(...)`}
              </pre>
            </div>
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-6">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <FileCheck2 className="h-4 w-4 text-[var(--color-success)]" aria-hidden /> Evidence anyone can verify
              </div>
              <p className="mb-4 text-sm text-[var(--color-text-muted)]">Export an audit as a signed package: manifest, artifacts, hash chain and an Ed25519 signature. The verifier ships inside the package — no Aegis account needed.</p>
              <pre className="overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] p-4 font-mono text-xs text-[var(--color-text-muted)]">
                {`$ python3 verify.py --public-key <key>
files ............ ok (148)
hash chain ....... ok (143 records)
root hash ........ ok
signature ........ valid (Ed25519)
status: VERIFIED`}
              </pre>
              <p className="mt-3 text-xs text-[var(--color-text-subtle)]">Verification shows the records are unchanged since capture. It is integrity evidence, not a legal attestation.</p>
            </div>
          </div>
        </section>

        {/* Developers + frameworks */}
        <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-6">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <TerminalSquare className="h-4 w-4 text-[var(--color-accent-bright)]" aria-hidden /> API, Python SDK and MCP server
              </div>
              <pre className="overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] p-4 font-mono text-xs text-[var(--color-text-muted)]">
                {`from aegis_ai import Aegis

aegis = Aegis(api_key, base_url="https://aegis.example.com")
audit = aegis.audit(system_id, ["fairness", "privacy", "safety"])
print(audit["status"], audit["findings_count"])

# CI/CD: re-test what a change can affect
aegis.assurance.trigger(system_id=system_id,
    event_type="prompt_change", ref=git_sha)`}
              </pre>
            </div>
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-6">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Boxes className="h-4 w-4 text-[var(--color-success)]" aria-hidden /> Framework mapping
              </div>
              <p className="text-sm text-[var(--color-text-muted)]">Map controls to NIST AI RMF, the NIST Generative AI Profile, OWASP Top 10 for LLM Applications and ISO/IEC 42001 as versioned reference data, and report readiness against them.</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {["NIST AI RMF 1.0", "NIST GenAI Profile", "OWASP LLM Top 10", "ISO/IEC 42001"].map((f) => (
                  <span key={f} className="rounded-full border border-[var(--color-border-strong)] px-2.5 py-1 text-xs text-[var(--color-text-muted)]">
                    {f}
                  </span>
                ))}
              </div>
              <p className="mt-4 text-xs text-[var(--color-text-subtle)]">Mappings support your assessment; they are not legal advice and do not certify compliance.</p>
            </div>
          </div>
        </section>

        {/* Final CTA */}
        <section className="relative overflow-hidden">
          <div className="absolute inset-0 aegis-radial" aria-hidden />
          <div className="relative mx-auto max-w-4xl px-6 py-24 text-center">
            <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">See the whole loop in five minutes.</h2>
            <p className="mx-auto mt-4 max-w-xl text-[var(--color-text-muted)]">The sandbox runs real audits against simulated systems — no API keys, nothing to install. Everything in it is marked DEMO and expires automatically.</p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/demo">
                <Button size="lg">
                  <ShieldCheck className="h-4 w-4" aria-hidden /> Try the sandbox
                </Button>
              </Link>
              <Link href="/signup">
                <Button size="lg" variant="secondary">
                  Create a workspace
                </Button>
              </Link>
              <Link href="/contact?kind=demo">
                <Button size="lg" variant="ghost">
                  Talk to us
                </Button>
              </Link>
            </div>
          </div>
        </section>
      </main>
      <MarketingFooter />
    </div>
  );
}

function SectionHead({ id, eyebrow, title }: { id: string; eyebrow: string; title: string }) {
  return (
    <div className="max-w-3xl">
      <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-accent-bright)]">{eyebrow}</p>
      <h2 id={id} className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
        {title}
      </h2>
    </div>
  );
}
