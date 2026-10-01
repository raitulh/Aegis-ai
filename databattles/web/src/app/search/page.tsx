"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
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
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
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

/** One ranked result. `showType` adds a visible type label (the mixed "All" view); otherwise it is screen-reader only. */
function ResultItem({ r, showType }: { r: SearchResult; showType: boolean }) {
  const t = TYPE_MAP[r.type];
  const Icon = t?.icon ?? SearchIcon;
  return (
    <li className="group relative flex gap-3.5 px-4 py-4 transition-colors hover:bg-surface-2/40 sm:px-5">
      <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)]">
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Link
            href={r.url}
            className="rounded-sm font-medium tracking-[-0.01em] text-fg transition-colors hover:text-accent-strong hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
          >
            {r.title}
          </Link>
          {showType ? (
            <Badge tone="outline" className="h-5 px-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em]">{t?.label ?? r.type}</Badge>
          ) : (
            <span className="sr-only">({t?.label ?? r.type})</span>
          )}
        </div>
        {r.subtitle ? <p className="mt-0.5 line-clamp-1 text-sm text-muted">{r.subtitle}</p> : null}
        {r.snippet ? (
          <p className="mt-2 line-clamp-3 border-l-2 border-border pl-3 text-sm leading-relaxed text-muted">
            <Highlighted text={r.snippet} />
          </p>
        ) : null}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="truncate font-mono text-[11px] text-subtle">{r.url}</span>
          {r.tags.length ? (
            <span className="flex flex-wrap gap-1">
              {[...new Set(r.tags)].slice(0, 6).map((tag) => (
                <span key={tag} className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-subtle">{tag}</span>
              ))}
            </span>
          ) : null}
        </div>
      </div>
      <ArrowUpRight className="mt-1 hidden h-4 w-4 shrink-0 text-subtle transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-strong sm:block" aria-hidden />
    </li>
  );
}

/** Shortcuts into each catalogue — for when there is nothing (or nothing yet) to show. */
function BrowseLinks() {
  return (
    <nav aria-label="Browse catalogues" className="mt-6 flex flex-wrap justify-center gap-2">
      {TYPES.filter((t) => t.href).map((t) => (
        <Link
          key={t.value}
          href={t.href}
          className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-bg-elevated/60 px-3 text-sm text-muted transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
        >
          <t.icon className="h-3.5 w-3.5" aria-hidden /> Browse {t.plural.toLowerCase()}
        </Link>
      ))}
    </nav>
  );
}

function ResultsSkeleton() {
  return (
    <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card" role="status" aria-label="Searching">
      <div className="divide-y divide-border">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex gap-3.5 px-4 py-4 sm:px-5" style={{ opacity: 1 - i * 0.12 }}>
            <Skeleton className="h-9 w-9 shrink-0 rounded-lg" />
            <div className="min-w-0 flex-1">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="mt-2 h-3 w-2/3" />
              <Skeleton className="mt-3 h-3 w-full" />
              <Skeleton className="mt-1.5 h-3 w-4/5" />
            </div>
          </div>
        ))}
      </div>
    </div>
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
    <Container size="lg" className="pb-20">
      <PageHeader
        eyebrow="Find anything"
        icon={<SearchIcon />}
        title="Search"
        description="Find competitions, datasets, courses, projects, organizations, people and discussions."
      />
      <form
        role="search"
        className="flex flex-col gap-2 rounded-[var(--radius-xl)] border border-border bg-glass p-2 shadow-card backdrop-blur-md animate-rise [animation-delay:80ms] sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          setState({ q: draft.trim() });
        }}
      >
        <label htmlFor="search-q" className="sr-only">Search DataBattles</label>
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input
            id="search-q"
            type="search"
            className="h-12 border-transparent bg-bg-elevated pl-10 text-base"
            placeholder="e.g. sentiment analysis, time series, pandas"
            value={draft}
            maxLength={200}
            autoFocus={!q}
            onChange={(e) => setDraft(e.target.value)}
          />
        </div>
        <Button type="submit" size="lg" className="h-12 sm:px-6" icon={<SearchIcon className="h-4 w-4" aria-hidden />}>Search</Button>
      </form>

      {ready && results.data && allCount > 0 ? (
        <div className="-mx-4 mt-5 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:px-0" role="group" aria-label="Filter by type">
          {[{ value: "", plural: "All", icon: SearchIcon }, ...tabs].map((t) => {
            const active = type === t.value;
            const count = t.value ? facets[t.value] ?? 0 : allCount;
            return (
              <button
                key={t.value || "all"}
                type="button"
                aria-pressed={active}
                onClick={() => setState({ type: t.value })}
                className={cn(
                  "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 text-sm font-medium transition-[background-color,border-color,color] duration-200",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                  active
                    ? "border-[color-mix(in_oklab,var(--accent)_45%,transparent)] bg-accent-soft text-accent-strong"
                    : "border-border bg-bg-elevated/60 text-muted hover:border-border-strong hover:text-fg",
                )}
              >
                <t.icon className="h-3.5 w-3.5" aria-hidden />
                {t.plural}
                <span className={cn("tabular rounded-full px-1.5 text-xs", active ? "bg-surface-2 text-accent-strong" : "bg-surface-3 text-subtle")}>{formatNumber(count)}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="mt-8">
        {!q ? (
          <div>
            <EmptyState
              icon={<SearchIcon />}
              title="What are you looking for?"
              description="Search by keyword, technique or topic. Private content you can't access is never shown."
            />
            <BrowseLinks />
          </div>
        ) : !ready ? (
          <EmptyState icon={<SearchIcon />} title="Keep typing" description="Enter at least 2 characters to search." />
        ) : results.isPending ? (
          <ResultsSkeleton />
        ) : results.isError ? (
          <ErrorState error={results.error} onRetry={() => results.refetch()} />
        ) : !results.data.items.length ? (
          <div>
            <EmptyState
              icon={<SearchX />}
              title={<>No results for “{q}”{type ? ` in ${TYPE_MAP[type].plural.toLowerCase()}` : ""}</>}
              description="Check the spelling, try fewer or more general words, or search for a related technique."
              action={type ? <Button variant="secondary" onClick={() => setState({ type: "" })}>Search all types</Button> : undefined}
            />
            {!type ? <BrowseLinks /> : null}
          </div>
        ) : (
          <>
            <p className="mb-5 flex flex-wrap items-center gap-x-2 text-sm text-muted" aria-live="polite">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
              <span>
                <span className="tabular font-medium text-fg">{formatNumber(results.data.total)}</span> {results.data.total === 1 ? "result" : "results"} for “
                <span className="text-fg">{results.data.query}</span>”{type ? ` in ${TYPE_MAP[type].plural.toLowerCase()}` : ""}
              </span>
            </p>
            {/* Results stay in the API's rank order in every view; the "All" view labels each row's type. */}
            <ul
              className={cn(
                "divide-y divide-border overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card transition-opacity",
                results.isPlaceholderData && "opacity-60",
              )}
            >
              {results.data.items.map((r) => (
                <ResultItem key={`${r.type}-${r.id}`} r={r} showType={!type} />
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
    <Suspense
      fallback={
        <Container size="lg" className="py-16">
          <ResultsSkeleton />
        </Container>
      }
    >
      <SearchInner />
    </Suspense>
  );
}
