"use client";

import * as RT from "@radix-ui/react-tabs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Shared tab styling: a gradient underline grows from the centre on the active tab, and hover gives a
 * soft pill. Works for Radix tabs (data-state) and route tabs (aria-current).
 */
const TAB =
  "relative shrink-0 rounded-t-md px-3 py-2.5 text-sm font-medium text-muted transition-colors duration-200 hover:text-fg " +
  "before:absolute before:inset-x-1 before:inset-y-1.5 before:-z-10 before:rounded-md before:bg-surface-2 before:opacity-0 before:transition-opacity hover:before:opacity-100 " +
  "after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:bg-brand after:opacity-0 after:transition-[scale,opacity] after:duration-300 after:ease-out-expo after:scale-x-0 " +
  "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] isolate";

export function Tabs({ value, onValueChange, defaultValue, tabs, children }: {
  value?: string;
  onValueChange?: (v: string) => void;
  defaultValue?: string;
  tabs: { value: string; label: ReactNode; count?: number }[];
  children: ReactNode;
}) {
  return (
    <RT.Root value={value} onValueChange={onValueChange} defaultValue={defaultValue ?? tabs[0]?.value}>
      <RT.List className="flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--border)] [scrollbar-width:none]" aria-label="Sections">
        {tabs.map((t) => (
          <RT.Trigger
            key={t.value}
            value={t.value}
            className={cn(TAB, "data-[state=active]:text-fg data-[state=active]:after:scale-x-100 data-[state=active]:after:opacity-100")}
          >
            {t.label}
            {t.count !== undefined ? <span className="tabular ml-1.5 rounded-full bg-surface-2 px-1.5 py-px text-[11px] text-muted">{t.count}</span> : null}
          </RT.Trigger>
        ))}
      </RT.List>
      {children}
    </RT.Root>
  );
}

export const TabPanel = ({ value, children, className }: { value: string; children: ReactNode; className?: string }) => (
  <RT.Content value={value} className={cn("pt-6 focus:outline-none", className)}>
    {children}
  </RT.Content>
);

/**
 * Route-based tabs (each tab is its own URL). `sticky` pins the bar under the global header on a glass
 * strip so section navigation stays in reach on long pages.
 */
export function NavTabs({
  items,
  className,
  sticky = false,
}: {
  items: { href: string; label: ReactNode; exact?: boolean; hidden?: boolean }[];
  className?: string;
  sticky?: boolean;
}) {
  const pathname = usePathname();
  return (
    <nav
      className={cn(
        "flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--border)] [scrollbar-width:none]",
        sticky && "sticky top-14 z-30 -mx-4 bg-[var(--glass-strong)] px-4 backdrop-blur-xl sm:-mx-6 sm:px-6 lg:top-[4.25rem] lg:mx-0 lg:rounded-t-[var(--radius-md)] lg:px-1",
        className,
      )}
      aria-label="Sections"
    >
      {items
        .filter((i) => !i.hidden)
        .map((i) => {
          const active = i.exact ? pathname === i.href : pathname === i.href || pathname.startsWith(i.href + "/");
          return (
            <Link
              key={i.href}
              href={i.href}
              aria-current={active ? "page" : undefined}
              className={cn(TAB, active && "text-fg after:scale-x-100 after:opacity-100")}
            >
              {i.label}
            </Link>
          );
        })}
    </nav>
  );
}
