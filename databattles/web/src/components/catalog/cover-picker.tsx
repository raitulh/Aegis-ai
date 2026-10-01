"use client";

import { Check } from "lucide-react";

import { Cover } from "@/components/ui/misc";
import { cn } from "@/lib/cn";

export const COVER_STYLES = ["aurora", "nebula", "circuit", "dunes", "mono", "sunrise"] as const;

/** Radio group of generated cover styles (keyboard: arrow keys via native radios). */
export function CoverPicker({ value, onChange, name = "cover_style" }: { value: string; onChange: (v: string) => void; name?: string }) {
  return (
    <div role="radiogroup" aria-label="Cover style" className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {COVER_STYLES.map((style) => {
        const selected = value === style;
        return (
          <label
            key={style}
            className={cn(
              "group relative cursor-pointer overflow-hidden rounded-[var(--radius-md)] border-2 transition-colors",
              "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--ring)]",
              selected ? "border-accent" : "border-transparent hover:border-border-strong",
            )}
          >
            <input type="radio" name={name} value={style} checked={selected} onChange={() => onChange(style)} className="sr-only" />
            <Cover style={style} className="h-16 w-full" />
            <span className="flex items-center justify-between bg-surface-2 px-2 py-1 text-xs capitalize text-muted">
              {style}
              {selected ? <Check className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> : null}
            </span>
          </label>
        );
      })}
    </div>
  );
}
