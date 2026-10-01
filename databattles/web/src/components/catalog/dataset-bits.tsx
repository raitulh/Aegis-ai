"use client";

import { useQuery } from "@tanstack/react-query";
import { BookText, Columns3, Eye, File, FileArchive, FileImage, FileJson, FileSpreadsheet, FileText, NotebookPen, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/misc";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatNumber, titleCase } from "@/lib/format";
import type { DataDictionaryEntry, DatasetFile, DatasetVersion, FilePreview } from "./types";

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
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <code className="whitespace-nowrap font-mono text-xs text-muted" title={`SHA-256 ${value}`}>
        <span className="text-subtle">sha256 </span>
        {value.slice(0, 12)}…
      </code>
      <CopyButton value={value} label="Copy" className="px-1.5 py-0.5 max-sm:min-h-9 max-sm:px-2.5" />
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
  if (!entries.length) {
    return (
      <div className="flex items-center gap-3 rounded-[var(--radius-lg)] border border-dashed border-border-strong bg-surface/40 px-4 py-5 text-sm text-subtle">
        <BookText className="h-4 w-4 shrink-0" aria-hidden />
        No data dictionary for this version.
      </div>
    );
  }
  return (
    <Table>
      <THead>
        <tr>
          <TH className="w-12 text-right">#</TH>
          <TH>Column</TH>
          <TH>Type</TH>
          <TH>Description</TH>
        </tr>
      </THead>
      <TBody>
        {entries.map((e, i) => (
          <TR key={`${e.column}-${i}`}>
            <TD className="tabular w-12 text-right font-mono text-[11px] text-subtle">{i + 1}</TD>
            <TD className="whitespace-nowrap font-mono text-xs font-medium text-fg">{e.column}</TD>
            <TD>
              {e.type ? (
                <span className="inline-flex rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-muted">{e.type}</span>
              ) : (
                <span className="text-subtle">—</span>
              )}
            </TD>
            <TD className="min-w-48 text-muted">{e.description || "—"}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

/* ------------------------------------------------------------------------------------------------ files */

const EXT_ICON: Record<string, typeof File> = {
  csv: FileSpreadsheet,
  tsv: FileSpreadsheet,
  parquet: FileSpreadsheet,
  json: FileJson,
  jsonl: FileJson,
  ipynb: NotebookPen,
  zip: FileArchive,
  png: FileImage,
  jpg: FileImage,
  jpeg: FileImage,
  md: FileText,
  txt: FileText,
};

/** File-type glyph chosen from the extension; tinted by the file's role (data / sample submission / other). */
export function FileGlyph({ filename, kind, className }: { filename: string; kind: string; className?: string }) {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  const Icon = EXT_ICON[ext] ?? File;
  const tone = kind === "data" ? "bg-cyan-soft text-cyan" : kind === "sample_submission" ? "bg-accent-soft text-accent-strong" : "bg-surface-2 text-muted";
  return (
    <span
      aria-hidden
      className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border shadow-[inset_0_1px_0_var(--hairline-highlight)]", tone, className)}
    >
      <Icon className="h-4 w-4" />
    </span>
  );
}

function FileFact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">{label}</dt>
      <dd className="tabular text-muted">{children}</dd>
    </div>
  );
}

/**
 * One file in a hairline-divided list: glyph, name, role, size/rows/checksum/scan facts and caller-supplied
 * actions. Place inside a `<ul>`; purely presentational.
 */
export function FileRow({ file, selected, extra, actions }: { file: DatasetFile; selected?: boolean; extra?: ReactNode; actions?: ReactNode }) {
  return (
    <li
      className={cn(
        "relative flex flex-col gap-3 px-4 py-3.5 transition-colors duration-200 sm:flex-row sm:items-center sm:gap-4 sm:px-5",
        selected ? "bg-accent-soft/60" : "hover:bg-surface-2/50",
      )}
    >
      {selected ? <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-brand" /> : null}
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <FileGlyph filename={file.filename} kind={file.kind} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="break-all font-mono text-[13px] font-medium text-fg">{file.filename}</span>
            <FileKindBadge kind={file.kind} />
          </div>
          <dl className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
            <FileFact label="Size">{formatBytes(file.size_bytes)}</FileFact>
            <FileFact label="Rows">{file.row_count !== null ? formatNumber(file.row_count) : "—"}</FileFact>
            <div>
              <dt className="sr-only">SHA-256 checksum</dt>
              <dd><Sha value={file.sha256} /></dd>
            </div>
            <div>
              <dt className="sr-only">Malware scan</dt>
              <dd><ScanBadge status={file.scan_status} /></dd>
            </div>
            {extra}
          </dl>
        </div>
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-1.5 pl-12 sm:pl-0">{actions}</div> : null}
    </li>
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
    <section
      aria-label={q.data ? `Preview of ${q.data.filename}` : "File preview"}
      className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card animate-rise"
    >
      <div className="flex items-start justify-between gap-4 border-b border-border bg-bg-elevated/60 px-4 py-3 sm:px-5">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-cyan-soft text-cyan" aria-hidden>
            <Eye className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <h3 className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm font-semibold text-fg">
              Preview
              {q.data ? <span className="truncate font-mono text-[13px] font-medium text-muted">{q.data.filename}</span> : null}
            </h3>
            {q.data?.preview ? (
              <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-subtle">
                <span className="tabular">
                  First {q.data.preview.rows.length} rows{q.data.row_count !== null ? ` of ${formatNumber(q.data.row_count)}` : ""}
                </span>
                <span className="tabular inline-flex items-center gap-1">
                  <Columns3 className="h-3 w-3" aria-hidden />
                  {q.data.preview.columns.length} columns
                </span>
                {q.data.preview.truncated_columns ? <span>extra columns hidden</span> : null}
              </p>
            ) : null}
          </div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close preview"><X className="h-4 w-4" /></Button>
      </div>
      <div className="p-3 sm:p-4">
        {q.isPending ? (
          <SkeletonRows rows={5} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !q.data.preview || !q.data.preview.columns.length ? (
          <EmptyState title="No preview available" description="Previews are generated for CSV and TSV files only." />
        ) : (
          <div className="max-h-[28rem] overflow-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated">
            <table className="w-full border-collapse font-mono text-xs">
              <caption className="sr-only">Preview of {q.data.filename}</caption>
              <thead className="sticky top-0 z-10 bg-surface-2 shadow-[0_1px_0_var(--border)]">
                <tr>
                  <th scope="col" className="px-3 py-2 text-right font-medium text-subtle">#</th>
                  {q.data.preview.columns.map((c, i) => (
                    <th key={i} scope="col" className="whitespace-nowrap px-3 py-2 text-left font-semibold text-fg">{c || <span className="text-subtle">(blank)</span>}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {q.data.preview.rows.map((row, r) => (
                  <tr key={r} className="transition-colors even:bg-surface/60 hover:bg-surface-2/70">
                    <td className="tabular px-3 py-1.5 text-right text-subtle">{r + 1}</td>
                    {q.data!.preview!.columns.map((_, c) => (
                      <td key={c} className="max-w-64 truncate whitespace-nowrap px-3 py-1.5 text-muted" title={row[c] ?? ""}>{row[c] ?? ""}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------------------------------------ page layout */

/**
 * Sticky in-page section navigation with scroll-spy. Links are plain anchors (keyboard and no-JS friendly);
 * the active section gets `aria-current`. Sits under the global header like `NavTabs sticky`.
 */
export function SectionNav({ items, label = "On this page", className, end }: { items: { id: string; label: ReactNode; count?: number }[]; label?: string; className?: string; end?: ReactNode }) {
  const [active, setActive] = useState<string | undefined>(items[0]?.id);
  const ids = items.map((i) => i.id).join("|");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const els = ids
      .split("|")
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => Boolean(el));
    if (!els.length) return;
    const visible = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) visible.set(e.target.id, e.isIntersecting);
        const first = els.find((el) => visible.get(el.id));
        if (first) setActive(first.id);
      },
      { rootMargin: "-130px 0px -50% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ids]);

  return (
    <nav
      aria-label={label}
      className={cn(
        "sticky top-14 z-30 -mx-4 flex items-center gap-3 border-b border-border bg-[var(--glass-strong)] px-4 backdrop-blur-xl sm:-mx-6 sm:px-6",
        "lg:top-[4.75rem] lg:mx-0 lg:rounded-[var(--radius-md)] lg:border lg:px-2 lg:shadow-card",
        className,
      )}
    >
      <ul className="flex min-w-0 flex-1 gap-0.5 overflow-x-auto [scrollbar-width:none]">
        {items.map((i) => (
          <li key={i.id} className="shrink-0">
            <a
              href={`#${i.id}`}
              aria-current={active === i.id ? "true" : undefined}
              onClick={() => setActive(i.id)}
              className={cn(
                "relative isolate -mb-px inline-flex h-11 items-center gap-1.5 px-3 text-[13px] font-medium text-muted transition-colors duration-200 hover:text-fg",
                "before:absolute before:inset-x-1 before:inset-y-2 before:-z-10 before:rounded-md before:bg-surface-2 before:opacity-0 before:transition-opacity hover:before:opacity-100",
                "after:absolute after:inset-x-2.5 after:bottom-0 after:h-0.5 after:scale-x-0 after:rounded-full after:bg-brand after:opacity-0 after:transition-[transform,opacity] after:duration-300 after:ease-out-expo",
                "aria-[current=true]:text-fg aria-[current=true]:after:scale-x-100 aria-[current=true]:after:opacity-100",
                "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
              )}
            >
              {i.label}
              {i.count !== undefined ? <span className="tabular rounded-full bg-surface-3 px-1.5 py-px text-[10.5px] text-subtle">{i.count}</span> : null}
            </a>
          </li>
        ))}
      </ul>
      {end ? <div className="hidden shrink-0 md:block">{end}</div> : null}
    </nav>
  );
}

/** Gap-px tile grid of `MetaItem`s (pass exactly as many items as fill the rows: 2 / 3 / 6 columns). */
export function FactGrid({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-2 gap-px bg-border sm:grid-cols-3 lg:grid-cols-6 [&>*]:bg-surface [&>*]:px-5 [&>*]:py-4", className)}>
      {children}
    </dl>
  );
}

/** Titled in-page section for detail pages (anchor target for `SectionNav`; offset clears both sticky bars). */
export function DetailBlock({
  id,
  eyebrow,
  title,
  description,
  action,
  children,
  className,
}: {
  id: string;
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={cn("scroll-mt-14", className)}>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          {eyebrow ? <p className="mb-1.5 text-eyebrow text-subtle">{eyebrow}</p> : null}
          <h2 id={`${id}-title`} className="text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">{title}</h2>
          {description ? <div className="mt-1 text-sm text-muted">{description}</div> : null}
        </div>
        {action ? <div className="min-w-0 shrink-0">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Removable chips for the filters currently applied to a listing (each chip clears one URL filter). */
export function ActiveFilters({ chips }: { chips: { key: string; label: ReactNode; onRemove: () => void }[] }) {
  if (!chips.length) return null;
  return (
    <ul className="-mt-2 mb-5 flex flex-wrap items-center gap-1.5" aria-label="Active filters">
      {chips.map((c) => (
        <li key={c.key}>
          <button
            type="button"
            onClick={c.onRemove}
            className={cn(
              "inline-flex h-9 items-center gap-1.5 rounded-full border sm:h-7 border-[color-mix(in_oklab,var(--accent)_35%,var(--border))] bg-accent-soft pl-2.5 pr-1.5 text-xs font-medium text-accent-strong",
              "transition-colors hover:border-[color-mix(in_oklab,var(--accent)_60%,var(--border))] animate-pop",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
            )}
          >
            <span className="max-w-56 truncate">{c.label}</span>
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-accent-strong/15" aria-hidden><X className="h-3 w-3" /></span>
            <span className="sr-only">Remove filter</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Numbered "how this works" strip for creation flows; `current` marks the step the page is on. */
export function FlowSteps({ steps, current = 0, label }: { steps: { title: string; text: string }[]; current?: number; label: string }) {
  return (
    <ol aria-label={label} className="mb-10 grid gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card sm:grid-cols-3">
      {steps.map((s, i) => {
        const on = i === current;
        return (
          <li key={s.title} aria-current={on ? "step" : undefined} className={cn("relative bg-surface px-4 py-3 sm:px-5 sm:py-3.5", on && "bg-surface-2")}>
            {on ? <span aria-hidden className="absolute inset-x-0 top-0 h-0.5 bg-brand" /> : null}
            <p className={`flex items-center gap-2 text-eyebrow ${on ? "text-accent-strong" : "text-subtle"}`}>
              <span className="tabular">{String(i + 1).padStart(2, "0")}</span>
              <span aria-hidden className="h-px w-4 bg-border-strong" />
              {on ? "You're here" : i < current ? "Done" : "Next"}
            </p>
            <p className="mt-1.5 text-sm font-medium text-fg">{s.title}</p>
            <p className={cn("mt-0.5 text-xs leading-relaxed text-muted", !on && "hidden sm:block")}>{s.text}</p>
          </li>
        );
      })}
    </ol>
  );
}
