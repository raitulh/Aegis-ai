import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

type Tone = "accent" | "success" | "danger" | "warning" | "cyan";
type Size = "md" | "lg" | "xl";

const TONE: Record<Tone, string> = {
  accent: "text-accent-strong",
  success: "text-success",
  danger: "text-danger",
  warning: "text-warning",
  cyan: "text-cyan",
};

const SIZE: Record<Size, { box: string; core: string; node: string }> = {
  md: { box: "h-24 w-24", core: "h-14 w-14 [&_svg]:h-6 [&_svg]:w-6", node: "h-2 w-2" },
  lg: { box: "h-32 w-32", core: "h-[4.5rem] w-[4.5rem] [&_svg]:h-7 [&_svg]:w-7", node: "h-2.5 w-2.5" },
  xl: { box: "h-40 w-40 sm:h-48 sm:w-48", core: "h-24 w-24 sm:h-28 sm:w-28 [&_svg]:h-9 [&_svg]:w-9", node: "h-2.5 w-2.5" },
};

/**
 * The DataBattles orbit motif wrapped around a status glyph: a slow dashed orbit carrying one node, a tilted
 * ellipse passing behind a glass core, and a soft tone-coloured glow. Purely decorative (aria-hidden) — the
 * meaning always lives in adjacent text. `busy` speeds the orbit up for in-progress states; `pulse` adds the
 * success pulse ring (use only on real success). CSS-only, so it renders on the server and freezes under
 * reduced motion.
 */
export function OrbitMotif({
  tone = "accent",
  size = "md",
  pulse = false,
  busy = false,
  children,
  className,
}: {
  tone?: Tone;
  size?: Size;
  pulse?: boolean;
  busy?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const s = SIZE[size];
  return (
    <div aria-hidden className={cn("relative isolate flex shrink-0 items-center justify-center", s.box, TONE[tone], className)}>
      <span
        className="absolute inset-[-35%] -z-10 rounded-full"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklab, currentColor 16%, transparent), transparent)" }}
      />
      <span
        className={cn(
          "absolute inset-0 rounded-full border border-dashed border-border-strong",
          busy ? "motion-safe:animate-[spin_2.4s_linear_infinite]" : "motion-safe:animate-spin-slow",
        )}
      >
        <span className={cn("absolute left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 rounded-full bg-current shadow-[0_0_12px_currentColor]", s.node)} />
      </span>
      <span className="absolute left-1/2 top-1/2 h-[36%] w-[132%] -translate-x-1/2 -translate-y-1/2 rotate-[-16deg] rounded-[50%] border border-current opacity-25" />
      <span className="absolute left-1/2 top-1/2 h-[64%] w-[64%] -translate-x-1/2 -translate-y-1/2 rounded-full border border-border" />
      <span
        className={cn(
          "relative flex items-center justify-center rounded-full border border-border-strong bg-surface-2 shadow-[inset_0_1px_0_var(--hairline-highlight),0_10px_30px_-12px_currentColor]",
          pulse && "motion-safe:animate-pulse-ring",
          s.core,
        )}
      >
        {children}
      </span>
    </div>
  );
}

/** Check mark that draws itself once (stroke animation; static under reduced motion). */
export function CheckGlyph({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className="motion-safe:animate-check" />
    </svg>
  );
}
