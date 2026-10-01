"use client";

import { Check } from "lucide-react";

import { Cover } from "@/components/ui/misc";
import { cn } from "@/lib/cn";

export const COVER_STYLES = ["aurora", "nebula", "circuit", "dunes", "mono", "sunrise"] as const;

/** Radio group of generated cover styles (keyboard: arrow keys via native radios). */
export function CoverPicker({ value, onChange, name = "cover_style" }: { value: string; onChange: (v: string) => void; name?: string }) {
  return (
    <div role="radiogroup" aria-label="Cover style" className="grid grid-cols-3 gap-2.5 sm:grid-cols-6">
      {COVER_STYLES.map((style) => {
        const selected = value === style;
        return (
          <label
            key={style}
            className={cn(
              "group relative cursor-pointer overflow-hidden rounded-[var(--radius-md)] border bg-surface-2 transition-[border-color,box-shadow,transform] duration-200 ease-out-expo",
              "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--ring)]",
              selected
                ? "border-[color-mix(in_oklab,var(--accent)_70%,transparent)] shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_22%,transparent)]"
                : "border-border hover:-translate-y-0.5 hover:border-border-strong",
            )}
          >
            <input type="radio" name={name} value={style} checked={selected} onChange={() => onChange(style)} className="sr-only" />
            <Cover style={style} className="h-16 w-full" interactive>
              {selected ? (
                <span className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-accent-fill text-accent-fg shadow-[0_2px_8px_rgb(0_0_0/0.35)] animate-pop" aria-hidden>
                  <Check className="h-3 w-3" strokeWidth={3} />
                </span>
              ) : null}
            </Cover>
            <span className={cn("flex items-center justify-between px-2 py-1.5 text-xs capitalize", selected ? "font-medium text-fg" : "text-muted")}>
              {style}
            </span>
          </label>
        );
      })}
    </div>
  );
}
