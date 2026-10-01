"use client";

import { ArrowUp } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";

export interface TocItem {
  id: string;
  title: string;
}

/** Offset (px) below the sticky top bar at which a section counts as "being read". */
const READ_LINE = 140;

/**
 * Sticky "On this page" navigation for the legal documents. Highlights the section currently being read
 * (scroll position only — no animation), marking it with `aria-current="location"`.
 */
export function LegalToc({ items }: { items: TocItem[] }) {
  const [active, setActive] = useState<string | undefined>(items[0]?.id);

  useEffect(() => {
    let raf = 0;
    const update = () => {
      raf = 0;
      let current = items[0]?.id;
      for (const it of items) {
        const el = document.getElementById(it.id);
        if (el && el.getBoundingClientRect().top - READ_LINE <= 0) current = it.id;
      }
      // At the very bottom the last (often short) section is the one being read.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) current = items[items.length - 1]?.id;
      setActive(current);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [items]);

  return (
    <nav aria-label="On this page">
      <p className="mb-3 text-eyebrow text-subtle">On this page</p>
      <ol className="border-l border-border">
        {items.map((s, i) => {
          const on = active === s.id;
          return (
            <li key={s.id}>
              <a
                href={`#${s.id}`}
                aria-current={on ? "location" : undefined}
                className={cn(
                  "-ml-px flex gap-3 border-l py-1.5 pl-4 pr-2 text-[13px] leading-snug transition-colors duration-200",
                  "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--ring)]",
                  on ? "border-accent text-fg" : "border-transparent text-muted hover:border-border-strong hover:text-fg",
                )}
              >
                <span className={cn("tabular w-5 shrink-0 font-mono text-[11px] leading-5", on ? "text-accent-strong" : "text-subtle")}>
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="min-w-0">{s.title}</span>
              </a>
            </li>
          );
        })}
      </ol>
      <a
        href="#main"
        className="mt-5 inline-flex min-h-9 items-center gap-1.5 rounded-md text-xs text-subtle transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
      >
        <ArrowUp className="h-3.5 w-3.5" aria-hidden /> Back to top
      </a>
    </nav>
  );
}
