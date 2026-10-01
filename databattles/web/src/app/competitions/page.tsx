"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Database, Loader2, RotateCcw, Search, SlidersHorizontal, Trophy, X } from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";

import {
  DIFFICULTY_OPTIONS,
  EVENT_TYPE_OPTIONS,
  SORT_OPTIONS,
  STATUS_OPTIONS,
  TASK_TYPE_OPTIONS,
} from "@/components/competition/labels";
import { CompetitionCard } from "@/components/domain/cards";
import { Button, LinkButton } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/extras";
import { Input, Select } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useDebounced } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionCard as CompetitionCardT, Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 12;

const DEFAULTS = {
  q: "",
  status: "",
  task_type: "",
  event_type: "",
  difficulty: "",
  prize: "",
  participation: "",
  tag: "",
  sort: "relevance",
  page: "1",
};

type Filters = typeof DEFAULTS;
const FILTER_KEYS = ["q", "status", "task_type", "event_type", "difficulty", "prize", "participation", "tag"] as const;

const PRIZE_OPTIONS = [{ value: "yes", label: "With prizes" }, { value: "no", label: "No prizes" }];
const PARTICIPATION_OPTIONS = [{ value: "individual", label: "Individual" }, { value: "team", label: "Teams allowed" }];

/** Labels for the removable chips of the active filters (search has its own clear button). */
const CHIP_FILTERS: { key: Exclude<(typeof FILTER_KEYS)[number], "q" | "tag">; label: string; options: { value: string; label: string }[] }[] = [
  { key: "status", label: "Status", options: STATUS_OPTIONS },
  { key: "task_type", label: "Task", options: TASK_TYPE_OPTIONS },
  { key: "event_type", label: "Event", options: EVENT_TYPE_OPTIONS },
  { key: "difficulty", label: "Difficulty", options: DIFFICULTY_OPTIONS },
  { key: "prize", label: "Prizes", options: PRIZE_OPTIONS },
  { key: "participation", label: "Participation", options: PARTICIPATION_OPTIONS },
];

function FilterSelect({
  id,
  label,
  value,
  onChange,
  options,
  anyLabel = "Any",
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  anyLabel?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-eyebrow text-subtle">{label}</label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="h-9">
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </Select>
    </div>
  );
}

function Chip({ label, onRemove, removeLabel }: { label: string; onRemove: () => void; removeLabel: string }) {
  return (
    <button
      type="button"
      onClick={onRemove}
      aria-label={removeLabel}
      className="inline-flex h-9 animate-scale-in items-center gap-1.5 rounded-full sm:h-7 border border-[color-mix(in_oklab,var(--accent)_30%,transparent)] bg-accent-soft pl-2.5 pr-2 text-xs font-medium text-accent-strong transition-colors hover:border-[color-mix(in_oklab,var(--accent)_55%,transparent)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
    >
      {label}
      <X className="h-3 w-3" aria-hidden />
    </button>
  );
}

function CompetitionsList() {
  const [state, set, reset] = useUrlState<Filters>(DEFAULTS);
  const page = Math.max(1, Number.parseInt(state.page || "1", 10) || 1);

  // Search box: local state, debounced into the URL. External URL changes (back, reset) flow back in.
  const [q, setQ] = useState(state.q);
  const debounced = useDebounced(q, 350);
  const committed = useRef(state.q);
  const setRef = useRef(set);
  useEffect(() => {
    setRef.current = set;
  });
  useEffect(() => {
    if (state.q !== committed.current) {
      committed.current = state.q;
      setQ(state.q);
    }
  }, [state.q]);
  useEffect(() => {
    const next = debounced.trim();
    if (next !== committed.current) {
      committed.current = next;
      setRef.current({ q: next });
    }
  }, [debounced]);

  const params = {
    q: state.q || undefined,
    status: state.status || undefined,
    task_type: state.task_type || undefined,
    event_type: state.event_type || undefined,
    difficulty: state.difficulty || undefined,
    prize: state.prize || undefined,
    participation: state.participation || undefined,
    tag: state.tag || undefined,
    sort: state.sort,
    page,
    page_size: PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: qk.competitions(params),
    queryFn: ({ signal }) => get<Page<CompetitionCardT>>("/competitions", params, signal),
    placeholderData: keepPreviousData,
  });

  const activeFilters = FILTER_KEYS.filter((k) => Boolean(state[k]));
  const filtered = activeFilters.length > 0;
  const clearAll = () => {
    committed.current = "";
    setQ("");
    reset();
  };
  const sortLabel = SORT_OPTIONS.find((o) => o.value === state.sort)?.label ?? state.sort;

  return (
    <Container>
      <PageHeader
        eyebrow="Compete"
        icon={<Trophy />}
        title="Competitions"
        description="Machine-learning challenges, datathons and hackathons from universities and clubs. Train anywhere — upload predictions here for reproducible scoring."
      />

      {/* Filter bar */}
      <div className="relative animate-rise rounded-[var(--radius-xl)] border border-border surface-glass p-3 shadow-card [animation-delay:80ms] sm:p-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <form role="search" onSubmit={(e) => e.preventDefault()} className="relative min-w-0 flex-1">
            <label htmlFor="comp-search" className="sr-only">Search competitions</label>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
            <Input
              id="comp-search"
              type="search"
              placeholder="Search by title or summary…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="pl-9 pr-9"
              maxLength={100}
            />
            {q ? (
              <button
                type="button"
                onClick={() => setQ("")}
                className="absolute right-0.5 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md sm:right-1.5 sm:h-7 sm:w-7 text-subtle transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
                aria-label="Clear search"
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            ) : null}
          </form>
          <div className="flex items-center gap-2 md:w-60">
            <label htmlFor="f-sort" className="shrink-0 text-eyebrow text-subtle">Sort</label>
            <Select id="f-sort" value={state.sort} onChange={(e) => set({ sort: e.target.value })}>
              {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </div>
        </div>

        <div className="mt-3 flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
          <span className="shrink-0 text-eyebrow text-subtle">Status</span>
          <SegmentedControl
            label="Status"
            size="sm"
            value={state.status}
            onChange={(v) => set({ status: v })}
            options={[{ value: "", label: "All but archived" }, ...STATUS_OPTIONS]}
            className="flex-wrap self-start [&>button]:h-9 sm:[&>button]:h-7"
          />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-4 sm:grid-cols-3 xl:grid-cols-6">
          <FilterSelect id="f-task" label="Task" value={state.task_type} onChange={(v) => set({ task_type: v })} options={TASK_TYPE_OPTIONS} />
          <FilterSelect id="f-event" label="Event type" value={state.event_type} onChange={(v) => set({ event_type: v })} options={EVENT_TYPE_OPTIONS} />
          <FilterSelect id="f-difficulty" label="Difficulty" value={state.difficulty} onChange={(v) => set({ difficulty: v })} options={DIFFICULTY_OPTIONS} />
          <FilterSelect id="f-prize" label="Prizes" value={state.prize} onChange={(v) => set({ prize: v })} options={PRIZE_OPTIONS} />
          <FilterSelect id="f-participation" label="Participation" value={state.participation} onChange={(v) => set({ participation: v })} options={PARTICIPATION_OPTIONS} />
          <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor="f-tag" className="text-eyebrow text-subtle">Tag</label>
            <Input
              id="f-tag"
              key={state.tag}
              defaultValue={state.tag}
              placeholder="e.g. tabular"
              className="h-9"
              maxLength={48}
              onBlur={(e) => {
                const v = e.target.value.trim().toLowerCase().replace(/^#/, "");
                if (v !== state.tag) set({ tag: v });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  const v = e.currentTarget.value.trim().toLowerCase().replace(/^#/, "");
                  if (v !== state.tag) set({ tag: v });
                }
              }}
            />
          </div>
        </div>

        {filtered ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <span className="mr-1 inline-flex items-center gap-1.5 text-eyebrow text-subtle">
              <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden /> Active
            </span>
            {CHIP_FILTERS.filter((f) => state[f.key]).map((f) => {
              const value = f.options.find((o) => o.value === state[f.key])?.label ?? state[f.key];
              return <Chip key={f.key} label={`${f.label}: ${value}`} removeLabel={`Remove ${f.label.toLowerCase()} filter ${value}`} onRemove={() => set({ [f.key]: "" })} />;
            })}
            {state.tag ? <Chip label={`#${state.tag}`} removeLabel={`Remove tag filter ${state.tag}`} onRemove={() => set({ tag: "" })} /> : null}
            <Button size="sm" variant="ghost" icon={<RotateCcw className="h-3.5 w-3.5" aria-hidden />} onClick={clearAll} className="ml-auto">
              Clear all filters
            </Button>
          </div>
        ) : null}
      </div>

      <div className="mt-8 pb-16" aria-live="polite" aria-busy={query.isFetching}>
        {query.isPending ? (
          <SkeletonCards count={6} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.data.items.length === 0 ? (
          filtered ? (
            <NoResults onReset={clearAll} />
          ) : (
            <EmptyState
              icon={<Trophy />}
              title="No competitions yet"
              description="There are no public competitions right now. Check back soon, or browse completed ones."
              action={
                <>
                  <Button variant="secondary" onClick={() => set({ status: "completed" })}>Show completed</Button>
                  <LinkButton href="/datasets" variant="ghost" icon={<Database className="h-4 w-4" aria-hidden />}>Explore datasets</LinkButton>
                </>
              }
            />
          )
        ) : (
          <>
            {/* Keeps the heading outline h1 → h2 → card h3. */}
            <h2 className="sr-only">Results</h2>
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <p className="text-sm text-muted">
                <span className="tabular text-base font-semibold text-fg">{formatNumber(query.data.total)}</span>{" "}
                {query.data.total === 1 ? "competition" : "competitions"}
                {filtered ? <span className="text-subtle"> · {activeFilters.length} {activeFilters.length === 1 ? "filter" : "filters"} applied</span> : null}
              </p>
              <p className="flex items-center gap-2 text-xs text-subtle">
                {query.isFetching ? (
                  <span className="inline-flex items-center gap-1.5 text-muted">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Updating…
                  </span>
                ) : null}
                <span>Sorted by <span className="text-muted">{sortLabel.toLowerCase()}</span></span>
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {query.data.items.map((c, i) => (
                <div key={c.id} className="flex min-w-0 animate-rise [&>a]:w-full" style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}>
                  <CompetitionCard c={c} />
                </div>
              ))}
            </div>
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={query.data.total}
              onPage={(p) => {
                set({ page: String(p) });
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
            />
          </>
        )}
      </div>
    </Container>
  );
}

export default function CompetitionsPage() {
  return (
    <Suspense fallback={<Container><div className="py-8"><SkeletonCards count={6} /></div></Container>}>
      <CompetitionsList />
    </Suspense>
  );
}
