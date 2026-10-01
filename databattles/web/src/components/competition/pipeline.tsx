"use client";

import { Check, Loader2, Minus, X } from "lucide-react";

import { cn } from "@/lib/cn";

type StepState = "done" | "active" | "failed" | "idle" | "skipped" | "unknown";

const STEPS = [
  { key: "queued", label: "Queued" },
  { key: "validating", label: "Validating" },
  { key: "scoring", label: "Scoring" },
  { key: "result", label: "Scored" },
] as const;

const STATE_TEXT: Record<StepState, string> = {
  done: "done",
  active: "in progress",
  failed: "failed",
  idle: "not started",
  skipped: "skipped",
  unknown: "not reported",
};

/**
 * Maps a real submission status onto the scoring pipeline. Nothing is inferred beyond what the status says:
 * `rejected` is a validation failure, `failed` is an infrastructure failure at an unreported step.
 */
function stepStates(status: string): { states: StepState[]; resultLabel: string } {
  switch (status) {
    case "queued":
      return { states: ["active", "idle", "idle", "idle"], resultLabel: "Scored" };
    case "validating":
      return { states: ["done", "active", "idle", "idle"], resultLabel: "Scored" };
    case "scoring":
      return { states: ["done", "done", "active", "idle"], resultLabel: "Scored" };
    case "scored":
      return { states: ["done", "done", "done", "done"], resultLabel: "Scored" };
    case "rejected":
      return { states: ["done", "failed", "skipped", "skipped"], resultLabel: "Rejected" };
    case "failed":
      return { states: ["done", "unknown", "unknown", "failed"], resultLabel: "Failed" };
    case "canceled":
      return { states: ["failed", "skipped", "skipped", "skipped"], resultLabel: "Canceled" };
    default:
      return { states: ["idle", "idle", "idle", "idle"], resultLabel: "Scored" };
  }
}

function Node({ state }: { state: StepState }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border transition-[background-color,border-color,color] duration-500 ease-out-expo",
        state === "done" && "border-transparent bg-success text-[var(--bg)]",
        state === "active" && "border-[color-mix(in_oklab,var(--info)_55%,transparent)] bg-info-soft text-info",
        state === "failed" && "border-transparent bg-danger text-white",
        (state === "idle" || state === "unknown") && "border-border-strong bg-bg-elevated text-subtle",
        state === "skipped" && "border-dashed border-border-strong bg-transparent text-subtle opacity-60",
      )}
    >
      {state === "done" ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : null}
      {state === "active" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
      {state === "failed" ? <X className="h-3.5 w-3.5" strokeWidth={3} /> : null}
      {state === "unknown" || state === "skipped" ? <Minus className="h-3 w-3" /> : null}
      {state === "idle" ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
    </span>
  );
}

/** queued → validating → scoring → scored/failed, driven only by the submission's real status. */
export function SubmissionPipeline({ status, className }: { status: string; className?: string }) {
  const { states, resultLabel } = stepStates(status);
  return (
    <ol aria-label="Scoring pipeline" className={cn("grid grid-cols-4", className)}>
      {STEPS.map((step, i) => {
        const state = states[i];
        const label = i === STEPS.length - 1 ? resultLabel : step.label;
        const lineDone = states[i] === "done" && (states[i + 1] === "done" || states[i + 1] === "active" || states[i + 1] === "failed");
        return (
          <li key={step.key} className="relative flex flex-col items-center text-center">
            {i < STEPS.length - 1 ? (
              <span aria-hidden className="absolute left-1/2 top-[13px] h-0.5 w-full overflow-hidden rounded-full bg-border-strong">
                <span className={cn("block h-full origin-left rounded-full bg-success transition-transform duration-700 ease-out-expo", lineDone ? "scale-x-100" : "scale-x-0")} />
              </span>
            ) : null}
            <Node state={state} />
            <span
              className={cn(
                "mt-2 text-[11px] font-medium leading-tight",
                state === "done" ? "text-fg" : state === "active" ? "text-info" : state === "failed" ? "text-danger" : "text-subtle",
              )}
            >
              {label}
              <span className="sr-only">: {STATE_TEXT[state]}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
