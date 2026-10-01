"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { useFinePointer, useReducedMotion } from "@/lib/motion";

/**
 * Pulls its child a few pixels toward the cursor (desktop only). Purely decorative: the child keeps its
 * own semantics, focus ring and hit area. Disabled on touch devices and when reduced motion is requested.
 */
export function Magnetic({ children, strength = 0.22, max = 7, className }: { children: ReactNode; strength?: number; max?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const fine = useFinePointer();
  const reduced = useReducedMotion();
  const enabled = fine && !reduced;

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    let raf = 0;
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const dx = Math.max(-max, Math.min(max, (e.clientX - (r.left + r.width / 2)) * strength));
      const dy = Math.max(-max, Math.min(max, (e.clientY - (r.top + r.height / 2)) * strength));
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        el.style.transform = `translate3d(${dx.toFixed(2)}px, ${dy.toFixed(2)}px, 0)`;
      });
    };
    const leave = () => {
      cancelAnimationFrame(raf);
      el.style.transform = "";
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerleave", leave);
      el.style.transform = "";
    };
  }, [enabled, strength, max]);

  return (
    <span ref={ref} className={cn("inline-flex transition-transform duration-300 ease-out-expo will-change-transform", className)}>
      {children}
    </span>
  );
}
