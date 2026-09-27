"use client";
import { titleCase } from "@/lib/utils";

/** Two-column counterfactual comparison — the flagship fairness "wow" component. */
export function CounterfactualComparison({ data }: { data: any }) {
  const a = data?.case_a ?? data?.example?.case_a;
  const b = data?.case_b ?? data?.example?.case_b;
  const changed = data?.changed_field ?? data?.example?.changed_field ?? "attribute";
  const valA = data?.value_a ?? data?.example?.value_a ?? a?.attributes?.[changed];
  const valB = data?.value_b ?? data?.example?.value_b ?? b?.attributes?.[changed];
  const deltaPts = data?.mean_delta_points ?? data?.example?.mean_delta_points;
  const pValue = data?.p_value;
  const flips = data?.decision_flips;
  const reps = data?.repetitions ?? data?.example?.repetitions ?? data?.pairs;

  if (!a || !b) {
    return <p className="text-sm text-[var(--color-text-subtle)]">Counterfactual detail is not available for this finding.</p>;
  }

  return (
    <div>
      <div className="mb-3 inline-flex items-center gap-2 rounded-md bg-[var(--color-surface-2)] px-2.5 py-1 text-xs">
        <span className="text-[var(--color-text-subtle)]">Controlled variable changed:</span>
        <span className="font-medium">{titleCase(changed)}</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Case label="Case A" attr={changed} value={valA} score={a.mean_score} decision={a.decision} output={a.sample_output} tone="var(--color-accent)" />
        <Case label="Case B" attr={changed} value={valB} score={b.mean_score} decision={b.decision} output={b.sample_output} tone="var(--color-high)" />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Mean Δ (points)" value={deltaPts != null ? `${deltaPts > 0 ? "+" : ""}${Number(deltaPts).toFixed(1)}` : "—"} tone="var(--color-high)" />
        <Stat label="Repetitions" value={reps ?? "—"} />
        <Stat label="Decision flips" value={flips ?? "—"} />
        <Stat label="p-value" value={pValue != null ? Number(pValue).toFixed(4) : "—"} />
      </div>
      <p className="mt-3 text-xs text-[var(--color-text-subtle)]">Observed counterfactual behavioural disparity under controlled tests — not proof of real-world discrimination.</p>
    </div>
  );
}

function Case({ label, attr, value, score, decision, output, tone }: { label: string; attr: string; value: any; score: any; decision: any; output: any; tone: string }) {
  return (
    <div className="rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3.5">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: tone }}>{label}</span>
        {value != null ? <span className="rounded-md bg-[var(--color-surface-2)] px-2 py-0.5 text-xs">{titleCase(attr)}: <b>{String(value)}</b></span> : null}
      </div>
      <div className="flex items-baseline gap-3">
        <div>
          <p className="text-xs text-[var(--color-text-subtle)]">Score</p>
          <p className="font-mono text-2xl font-semibold">{score != null ? Number(score).toFixed(0) : "—"}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--color-text-subtle)]">Recommendation</p>
          <p className="text-sm font-medium capitalize">{decision ?? "—"}</p>
        </div>
      </div>
      {output ? <p className="mt-2 line-clamp-3 rounded-[var(--radius-sm)] bg-[var(--color-bg)]/50 p-2 font-mono text-[11px] text-[var(--color-text-subtle)]">{output}</p> : null}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: any; tone?: string }) {
  return (
    <div className="rounded-[var(--radius)] bg-[var(--color-surface-2)] px-2.5 py-2 text-center">
      <p className="font-mono text-sm font-semibold" style={{ color: tone }}>{value}</p>
      <p className="text-[10px] text-[var(--color-text-subtle)]">{label}</p>
    </div>
  );
}
