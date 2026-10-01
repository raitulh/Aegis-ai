"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";

import {
  DIFFICULTY_OPTIONS,
  EVENT_TYPE_OPTIONS,
  SORT_OPTIONS,
  STATUS_OPTIONS,
  TASK_TYPE_OPTIONS,
} from "@/components/competition/labels";
import { CompetitionCard } from "@/components/domain/cards";
import { Button } from "@/components/ui/button";
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
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">{label}</label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="h-9">
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </Select>
    </div>
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

  return (
    <Container>
      <PageHeader
        title="Competitions"
        description="Machine-learning challenges, datathons and hackathons from universities and clubs. Train anywhere — upload predictions here for reproducible scoring."
      />

      <div className="rounded-[var(--radius-lg)] border border-border bg-surface p-4">
        <form role="search" onSubmit={(e) => e.preventDefault()} className="relative">
          <label htmlFor="comp-search" className="sr-only">Search competitions</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input
            id="comp-search"
            type="search"
            placeholder="Search by title or summary…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="pl-9"
            maxLength={100}
          />
        </form>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-8">
          <FilterSelect id="f-status" label="Status" value={state.status} onChange={(v) => set({ status: v })} options={STATUS_OPTIONS} anyLabel="All but archived" />
          <FilterSelect id="f-task" label="Task" value={state.task_type} onChange={(v) => set({ task_type: v })} options={TASK_TYPE_OPTIONS} />
          <FilterSelect id="f-event" label="Event type" value={state.event_type} onChange={(v) => set({ event_type: v })} options={EVENT_TYPE_OPTIONS} />
          <FilterSelect id="f-difficulty" label="Difficulty" value={state.difficulty} onChange={(v) => set({ difficulty: v })} options={DIFFICULTY_OPTIONS} />
          <FilterSelect
            id="f-prize"
            label="Prizes"
            value={state.prize}
            onChange={(v) => set({ prize: v })}
            options={[{ value: "yes", label: "With prizes" }, { value: "no", label: "No prizes" }]}
          />
          <FilterSelect
            id="f-participation"
            label="Participation"
            value={state.participation}
            onChange={(v) => set({ participation: v })}
            options={[{ value: "individual", label: "Individual" }, { value: "team", label: "Teams allowed" }]}
          />
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor="f-tag" className="text-xs font-medium text-muted">Tag</label>
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
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor="f-sort" className="text-xs font-medium text-muted">Sort by</label>
            <Select id="f-sort" value={state.sort} onChange={(e) => set({ sort: e.target.value })} className="h-9">
              {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </div>
        </div>
        {filtered ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {state.tag ? (
              <button
                type="button"
                onClick={() => set({ tag: "" })}
                className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-strong"
                aria-label={`Remove tag filter ${state.tag}`}
              >
                #{state.tag} <X className="h-3 w-3" aria-hidden />
              </button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                committed.current = "";
                setQ("");
                reset();
              }}
            >
              Clear all filters
            </Button>
          </div>
        ) : null}
      </div>

      <div className="mt-6 pb-16" aria-live="polite" aria-busy={query.isFetching}>
        {query.isPending ? (
          <SkeletonCards count={6} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.data.items.length === 0 ? (
          filtered ? (
            <NoResults
              onReset={() => {
                committed.current = "";
                setQ("");
                reset();
              }}
            />
          ) : (
            <EmptyState
              title="No competitions yet"
              description="There are no public competitions right now. Check back soon, or browse completed ones."
              action={<Button variant="secondary" onClick={() => set({ status: "completed" })}>Show completed</Button>}
            />
          )
        ) : (
          <>
            <p className="mb-3 text-sm text-muted">
              {formatNumber(query.data.total)} {query.data.total === 1 ? "competition" : "competitions"}
              {query.isFetching ? <span className="ml-2 text-subtle">Updating…</span> : null}
            </p>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {query.data.items.map((c) => <CompetitionCard key={c.id} c={c} />)}
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
