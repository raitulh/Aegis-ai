"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Columns3, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/form";
import { FileDrop } from "@/components/ui/misc";
import { InlineNotice } from "@/components/ui/states";
import { get, idempotencyKey, upload } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useConfig, useUnsavedChangesWarning } from "@/lib/hooks";
import { KIND_LABELS } from "./dataset-bits";
import { describeError } from "./errors";
import type { DataDictionaryEntry, DatasetFile, FilePreview } from "./types";

export const DATASET_EXTENSIONS = [".csv", ".tsv", ".json", ".jsonl", ".parquet", ".txt", ".md", ".zip", ".ipynb", ".png", ".jpg", ".jpeg"];

interface PendingUpload {
  file: File;
  kind: string;
  key: string;
}

/**
 * Upload to POST /datasets/{slug}/versions/{v}/files (multipart: file + kind) with progress.
 * The Idempotency-Key is kept per chosen file so "Retry" never creates a duplicate.
 */
export function DatasetUploader({ slug, version, onUploaded }: { slug: string; version: number; onUploaded?: (f: DatasetFile) => void }) {
  const qc = useQueryClient();
  const config = useConfig();
  const maxMb = config.data?.limits.dataset_file_mb;
  const [kind, setKind] = useState("data");
  const [progress, setProgress] = useState<number | null>(null);
  const [pending, setPending] = useState<PendingUpload | null>(null);
  const [error, setError] = useState<{ title: string; message: string; code?: string } | null>(null);
  const [dropKey, setDropKey] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const busy = progress !== null;

  async function start(p: PendingUpload) {
    setPending(p);
    setError(null);
    setProgress(0);
    const form = new FormData();
    form.append("file", p.file);
    form.append("kind", p.kind);
    abort.current = new AbortController();
    try {
      const f = await upload<DatasetFile>(`/datasets/${slug}/versions/${version}/files`, form, {
        onProgress: setProgress,
        headers: { "Idempotency-Key": p.key },
        signal: abort.current.signal,
      });
      toast.success(`Uploaded ${f.filename}`, { description: `${formatBytes(f.size_bytes)}${f.row_count !== null ? ` · ${f.row_count.toLocaleString()} rows` : ""}` });
      setPending(null);
      setDropKey((k) => k + 1);
      await qc.invalidateQueries({ queryKey: ["datasets", slug] });
      onUploaded?.(f);
    } catch (e) {
      const d = describeError(e, "Upload failed");
      setError({ ...d, code: (e as { code?: string })?.code });
    } finally {
      setProgress(null);
      abort.current = null;
    }
  }

  const retryable = error && pending && !["invalid_file_type", "malware_detected", "duplicate_file", "quota_exceeded", "file_too_large", "payload_too_large", "version_immutable"].includes(error.code ?? "");

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-[14rem_1fr] sm:items-start">
        <Field label="File kind" hint="What the file is for.">
          {(p) => (
            <Select {...p} value={kind} onChange={(e) => setKind(e.target.value)} disabled={busy}>
              {Object.entries(KIND_LABELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </Select>
          )}
        </Field>
        <div>
          <FileDrop
            key={dropKey}
            accept={DATASET_EXTENSIONS.join(",")}
            maxBytes={maxMb ? maxMb * 1024 * 1024 : undefined}
            disabled={busy}
            progress={progress}
            hint={`${DATASET_EXTENSIONS.join(" ")}${maxMb ? ` · up to ${maxMb} MB per file` : ""} · files are scanned for malware`}
            onFile={(file) => start({ file, kind, key: idempotencyKey() })}
          />
          {busy ? (
            <div className="mt-2 flex items-center justify-between text-xs text-muted" aria-live="polite">
              <span>Uploading {pending?.file.name}… {Math.round((progress ?? 0) * 100)}%{progress !== null && progress >= 1 ? " · scanning and indexing" : ""}</span>
              <Button size="sm" variant="ghost" onClick={() => abort.current?.abort()} icon={<X className="h-3.5 w-3.5" aria-hidden />}>Cancel</Button>
            </div>
          ) : null}
        </div>
      </div>
      {error ? (
        <InlineNotice
          tone={error.code === "quota_exceeded" || error.code === "aborted" ? "warning" : "danger"}
          title={`${error.title}${pending ? ` — ${pending.file.name}` : ""}`}
          action={
            <div className="flex gap-2">
              {retryable ? (
                <Button size="sm" variant="secondary" onClick={() => pending && start(pending)} icon={<RotateCcw className="h-3.5 w-3.5" aria-hidden />}>Retry</Button>
              ) : null}
              <Button size="sm" variant="ghost" onClick={() => { setError(null); setPending(null); setDropKey((k) => k + 1); }}>Dismiss</Button>
            </div>
          }
        >
          {error.message}
          {error.code === "quota_exceeded" ? " Delete unused files or old draft uploads, or ask a platform admin for more space." : null}
        </InlineNotice>
      ) : null}
    </div>
  );
}

/** Editable data dictionary rows (column / type / description), max 200 as enforced by the API. */
export function DataDictionaryEditor({
  slug,
  value,
  onChange,
  files,
}: {
  slug: string;
  value: DataDictionaryEntry[];
  onChange: (v: DataDictionaryEntry[]) => void;
  files: DatasetFile[];
}) {
  const previewable = useMemo(() => files.filter((f) => f.has_preview), [files]);
  const [importing, setImporting] = useState(false);
  const [source, setSource] = useState<string>("");
  const selected = source || previewable[0]?.id || "";

  const update = (i: number, patch: Partial<DataDictionaryEntry>) => onChange(value.map((row, j) => (j === i ? { ...row, ...patch } : row)));

  async function importColumns() {
    if (!selected) return;
    setImporting(true);
    try {
      const p = await get<FilePreview>(`/datasets/${slug}/files/${selected}/preview`);
      const cols = p.preview?.columns ?? [];
      const existing = new Set(value.map((r) => r.column));
      const added = cols.filter((c) => c && !existing.has(c)).map((c) => ({ column: c, type: "", description: "" }));
      if (!added.length) toast.info("All columns from that file are already listed.");
      else {
        onChange([...value, ...added].slice(0, 200));
        toast.success(`Added ${added.length} column${added.length === 1 ? "" : "s"} from ${p.filename}`);
      }
    } catch (e) {
      toast.error(describeError(e, "Could not read the file preview").message);
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="space-y-3">
      {value.length ? (
        <div className="overflow-x-auto rounded-[var(--radius-lg)] border border-border">
          <table className="w-full min-w-[36rem] border-collapse text-sm">
            <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-subtle">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Column</th>
                <th scope="col" className="w-36 px-3 py-2 font-medium">Type</th>
                <th scope="col" className="px-3 py-2 font-medium">Description</th>
                <th scope="col" className="w-10 px-2 py-2"><span className="sr-only">Remove</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border bg-surface">
              {value.map((row, i) => (
                <tr key={i}>
                  <td className="px-2 py-1.5">
                    <Input aria-label={`Column name, row ${i + 1}`} className="h-8 font-mono text-xs" value={row.column} maxLength={80} onChange={(e) => update(i, { column: e.target.value })} />
                  </td>
                  <td className="px-2 py-1.5">
                    <Input aria-label={`Type, row ${i + 1}`} className="h-8 font-mono text-xs" value={row.type} maxLength={40} placeholder="e.g. int, str" onChange={(e) => update(i, { type: e.target.value })} />
                  </td>
                  <td className="px-2 py-1.5">
                    <Input aria-label={`Description, row ${i + 1}`} className="h-8 text-xs" value={row.description} maxLength={500} onChange={(e) => update(i, { description: e.target.value })} />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <Button size="icon" variant="ghost" className="h-8 w-8" aria-label={`Remove row ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-subtle">No columns documented yet. Add them manually or import the header row of an uploaded CSV/TSV.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={value.length >= 200} onClick={() => onChange([...value, { column: "", type: "", description: "" }])} icon={<Plus className="h-3.5 w-3.5" aria-hidden />}>
          Add column
        </Button>
        {previewable.length ? (
          <div className="flex flex-wrap items-center gap-2">
            {previewable.length > 1 ? (
              <Select aria-label="File to import columns from" className="h-8 w-auto text-xs" value={selected} onChange={(e) => setSource(e.target.value)}>
                {previewable.map((f) => <option key={f.id} value={f.id}>{f.filename}</option>)}
              </Select>
            ) : null}
            <Button size="sm" variant="ghost" loading={importing} onClick={importColumns} icon={<Columns3 className="h-3.5 w-3.5" aria-hidden />}>
              Import columns{previewable.length === 1 ? ` from ${previewable[0].filename}` : ""}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function useDirty<T>(value: T, initial: T) {
  const dirty = useMemo(() => JSON.stringify(value) !== JSON.stringify(initial), [value, initial]);
  useUnsavedChangesWarning(dirty);
  return dirty;
}
