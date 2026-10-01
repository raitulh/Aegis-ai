"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useDebounced } from "@/lib/hooks";
import { Textarea } from "./form";

/**
 * Renders HTML that the backend already sanitized (markdown-it with raw HTML disabled + nh3 allow-list).
 * Never pass unsanitized user input here.
 */
export function Prose({ html, className }: { html: string | null | undefined; className?: string }) {
  if (!html) return null;
  return <div className={cn("prose-db", className)} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Converts search snippet markers [[…]] into <mark> safely (text is escaped first). */
export function Highlighted({ text }: { text: string }) {
  const parts = text.split(/(\[\[.*?\]\])/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("[[") && p.endsWith("]]") ? (
          <mark key={i} className="hl">{p.slice(2, -2)}</mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

/** Markdown editor with a server-rendered preview (identical to what gets stored). */
export function MarkdownEditor({
  id,
  value,
  onChange,
  placeholder,
  rows = 8,
  maxLength,
  ...aria
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  maxLength?: number;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}) {
  const [tab, setTab] = useState<"write" | "preview">("write");
  const debounced = useDebounced(value, 400);
  const preview = useQuery({
    queryKey: ["md-preview", debounced],
    queryFn: () => post<{ html: string }>("/meta/markdown-preview", { text: debounced }),
    enabled: tab === "preview" && debounced.trim().length > 0,
    staleTime: 60_000,
  });
  return (
    <div className="rounded-[var(--radius-md)] border border-border bg-bg-elevated">
      <div className="flex items-center justify-between border-b border-border px-2" role="tablist">
        <div className="flex">
          {(["write", "preview"] as const).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={cn("px-3 py-2 text-xs font-medium capitalize", tab === t ? "text-fg" : "text-subtle hover:text-fg")}
            >
              {t}
            </button>
          ))}
        </div>
        <span className="pr-2 text-[11px] text-subtle">Markdown supported · HTML is not</span>
      </div>
      {tab === "write" ? (
        <Textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={rows}
          maxLength={maxLength}
          className="rounded-none border-0 bg-transparent font-mono text-[13px] focus:ring-0"
          {...aria}
        />
      ) : (
        <div className="min-h-32 px-4 py-3">
          {!value.trim() ? (
            <p className="text-sm text-subtle">Nothing to preview.</p>
          ) : preview.isPending ? (
            <p className="text-sm text-subtle">Rendering…</p>
          ) : preview.isError ? (
            <p className="text-sm text-danger">Preview unavailable.</p>
          ) : (
            <Prose html={preview.data?.html} />
          )}
        </div>
      )}
    </div>
  );
}
