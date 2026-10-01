import { Check } from "lucide-react";

import { Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/cn";

/**
 * Read-only progress for the onboarding wizard: numbered steps with a filling brand track. Steps are not
 * clickable — moving between them stays with the wizard's own Back / Continue buttons.
 */
export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol aria-label="Onboarding progress" className="mb-8 grid gap-2" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
      {steps.map((label, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={label} aria-current={active ? "step" : undefined} className="min-w-0">
            <div className="h-1 overflow-hidden rounded-full bg-surface-3">
              <div
                className="h-full rounded-full bg-brand shadow-[0_0_10px_-2px_color-mix(in_oklab,var(--accent)_70%,transparent)] transition-[width] duration-500 ease-out-expo"
                style={{ width: done || active ? "100%" : "0%" }}
              />
            </div>
            <div className="mt-2.5 flex min-w-0 items-center gap-2">
              <span
                className={cn(
                  "tabular flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[10px] transition-colors duration-300",
                  done && "border-transparent bg-success-soft text-success",
                  active && "border-accent bg-accent-soft text-accent-strong",
                  !done && !active && "border-border-strong text-subtle",
                )}
                aria-hidden
              >
                {done ? <Check className="h-3 w-3 animate-pop" /> : i + 1}
              </span>
              <span className={cn("truncate text-xs font-medium", active ? "text-fg" : "text-subtle")}>{label}</span>
              <span className="sr-only">{done ? "(completed)" : active ? "(current step)" : "(upcoming)"}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Loading placeholder shaped like the first wizard step. */
export function OnboardingSkeleton() {
  return (
    <div role="status" aria-label="Loading">
      <div className="mb-8 grid grid-cols-3 gap-2">
        {[0, 1, 2].map((i) => (
          <div key={i}>
            <Skeleton className="h-1 w-full rounded-full" />
            <Skeleton className="mt-3 h-3 w-16" />
          </div>
        ))}
      </div>
      <div className="space-y-5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i}>
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-2 h-10 w-full rounded-[var(--radius-md)]" />
          </div>
        ))}
      </div>
    </div>
  );
}
