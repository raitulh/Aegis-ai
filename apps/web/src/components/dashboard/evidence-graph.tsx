"use client";
import Link from "next/link";
import { FileText, Gauge, ScrollText, ShieldAlert, Wrench } from "lucide-react";
import { titleCase } from "@/lib/utils";

const TARGET_META: Record<string, { icon: typeof FileText; label: string; href: (id: string) => string }> = {
  finding: { icon: ShieldAlert, label: "Finding", href: (id) => `/dashboard/findings/${id}` },
  test_result: { icon: Gauge, label: "Test result", href: () => "#" },
  control: { icon: ScrollText, label: "Control", href: () => "#" },
  remediation: { icon: Wrench, label: "Remediation", href: () => "#" },
};

/** 2D evidence graph: the evidence node and everything it links to (Policy → Control → Test → Finding → Evidence → Remediation). */
export function EvidenceGraph({ data }: { data: { evidence: { id: string; kind: string; title: string }; links: { target_type: string; target_id: string; relation: string }[] } }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-[var(--radius)] border border-[var(--color-accent)]/40 bg-[var(--color-accent-dim)] px-3 py-2">
        <FileText className="h-4 w-4 text-[var(--color-accent-bright)]" />
        <span className="text-sm font-medium">{data.evidence.title}</span>
        <span className="ml-auto text-[10px] uppercase text-[var(--color-text-subtle)]">{titleCase(data.evidence.kind)}</span>
      </div>
      {data.links.length ? (
        <div className="ml-4 space-y-2 border-l border-[var(--color-border)] pl-4">
          {data.links.map((l, i) => {
            const meta = TARGET_META[l.target_type] ?? { icon: FileText, label: titleCase(l.target_type), href: () => "#" };
            const Icon = meta.icon;
            return (
              <Link key={i} href={meta.href(l.target_id)} className="flex items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm hover:border-[var(--color-border-strong)]">
                <Icon className="h-4 w-4 text-[var(--color-text-subtle)]" />
                <span>{meta.label}</span>
                <span className="ml-auto text-[10px] text-[var(--color-text-subtle)]">{l.relation}</span>
              </Link>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-subtle)]">This evidence is not yet linked to a finding.</p>
      )}
    </div>
  );
}
