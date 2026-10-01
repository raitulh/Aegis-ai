"use client";

import * as RT from "@radix-ui/react-tabs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Tabs({ value, onValueChange, defaultValue, tabs, children }: {
  value?: string;
  onValueChange?: (v: string) => void;
  defaultValue?: string;
  tabs: { value: string; label: ReactNode; count?: number }[];
  children: ReactNode;
}) {
  return (
    <RT.Root value={value} onValueChange={onValueChange} defaultValue={defaultValue ?? tabs[0]?.value}>
      <RT.List className="flex gap-1 overflow-x-auto border-b border-border" aria-label="Sections">
        {tabs.map((t) => (
          <RT.Trigger
            key={t.value}
            value={t.value}
            className="-mb-px shrink-0 border-b-2 border-transparent px-3 py-2.5 text-sm font-medium text-muted hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg"
          >
            {t.label}
            {t.count !== undefined ? <span className="ml-1.5 rounded-full bg-surface-3 px-1.5 text-xs text-subtle">{t.count}</span> : null}
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

/** Route-based tabs (each tab is its own URL). */
export function NavTabs({ items, className }: { items: { href: string; label: ReactNode; exact?: boolean; hidden?: boolean }[]; className?: string }) {
  const pathname = usePathname();
  return (
    <nav className={cn("flex gap-1 overflow-x-auto border-b border-border", className)} aria-label="Sections">
      {items
        .filter((i) => !i.hidden)
        .map((i) => {
          const active = i.exact ? pathname === i.href : pathname === i.href || pathname.startsWith(i.href + "/");
          return (
            <Link
              key={i.href}
              href={i.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "-mb-px shrink-0 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
              )}
            >
              {i.label}
            </Link>
          );
        })}
    </nav>
  );
}
