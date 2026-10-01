"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Database,
  FolderGit2,
  MessagesSquare,
  Search as SearchIcon,
  SearchX,
  Trophy,
  University,
  User,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { Highlighted } from "@/components/ui/markdown";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, SkeletonRows, Spinner } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { qk } from "@/lib/query";
import type { Page, SearchResult } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 20;

const TYPES: { value: string; label: string; plural: string; icon: LucideIcon; href: string }[] = [
  { value: "competition", label: "Competition", plural: "Competitions", icon: Trophy, href: "/competitions" },
  { value: "dataset", label: "Dataset", plural: "Datasets", icon: Database, href: "/datasets" },
  { value: "course", label: "Course", plural: "Courses", icon: BookOpen, href: "/learn" },
  { value: "project", label: "Project", plural: "Projects", icon: FolderGit2, href: "/projects" },
  { value: "organization", label: "Organization", plural: "Organizations", icon: University, href: "/orgs" },
  { value: "user", label: "Person", plural: "People", icon: User, href: "" },
  { value: "thread", label: "Discussion", plural: "Discussions", icon: MessagesSquare, href: "/discussions" },
];
const TYPE_MAP = Object.fromEntries(TYPES.map((t) => [t.value, t]));

interface SearchResponse extends Page<SearchResult> {
  facets: Record<string, number>;
  query: string;
}

function ResultItem({ r }: { r: SearchResult }) {
  const t = TYPE_MAP[r.type];
  const Icon = t?.icon ?? SearchIcon;
  return (
    <li className="flex gap-3 px-4 py-4">
      <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted">
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={r.url} className="font-medium text-fg hover:text-accent-strong hover:underline">{r.title}</Link>
          <Badge tone="outline">{t?.label ?? r.type}</Badge>
        </div>
        {r.subtitle ? <p className="mt-0.5 line-clamp-1 text-sm text-muted">{r.subtitle}</p> : null}
        {r.snippet ? (
          <p className="mt-1.5 line-clamp-3 text-sm text-muted">
            <Highlighted text={r.snippet} />
          </p>
        ) : null}
        {r.tags.length ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {r.tags.slice(0, 6).map((tag) => (
              <span key={tag} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-subtle">{tag}</span>
            ))}
          </div>
        ) : null}
      </div>
    </li>
  );
}

function SearchInner() {
  const [state, setState] = useUrlState({ q: "", type: "", page: "1" });
  const q = (state.q ?? "").trim();
  const type = state.type && TYPE_MAP[state.type] ? state.type : "";
  const page = Math.max(1, Number(state.page) || 1);
  const [draft, setDraft] = useState(q);

  useEffect(() => {
    setDraft(state.q ?? "");
  }, [state.q]);

  const ready = q.length >= 2;
  const results = useQuery({
    queryKey: qk.search(q, type || undefined, page),
    queryFn: ({ signal }) => get<SearchResponse>("/search", { q, type: type || undefined, page, page_size: PAGE_SIZE }, signal),
    enabled: ready,
    placeholderData: keepPreviousData,
  });

  const facets = results.data?.facets ?? {};
  const allCount = Object.values(facets).reduce((a, b) => a + b, 0);
  const tabs = TYPES.filter((t) => (facets[t.value] ?? 0) > 0 || t.value === type);

  return (
    <Container size="lg">
      <PageHeader title="Search" description="Find competitions, datasets, courses, projects, organizations, people and discussions." />
      <form
        role="search"
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setState({ q: draft.trim() });
        }}
      >
        <label htmlFor="search-q" className="sr-only">Search DataBattles</label>
        <div className="relative flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input
            id="search-q"
            type="search"
            className="h-11 pl-9 text-base"
            placeholder="e.g. sentiment analysis, time series, pandas"
            value={draft}
            maxLength={200}
            autoFocus={!q}
            onChange={(e) => setDraft(e.target.value)}
          />
        </div>
        <Button type="submit" size="lg" className="h-11">Search</Button>
      </form>

      {ready && results.data && allCount > 0 ? (
        <div className="mt-6 flex gap-1 overflow-x-auto border-b border-border" role="group" aria-label="Filter by type">
          {[{ value: "", plural: "All" }, ...tabs].map((t) => {
            const active = type === t.value;
            const count = t.value ? facets[t.value] ?? 0 : allCount;
            return (
              <button
                key={t.value || "all"}
                type="button"
                aria-pressed={active}
                onClick={() => setState({ type: t.value })}
                className={cn(
                  "-mb-px shrink-0 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                  active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
                )}
              >
                {t.plural}
                <span className="ml-1.5 rounded-full bg-surface-3 px-1.5 text-xs text-subtle">{formatNumber(count)}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="mt-6 pb-16">
        {!q ? (
          <div>
            <EmptyState
              icon={<SearchIcon className="h-5 w-5" />}
              title="What are you looking for?"
              description="Search by keyword, technique or topic. Private content you can't access is never shown."
            />
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {TYPES.filter((t) => t.href).map((t) => (
                <Link key={t.value} href={t.href} className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm text-muted hover:border-border-strong hover:text-fg">
                  <t.icon className="h-3.5 w-3.5" aria-hidden /> Browse {t.plural.toLowerCase()}
                </Link>
              ))}
            </div>
          </div>
        ) : !ready ? (
          <EmptyState icon={<SearchIcon className="h-5 w-5" />} title="Keep typing" description="Enter at least 2 characters to search." />
        ) : results.isPending ? (
          <SkeletonRows rows={6} />
        ) : results.isError ? (
          <ErrorState error={results.error} onRetry={() => results.refetch()} />
        ) : !results.data.items.length ? (
          <EmptyState
            icon={<SearchX className="h-5 w-5" />}
            title={<>No results for “{q}”{type ? ` in ${TYPE_MAP[type].plural.toLowerCase()}` : ""}</>}
            description="Check the spelling, try fewer or more general words, or search for a related technique."
            action={type ? <Button variant="secondary" onClick={() => setState({ type: "" })}>Search all types</Button> : undefined}
          />
        ) : (
          <>
            <p className="mb-3 text-sm text-muted" aria-live="polite">
              {formatNumber(results.data.total)} {results.data.total === 1 ? "result" : "results"} for “<span className="text-fg">{results.data.query}</span>”
            </p>
            <ul className={cn("divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface transition-opacity", results.isPlaceholderData && "opacity-60")}>
              {results.data.items.map((r) => (
                <ResultItem key={`${r.type}-${r.id}`} r={r} />
              ))}
            </ul>
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={results.data.total}
              onPage={(p) => {
                setState({ page: String(p) });
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
            />
          </>
        )}
      </div>
    </Container>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <SearchInner />
    </Suspense>
  );
}
