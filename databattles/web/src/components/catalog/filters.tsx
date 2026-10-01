"use client";

import { Check, Search, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Input, Select } from "@/components/ui/form";
import { cn } from "@/lib/cn";
import { useDebounced } from "@/lib/hooks";

/**
 * Debounced search box bound to a URL value. Local state keeps typing snappy; the committed value
 * is pushed after `delay` ms. External changes (back/forward, "clear filters") flow back in.
 */
export function SearchBox({
  value,
  onCommit,
  label,
  placeholder,
  className,
  delay = 350,
}: {
  value: string;
  onCommit: (v: string) => void;
  label: string;
  placeholder?: string;
  className?: string;
  delay?: number;
}) {
  const id = useId();
  const [text, setText] = useState(value);
  const debounced = useDebounced(text, delay);
  const committed = useRef(value);

  useEffect(() => {
    // URL changed from outside (navigation / reset): adopt it.
    if (value !== committed.current) {
      committed.current = value;
      setText(value);
    }
  }, [value]);

  useEffect(() => {
    const next = debounced.trim();
    if (next !== committed.current) {
      committed.current = next;
      onCommit(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  return (
    <div className={cn("relative min-w-0", className)}>
      <label htmlFor={id} className="sr-only">{label}</label>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
      <Input
        id={id}
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder ?? label}
        className="pl-9 pr-9"
        maxLength={80}
      />
      {text ? (
        <button
          type="button"
          onClick={() => setText("")}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-subtle hover:text-fg"
          aria-label="Clear search"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/** A labelled <select> sized for filter bars. */
export function FilterSelect({
  label,
  value,
  onChange,
  children,
  className,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs font-medium text-subtle">{label}</label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </Select>
    </div>
  );
}

/** Toggle button for boolean filters (aria-pressed conveys state; not color alone). */
export function ToggleChip({
  pressed,
  onChange,
  children,
  icon,
}: {
  pressed: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={() => onChange(!pressed)}
      className={cn(
        "inline-flex h-10 items-center gap-1.5 rounded-[var(--radius-md)] border px-3 text-sm font-medium transition-[background-color,border-color,color,box-shadow] duration-200",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] active:scale-[0.98]",
        pressed
          ? "border-[color-mix(in_oklab,var(--accent)_55%,transparent)] bg-accent-soft text-accent-strong shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_10%,transparent)]"
          : "border-border bg-bg-elevated/60 text-muted hover:border-border-strong hover:text-fg",
      )}
    >
      {icon}
      {children}
      {pressed ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
    </button>
  );
}

/** Responsive filter row wrapper. */
export function FilterBar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      role="search"
      className={cn(
        "relative mb-6 flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-glass p-3 shadow-card backdrop-blur-md sm:flex-row sm:flex-wrap sm:items-end",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** "3 results" line with an optional clear action. */
export function ResultSummary({ total, noun, onClear, active }: { total: number; noun: string; onClear?: () => void; active?: boolean }) {
  return (
    <div className="mb-4 flex items-center justify-between text-sm text-muted" aria-live="polite">
      <span className="flex items-center gap-2">
        <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
        <span className="tabular font-medium text-fg">{total.toLocaleString()}</span> {total === 1 ? noun : `${noun}s`}
      </span>
      {active && onClear ? (
        <button type="button" onClick={onClear} className="text-accent-strong hover:underline">
          Clear filters
        </button>
      ) : null}
    </div>
  );
}
