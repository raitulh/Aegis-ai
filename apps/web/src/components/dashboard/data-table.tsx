"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Accessible data table. Rows that navigate render a real link in their first cell (keyboard and
 * screen-reader friendly) and the whole row is clickable for pointer users.
 */
export function DataTable({ children, className, caption }: { children: ReactNode; className?: string; caption?: string }) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-lg)] border border-[var(--color-border)]">
      <table className={cn("tabular w-full min-w-[640px] text-sm", className)}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        {children}
      </table>
    </div>
  );
}
export function THead({ children }: { children: ReactNode }) {
  return <thead className="border-b border-[var(--color-border)] bg-[var(--color-surface)]/60 text-left text-xs font-medium uppercase tracking-wider text-[var(--color-text-subtle)]">{children}</thead>;
}
export function TR({ children, href, className, selected }: { children: ReactNode; href?: string; className?: string; selected?: boolean }) {
  return (
    <tr
      aria-selected={selected}
      className={cn(
        "relative border-b border-[var(--color-border)]/60 last:border-0",
        href && "transition-colors hover:bg-[var(--color-surface)]/70 [&_td:first-child_a]:after:absolute [&_td:first-child_a]:after:inset-0",
        selected && "bg-[var(--color-accent-dim)]/40",
        className,
      )}
    >
      {children}
    </tr>
  );
}
export function TH({ children, className, scope = "col" }: { children?: ReactNode; className?: string; scope?: "col" | "row" }) {
  return (
    <th scope={scope} className={cn("px-4 py-2.5 font-medium", className)}>
      {children}
    </th>
  );
}
export function TD({ children, className, title }: { children?: ReactNode; className?: string; title?: string }) {
  return (
    <td className={cn("px-4 py-3 align-middle", className)} title={title}>
      {children}
    </td>
  );
}
/** Primary cell link: the row's accessible navigation target. */
export function RowLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="font-medium text-[var(--color-text)] hover:text-[var(--color-accent-bright)]">
      {children}
    </Link>
  );
}
