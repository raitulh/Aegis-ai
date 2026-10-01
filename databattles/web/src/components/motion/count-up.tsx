"use client";

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { easeOutExpo, useInView, useReducedMotion } from "@/lib/motion";

/**
 * Counts up to a real value the first time it scrolls into view, and animates between values when the
 * underlying data changes. Screen readers get the final formatted value only. Shows `placeholder`
 * (an em dash by default) until a value exists — it never invents one.
 */
export function CountUp({
  value,
  format = (n: number) => formatNumber(n),
  duration = 1100,
  placeholder = "—",
  className,
}: {
  value: number | null | undefined;
  format?: (n: number) => string;
  duration?: number;
  placeholder?: string;
  className?: string;
}) {
  const [ref, inView] = useInView<HTMLSpanElement>({ rootMargin: "0px 0px -5% 0px" });
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState<number | null>(null);
  const shown = useRef<number>(0);

  useEffect(() => {
    if (value === null || value === undefined) return;
    if (reduced) {
      shown.current = value;
      setDisplay(value);
      return;
    }
    if (!inView) return;
    const from = shown.current;
    if (from === value) {
      setDisplay(value);
      return;
    }
    const start = performance.now();
    const integer = Number.isInteger(value);
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      const v = from + (value - from) * easeOutExpo(p);
      shown.current = p >= 1 ? value : v;
      setDisplay(p >= 1 ? value : integer ? Math.round(v) : v);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, inView, reduced, duration]);

  const hasValue = value !== null && value !== undefined;
  return (
    <span ref={ref} className={cn("tabular", className)}>
      <span aria-hidden>{hasValue ? format(display ?? 0) : placeholder}</span>
      <span className="sr-only">{hasValue ? format(value) : "Not available"}</span>
    </span>
  );
}
