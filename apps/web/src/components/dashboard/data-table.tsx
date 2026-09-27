"use client";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function DataTable({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-lg)] border border-[var(--color-border)]">
      <table className={cn("w-full min-w-[640px] text-sm", className)}>{children}</table>
    </div>
  );
}
export function THead({ children }: { children: ReactNode }) {
  return <thead className="border-b border-[var(--color-border)] bg-[var(--color-surface)]/60 text-left text-xs font-medium uppercase tracking-wider text-[var(--color-text-subtle)]">{children}</thead>;
}
export function TR({ children, onClick, className }: { children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <tr onClick={onClick} className={cn("border-b border-[var(--color-border)]/60 last:border-0", onClick && "cursor-pointer transition-colors hover:bg-[var(--color-surface)]/70", className)}>
      {children}
    </tr>
  );
}
export function TH({ children, className }: { children?: ReactNode; className?: string }) {
  return <th className={cn("px-4 py-2.5 font-medium", className)}>{children}</th>;
}
export function TD({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cn("px-4 py-3 align-middle", className)}>{children}</td>;
}
