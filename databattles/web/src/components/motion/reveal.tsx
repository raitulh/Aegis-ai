"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";

import { cn } from "@/lib/cn";

type RevealTag = "div" | "section" | "article" | "li" | "ul" | "ol" | "span" | "header" | "aside";

/**
 * Fades and rises its children into place the first time they scroll into view.
 *
 * Content that is already on screen when the page loads is left untouched (no flash), and the element
 * stays fully visible when JavaScript, IntersectionObserver or motion is unavailable — the hidden state
 * is only applied from an effect, and `prefers-reduced-motion` / the in-app setting neutralise it in CSS.
 */
export function Reveal({
  as = "div",
  children,
  className,
  delay = 0,
  style,
  id,
}: {
  as?: RevealTag;
  children: ReactNode;
  className?: string;
  /** Delay in ms, for staggering siblings. */
  delay?: number;
  style?: CSSProperties;
  id?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);
  const [state, setState] = useState<"idle" | "hidden" | "shown">("idle");

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    // Already visible on first paint: never hide it.
    if (el.getBoundingClientRect().top < window.innerHeight * 0.9) return;
    setState("hidden");
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setState("shown");
          io.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // Typed as "div" for simplicity; the rendered tag is whatever `as` says.
  const Tag = as as "div";
  return (
    <Tag
      ref={ref as RefObject<HTMLDivElement | null>}
      id={id}
      className={cn("reveal", className)}
      data-reveal={state === "idle" ? undefined : state}
      style={state === "shown" && delay ? { ...style, transitionDelay: `${delay}ms` } : style}
    >
      {children}
    </Tag>
  );
}
