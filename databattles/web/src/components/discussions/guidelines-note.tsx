import { ArrowUpRight, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/cn";

/** Quiet reminder of the house rules (static copy) with a link to the full community guidelines. */
export function GuidelinesNote({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-[var(--radius-lg)] border border-border bg-surface/60 p-4", className)}>
      <p className="flex items-center gap-1.5 text-eyebrow text-subtle">
        <ShieldCheck className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> House rules
      </p>
      <ul className="mt-3 list-disc space-y-1.5 pl-4 text-xs leading-relaxed text-muted marker:text-subtle">
        <li>Be kind and credit others&apos; work.</li>
        <li>Never post competition test labels or private data.</li>
      </ul>
      <Link href="/guidelines" className="mt-3 inline-flex min-h-8 items-center gap-0.5 text-xs text-accent-strong hover:underline">
        Community guidelines <ArrowUpRight className="h-3 w-3" aria-hidden />
      </Link>
    </div>
  );
}
