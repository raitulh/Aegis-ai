"use client";

import { useEffect, useRef, type RefObject } from "react";

import { useFinePointer, useReducedMotion } from "@/lib/motion";

/**
 * Subtle perspective tilt + cursor spotlight for interactive cards (desktop only).
 * Writes CSS variables (`--rx`, `--ry`, `--mx`, `--my`) instead of React state, so pointer movement never
 * re-renders. Pair with the `.spotlight` class for the glare and `[transform:var(--tilt)]` for the tilt.
 */
export function useTilt<T extends HTMLElement>({ max = 5 }: { max?: number } = {}): RefObject<T | null> {
  const ref = useRef<T | null>(null);
  const fine = useFinePointer();
  const reduced = useReducedMotion();

  useEffect(() => {
    const el = ref.current;
    if (!el || !fine) return;
    let raf = 0;
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        el.style.setProperty("--mx", `${(px * 100).toFixed(1)}%`);
        el.style.setProperty("--my", `${(py * 100).toFixed(1)}%`);
        if (!reduced) {
          el.style.setProperty(
            "--tilt",
            `perspective(900px) rotateX(${((0.5 - py) * max).toFixed(2)}deg) rotateY(${((px - 0.5) * max).toFixed(2)}deg) translateY(-3px)`,
          );
        }
      });
    };
    const leave = () => {
      cancelAnimationFrame(raf);
      el.style.removeProperty("--tilt");
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerleave", leave);
    };
  }, [fine, reduced, max]);

  return ref;
}

/** Cursor-following glow only (no tilt) for large surfaces. Pair with the `.spotlight` class. */
export function useSpotlight<T extends HTMLElement>(): RefObject<T | null> {
  const ref = useRef<T | null>(null);
  const fine = useFinePointer();
  useEffect(() => {
    const el = ref.current;
    if (!el || !fine) return;
    let raf = 0;
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        el.style.setProperty("--mx", `${e.clientX - r.left}px`);
        el.style.setProperty("--my", `${e.clientY - r.top}px`);
      });
    };
    el.addEventListener("pointermove", move);
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("pointermove", move);
    };
  }, [fine]);
  return ref;
}
