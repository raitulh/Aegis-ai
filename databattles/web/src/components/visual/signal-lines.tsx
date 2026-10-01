import { useId } from "react";

import { cn } from "@/lib/cn";

/**
 * Animated data paths: thin curves with a bright pulse travelling along them, suggesting data moving
 * between systems. Decorative only (aria-hidden); the pulse freezes under reduced motion.
 */
export function SignalLines({ className, density = 5 }: { className?: string; density?: number }) {
  const id = useId().replace(/:/g, "");
  const paths = Array.from({ length: density }, (_, i) => {
    const y = 40 + i * (220 / Math.max(1, density - 1));
    const c = 60 + ((i * 47) % 120);
    return `M -20 ${y} C 220 ${y - c}, 420 ${y + c}, 640 ${y - c / 3} S 960 ${y + c / 2}, 1220 ${y}`;
  });
  return (
    <svg aria-hidden viewBox="0 0 1200 300" preserveAspectRatio="none" className={cn("pointer-events-none h-full w-full", className)} fill="none">
      <defs>
        <linearGradient id={`${id}-s`} x1="0" x2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity="0" />
          <stop offset="0.5" stopColor="var(--accent)" stopOpacity="0.35" />
          <stop offset="1" stopColor="var(--cyan)" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}-p`} x1="0" x2="1">
          <stop offset="0" stopColor="var(--cyan)" stopOpacity="0" />
          <stop offset="0.5" stopColor="var(--cyan)" stopOpacity="1" />
          <stop offset="1" stopColor="var(--accent-strong)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {paths.map((d, i) => (
        <g key={i}>
          <path d={d} stroke={`url(#${id}-s)`} strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <path
            d={d}
            stroke={`url(#${id}-p)`}
            strokeWidth="1.6"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
            strokeDasharray="60 1400"
            className="motion-safe:animate-[signal_7s_linear_infinite]"
            style={{ animationDelay: `${-i * 1.3}s` }}
          />
        </g>
      ))}
    </svg>
  );
}
