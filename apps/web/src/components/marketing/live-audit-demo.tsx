"use client";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronRight, Loader2, ShieldCheck, TriangleAlert } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

const STAGES = [
  "Initializing audit…",
  "Loading AI system…",
  "Generating probes…",
  "Running evaluation…",
  "Verifying claims…",
  "Checking policies…",
  "Building evidence…",
];

const EVENTS = [
  { text: "Policy parsed — 3 controls compiled", level: "ok" },
  { text: "50 fairness probes generated", level: "ok" },
  { text: "100 factual tests executed", level: "ok" },
  { text: "Unsupported claim detected", level: "warn" },
  { text: "PII scan completed", level: "ok" },
  { text: "Policy control HUM-004 failed", level: "warn" },
  { text: "143 evidence artifacts captured", level: "ok" },
];

const RESULT = {
  scores: { Fairness: 71, Truthfulness: 84, Safety: 96, Privacy: 91, Compliance: 62 },
  findings: 17,
  high: 4,
  evidence: 143,
};

const FINDING_CARDS = [
  { title: "Counterfactual disparity on gender", dimension: "Fairness", detail: "Changing the candidate's gender shifted the score by −10.7 points on average (p = 0.0002).", severity: "high" },
  { title: "Unsupported candidate claim", dimension: "Truthfulness", detail: "The summary stated an award not present in the source résumé.", severity: "medium" },
  { title: "Human oversight bypass", dimension: "Governance", detail: "A rejection email was sent without the required human approval (HUM-004).", severity: "high" },
];

export function LiveAuditDemo() {
  const [phase, setPhase] = useState<"idle" | "running" | "done">("idle");
  const [stage, setStage] = useState(0);
  const [events, setEvents] = useState<typeof EVENTS>([]);
  const [openFinding, setOpenFinding] = useState<number | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const run = useCallback(() => {
    timers.current.forEach(clearTimeout);
    setPhase("running");
    setStage(0);
    setEvents([]);
    setOpenFinding(null);
    STAGES.forEach((_, i) => timers.current.push(setTimeout(() => setStage(i), i * 420)));
    EVENTS.forEach((e, i) => timers.current.push(setTimeout(() => setEvents((prev) => [...prev, e]), 500 + i * 360)));
    timers.current.push(setTimeout(() => setPhase("done"), 500 + EVENTS.length * 360 + 500));
  }, []);

  return (
    <div className="overflow-hidden rounded-[var(--radius-xl)] border border-[var(--color-border-strong)] bg-[var(--color-bg-elevated)] shadow-[var(--shadow-lg)]">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3">
        <div className="flex items-center gap-2 text-sm">
          <span className="h-2 w-2 rounded-full bg-[var(--color-critical)]" />
          <span className="h-2 w-2 rounded-full bg-[var(--color-medium)]" />
          <span className="h-2 w-2 rounded-full bg-[var(--color-success)]" />
          <span className="ml-3 font-mono text-xs text-[var(--color-text-muted)]">aegis · live audit — Hiring-Agent</span>
        </div>
        <span className="rounded-full border border-[var(--color-border-strong)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-subtle)]">Simulated</span>
      </div>

      <div className="grid gap-0 md:grid-cols-[1.1fr_1fr]">
        <div className="border-b border-[var(--color-border)] p-6 md:border-b-0 md:border-r">
          {phase === "idle" && (
            <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-4 text-center">
              <div className="relative">
                <div className="absolute inset-0 animate-[pulse-ring_2s_ease-out_infinite] rounded-full border border-[var(--color-accent)]" />
                <div className="grid h-16 w-16 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[var(--color-accent-bright)]">
                  <ShieldCheck className="h-7 w-7" />
                </div>
              </div>
              <p className="max-w-xs text-sm text-[var(--color-text-muted)]">Run a live audit of a simulated AI hiring agent and inspect the evidence behind each finding.</p>
              <Button onClick={run} icon={ChevronRight}>
                Run a Live Audit
              </Button>
            </div>
          )}

          {phase === "running" && (
            <div className="min-h-[280px]">
              <div className="mb-4 flex items-center gap-2 text-sm font-medium">
                <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent)]" />
                {STAGES[stage]}
              </div>
              <div className="space-y-1.5">
                {events.map((e, i) => (
                  <motion.div key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} className="flex items-center gap-2 font-mono text-xs">
                    {e.level === "ok" ? <Check className="h-3.5 w-3.5 text-[var(--color-success)]" /> : <TriangleAlert className="h-3.5 w-3.5 text-[var(--color-medium)]" />}
                    <span className={e.level === "warn" ? "text-[var(--color-medium)]" : "text-[var(--color-text-muted)]"}>{e.text}</span>
                  </motion.div>
                ))}
              </div>
            </div>
          )}

          {phase === "done" && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="min-h-[280px]">
              <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-success)]">Audit complete</p>
              <div className="mt-4 space-y-3">
                {Object.entries(RESULT.scores).map(([k, v]) => (
                  <div key={k}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="text-[var(--color-text-muted)]">{k}</span>
                      <span className="font-mono font-medium">{v}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-surface-3)]">
                      <motion.div initial={{ width: 0 }} animate={{ width: `${v}%` }} transition={{ duration: 0.8 }} className="h-full rounded-full" style={{ background: v >= 85 ? "var(--color-success)" : v >= 70 ? "var(--color-medium)" : "var(--color-high)" }} />
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-5 flex gap-4 text-sm">
                <Stat value={RESULT.findings} label="findings" />
                <Stat value={RESULT.high} label="high-risk" tone="var(--color-high)" />
                <Stat value={RESULT.evidence} label="evidence" />
              </div>
              <Button variant="ghost" size="sm" className="mt-4" onClick={run}>
                Run again
              </Button>
            </motion.div>
          )}
        </div>

        <div className="p-6">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-subtle)]">
            {phase === "done" ? "Findings — click to inspect" : "Evidence-backed findings"}
          </p>
          <div className="space-y-2">
            {FINDING_CARDS.map((f, i) => (
              <button
                key={i}
                onClick={() => phase === "done" && setOpenFinding(openFinding === i ? null : i)}
                disabled={phase !== "done"}
                className={cn(
                  "w-full rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left transition-all",
                  phase === "done" ? "cursor-pointer hover:border-[var(--color-border-strong)]" : "opacity-40",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{f.title}</span>
                  <span className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase" style={{ color: f.severity === "high" ? "var(--color-high)" : "var(--color-medium)", background: `color-mix(in srgb, ${f.severity === "high" ? "var(--color-high)" : "var(--color-medium)"} 14%, transparent)` }}>
                    {f.severity}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-[var(--color-text-subtle)]">{f.dimension}</p>
                <AnimatePresence>
                  {openFinding === i && (
                    <motion.p initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="mt-2 overflow-hidden text-xs text-[var(--color-text-muted)]">
                      {f.detail}
                    </motion.p>
                  )}
                </AnimatePresence>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone?: string }) {
  return (
    <div>
      <p className="font-mono text-xl font-semibold" style={{ color: tone }}>
        {value}
      </p>
      <p className="text-xs text-[var(--color-text-subtle)]">{label}</p>
    </div>
  );
}
