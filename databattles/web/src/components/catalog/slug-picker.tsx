"use client";

import { useQuery } from "@tanstack/react-query";
import { Database, Plus, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { get } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import type { Page } from "@/lib/types";

interface SlugItem {
  slug: string;
  title: string;
}

/** Type-ahead suggestions from a public list endpoint (/competitions or /datasets). */
function useSuggestions(kind: "competitions" | "datasets", q: string) {
  const debounced = useDebounced(q.trim(), 300);
  return useQuery({
    queryKey: [kind, "suggest", debounced],
    queryFn: () => get<Page<SlugItem>>(`/${kind}`, { q: debounced || undefined, page_size: 8 }),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
}

type AriaProps = { id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string };

/** Single competition slug with suggestions (native datalist keeps it accessible and lightweight). */
export function CompetitionSlugInput({ value, onChange, ...aria }: { value: string; onChange: (v: string) => void } & AriaProps) {
  const listId = useId();
  const s = useSuggestions("competitions", value);
  return (
    <>
      <Input
        {...aria}
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search or paste a competition slug"
        maxLength={80}
        autoComplete="off"
      />
      <datalist id={listId}>
        {(s.data?.items ?? []).map((c) => <option key={c.slug} value={c.slug}>{c.title}</option>)}
      </datalist>
    </>
  );
}

/** Up to `max` dataset slugs, shown as removable chips, with type-ahead suggestions. */
export function DatasetSlugsInput({
  value,
  onChange,
  max = 10,
  known,
  ...aria
}: { value: string[]; onChange: (v: string[]) => void; max?: number; known?: SlugItem[] } & AriaProps) {
  const listId = useId();
  const [text, setText] = useState("");
  const s = useSuggestions("datasets", text);
  const titles = new Map<string, string>();
  (known ?? []).forEach((k) => titles.set(k.slug, k.title));
  (s.data?.items ?? []).forEach((k) => titles.set(k.slug, k.title));

  const add = () => {
    const typed = text.trim();
    // Accept a slug, or a title that matches one of the suggestions.
    const match = (s.data?.items ?? []).find((d) => d.slug === typed.toLowerCase() || d.title.toLowerCase() === typed.toLowerCase());
    const slug = match ? match.slug : typed.toLowerCase();
    if (!slug || value.includes(slug) || value.length >= max) return;
    onChange([...value, slug]);
    setText("");
  };

  return (
    <div className="space-y-2">
      {value.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Linked datasets">
          {value.map((slug) => (
            <li key={slug} className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface-2 py-1 pl-2 pr-1 text-xs animate-pop max-sm:py-0">
              <Database className="h-3 w-3 shrink-0 text-cyan" aria-hidden />
              <span className="truncate font-medium text-fg">{titles.get(slug) ?? slug}</span>
              {titles.get(slug) ? <span className="hidden truncate font-mono text-[11px] text-subtle sm:inline">{slug}</span> : null}
              <button
                type="button"
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-subtle max-sm:h-9 max-sm:w-9 transition-colors hover:bg-surface-3 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
                aria-label={`Remove ${slug}`}
                onClick={() => onChange(value.filter((x) => x !== slug))}
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {value.length < max ? (
        <div className="flex gap-2">
          <Input
            {...aria}
            list={listId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            placeholder="Search public datasets by name or slug"
            autoComplete="off"
            maxLength={80}
          />
          <datalist id={listId}>
            {(s.data?.items ?? []).filter((d) => !value.includes(d.slug)).map((d) => <option key={d.slug} value={d.slug}>{d.title}</option>)}
          </datalist>
          <Button variant="secondary" onClick={add} disabled={!text.trim()} icon={<Plus className="h-4 w-4" aria-hidden />}>
            Add
          </Button>
        </div>
      ) : (
        <p className="text-xs text-subtle">You can link up to {max} datasets.</p>
      )}
    </div>
  );
}
