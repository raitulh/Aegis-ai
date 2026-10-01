"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * Motion tokens shared by CSS (see globals.css `--dur-*` / `--ease-*`) and JS-driven effects.
 * Keep durations short and purposeful: interactions respond in ≤200ms, entrances settle in ≤640ms.
 */
export const motion = {
  duration: { fast: 120, base: 200, slow: 360, slower: 640 },
  ease: {
    out: "cubic-bezier(0.16, 1, 0.3, 1)",
    inOut: "cubic-bezier(0.65, 0, 0.35, 1)",
    spring: "cubic-bezier(0.34, 1.4, 0.64, 1)",
  },
  /** Stagger between siblings in a reveal sequence. */
  stagger: 70,
} as const;

/** easeOutExpo for rAF-driven tweens (count-up, camera easing). */
export const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

function readReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  if (document.documentElement.dataset.motion === "reduced") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * True when the viewer asked for less motion — either the OS setting or the in-app preference
 * (`data-motion="reduced"` on <html>, set from Settings → Appearance). Starts `false` on the server.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const update = () => setReduced(readReducedMotion());
    update();
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    mq.addEventListener("change", update);
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-motion"] });
    return () => {
      mq.removeEventListener("change", update);
      mo.disconnect();
    };
  }, []);
  return reduced;
}

/** True on devices with a fine pointer that can hover (desktop). Touch devices skip pointer effects. */
export function useFinePointer(): boolean {
  const [fine, setFine] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(hover: hover) and (pointer: fine)");
    const update = () => setFine(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return fine;
}

/**
 * Observes an element and reports whether it is (or has been, with `once`) within the viewport.
 * Falls back to `true` where IntersectionObserver is unavailable so content is never stuck hidden.
 */
export function useInView<T extends Element>(
  { rootMargin = "0px 0px -10% 0px", once = true, threshold = 0 }: { rootMargin?: string; once?: boolean; threshold?: number } = {},
): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          if (once) io.disconnect();
        } else if (!once) {
          setInView(false);
        }
      },
      { rootMargin, threshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [rootMargin, once, threshold]);
  return [ref, inView];
}
