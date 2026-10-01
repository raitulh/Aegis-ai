"use client";

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/misc";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatNumber, titleCase } from "@/lib/format";
import type { DataDictionaryEntry, DatasetVersion, FilePreview } from "./types";

export const KIND_LABELS: Record<string, string> = {
  data: "Data",
  sample_submission: "Sample submission",
  notebook: "Notebook",
  documentation: "Documentation",
};

export function FileKindBadge({ kind }: { kind: string }) {
  return <Badge tone={kind === "data" ? "info" : "outline"}>{KIND_LABELS[kind] ?? titleCase(kind)}</Badge>;
}

const SCAN: Record<string, { tone: "success" | "neutral" | "warning" | "danger"; label: string; title: string }> = {
  clean: { tone: "success", label: "Scanned clean", title: "The malware scanner found no threats." },
  skipped: { tone: "neutral", label: "Not scanned", title: "Malware scanning is not enabled on this deployment." },
  error: { tone: "warning", label: "Scan error", title: "The scanner could not check this file." },
  infected: { tone: "danger", label: "Blocked", title: "The file failed the malware scan and cannot be downloaded." },
};

export function ScanBadge({ status }: { status: string }) {
  const s = SCAN[status] ?? { tone: "neutral" as const, label: titleCase(status), title: status };
  return <Badge tone={s.tone} title={s.title}>{s.label}</Badge>;
}

const VERSION_TONE: Record<string, "neutral" | "success" | "warning"> = { draft: "warning", published: "success", archived: "neutral" };

export function VersionStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={VERSION_TONE[status] ?? "neutral"}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {titleCase(status)}
    </Badge>
  );
}

export function Sha({ value }: { value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <code className="font-mono text-xs text-muted" title={`SHA-256 ${value}`}>{value.slice(0, 12)}…</code>
      <CopyButton value={value} label="Copy" className="px-1.5 py-0.5" />
    </span>
  );
}

export function toDictionary(v: DatasetVersion | null | undefined): DataDictionaryEntry[] {
  return (v?.data_dictionary ?? []).map((d) => ({
    column: String(d.column ?? ""),
    type: String(d.type ?? ""),
    description: String(d.description ?? ""),
  }));
}

export function DataDictionaryTable({ entries }: { entries: DataDictionaryEntry[] }) {
  if (!entries.length) return <p className="text-sm text-subtle">No data dictionary for this version.</p>;
  return (
    <Table>
      <THead>
        <tr>
          <TH>Column</TH>
          <TH>Type</TH>
          <TH>Description</TH>
        </tr>
      </THead>
      <TBody>
        {entries.map((e, i) => (
          <TR key={`${e.column}-${i}`}>
            <TD className="font-mono text-xs text-fg">{e.column}</TD>
            <TD className="font-mono text-xs text-muted">{e.type || "—"}</TD>
            <TD className="text-muted">{e.description || "—"}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

/** GET /datasets/{slug}/files/{id}/preview — first rows of a CSV/TSV, parsed server-side at upload. */
export function FilePreviewPanel({ slug, fileId, onClose }: { slug: string; fileId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["datasets", slug, "preview", fileId],
    queryFn: () => get<FilePreview>(`/datasets/${slug}/files/${fileId}/preview`),
    staleTime: 5 * 60_000,
  });
  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={q.data ? <>Preview · <span className="font-mono">{q.data.filename}</span></> : "Preview"}
        description={
          q.data?.preview
            ? `First ${q.data.preview.rows.length} rows${q.data.row_count !== null ? ` of ${formatNumber(q.data.row_count)}` : ""}${q.data.preview.truncated_columns ? " · extra columns hidden" : ""}`
            : undefined
        }
        action={<Button variant="ghost" size="icon" onClick={onClose} aria-label="Close preview"><X className="h-4 w-4" /></Button>}
      />
      <CardBody>
        {q.isPending ? (
          <SkeletonRows rows={5} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !q.data.preview || !q.data.preview.columns.length ? (
          <EmptyState title="No preview available" description="Previews are generated for CSV and TSV files only." />
        ) : (
          <div className="max-h-[28rem] overflow-auto rounded-[var(--radius-md)] border border-border">
            <table className="w-full border-collapse font-mono text-xs">
              <caption className="sr-only">Preview of {q.data.filename}</caption>
              <thead className="sticky top-0 bg-surface-2">
                <tr>
                  <th scope="col" className="border-b border-border px-3 py-2 text-left font-medium text-subtle">#</th>
                  {q.data.preview.columns.map((c, i) => (
                    <th key={i} scope="col" className="whitespace-nowrap border-b border-border px-3 py-2 text-left font-semibold text-fg">{c || <span className="text-subtle">(blank)</span>}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border bg-surface">
                {q.data.preview.rows.map((row, r) => (
                  <tr key={r}>
                    <td className="px-3 py-1.5 text-subtle tabular-nums">{r + 1}</td>
                    {q.data!.preview!.columns.map((_, c) => (
                      <td key={c} className="max-w-64 truncate whitespace-nowrap px-3 py-1.5 text-muted" title={row[c] ?? ""}>{row[c] ?? ""}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
