"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CircleCheck, EyeOff, Lock, Megaphone, MessageSquare, Pin, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Checkbox, Input, Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, formatDateTime, relativeTime } from "@/lib/format";
import { useDebounced } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { useUrlState } from "@/lib/url-state";
import type { DiscussionCategory, ThreadPage, ThreadRow } from "./types";

export function useCategories() {
  return useQuery({
    queryKey: ["discussions", "categories"],
    queryFn: () => get<DiscussionCategory[]>("/discussions/categories"),
    staleTime: 60_000,
  });
}

/** Thread state indicators — each has an icon and a text label. */
export function ThreadFlags({
  t,
  className,
}: {
  t: Pick<ThreadRow, "pinned" | "is_announcement" | "locked" | "hidden" | "deleted"> & { has_accepted_answer?: boolean };
  className?: string;
}) {
  const flags = [
    t.pinned ? <Badge key="pin" tone="accent" icon={<Pin className="h-3 w-3" aria-hidden />}>Pinned</Badge> : null,
    t.is_announcement ? <Badge key="ann" tone="info" icon={<Megaphone className="h-3 w-3" aria-hidden />}>Announcement</Badge> : null,
    t.has_accepted_answer ? <Badge key="ans" tone="success" icon={<CircleCheck className="h-3 w-3" aria-hidden />}>Answered</Badge> : null,
    t.locked ? <Badge key="lock" tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Locked</Badge> : null,
    t.hidden ? <Badge key="hid" tone="danger" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Hidden</Badge> : null,
    t.deleted ? <Badge key="del" tone="danger">Deleted</Badge> : null,
  ].filter(Boolean);
  if (!flags.length) return null;
  return <div className={cn("flex flex-wrap items-center gap-1.5", className)}>{flags}</div>;
}

function ThreadItem({ t }: { t: ThreadRow }) {
  return (
    <li className={cn("flex gap-4 px-4 py-4 transition-colors hover:bg-surface-2/60", t.pinned && "bg-accent-soft/40")}>
      <div className="min-w-0 flex-1">
        <ThreadFlags t={t} className="mb-1.5" />
        <Link href={`/discussions/t/${t.id}`} className="font-medium text-fg hover:text-accent-strong focus-visible:underline">
          {t.title}
        </Link>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
          <UserLink user={t.author} size={18} className="text-xs text-muted" />
          <span aria-hidden>·</span>
          <time dateTime={t.created_at} title={formatDateTime(t.created_at)}>started {relativeTime(t.created_at)}</time>
          <span aria-hidden>·</span>
          <time dateTime={t.last_activity_at} title={formatDateTime(t.last_activity_at)}>active {relativeTime(t.last_activity_at)}</time>
        </div>
      </div>
      <div className="flex w-14 shrink-0 flex-col items-center justify-center text-center">
        <span className="inline-flex items-center gap-1 text-sm font-semibold tabular-nums text-fg">
          <MessageSquare className="h-3.5 w-3.5 text-subtle" aria-hidden />
          {compactNumber(t.reply_count)}
        </span>
        <span className="text-[11px] text-subtle">{t.reply_count === 1 ? "reply" : "replies"}</span>
      </div>
    </li>
  );
}

const DEFAULTS = { q: "", sort: "activity", mine: "", page: "1" };
const PAGE_SIZE = 20;

const SORTS = [
  { value: "activity", label: "Latest activity" },
  { value: "new", label: "Newest" },
  { value: "replies", label: "Most replies" },
  { value: "unanswered", label: "Unanswered first" },
];

/**
 * Searchable, sortable thread list. Filters live in the URL (wrap the page in <Suspense>).
 */
export function ThreadListView({ category, signedIn, emptyAction }: { category?: string; signedIn: boolean; emptyAction?: ReactNode }) {
  const [state, set, reset] = useUrlState(DEFAULTS);
  const page = Math.max(1, Number(state.page) || 1);
  const [search, setSearch] = useState(state.q);
  const debounced = useDebounced(search, 350);

  useEffect(() => {
    if (debounced !== state.q) set({ q: debounced });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  useEffect(() => {
    if (state.q !== debounced) setSearch(state.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.q]);

  const filters = {
    category: category || undefined,
    q: state.q || undefined,
    sort: state.sort,
    mine: signedIn && state.mine === "1" ? true : undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const threads = useQuery({
    queryKey: qk.threads(filters),
    queryFn: () => get<ThreadPage>("/discussions/threads", filters),
    placeholderData: keepPreviousData,
  });
  const filtered = Boolean(state.q || state.mine);

  return (
    <div>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <label htmlFor="thread-search" className="sr-only">Search discussions</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input
            id="thread-search"
            type="search"
            className="pl-9"
            placeholder="Search discussions…"
            value={search}
            maxLength={80}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-3">
          <Select aria-label="Sort discussions" value={state.sort} onChange={(e) => set({ sort: e.target.value })} className="sm:w-44">
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </Select>
          {signedIn ? (
            <Checkbox label="Mine" className="shrink-0" checked={state.mine === "1"} onChange={(e) => set({ mine: e.target.checked ? "1" : "" })} />
          ) : null}
        </div>
      </div>

      {threads.isPending ? (
        <div className="divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface" role="status" aria-label="Loading discussions">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="px-4 py-4">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="mt-2 h-3 w-1/3" />
            </div>
          ))}
        </div>
      ) : threads.isError ? (
        <ErrorState error={threads.error} onRetry={() => threads.refetch()} />
      ) : threads.data.items.length === 0 ? (
        filtered ? (
          <NoResults onReset={() => { setSearch(""); reset(); }} />
        ) : (
          <EmptyState title="No discussions yet" description="Start the conversation — ask a question or share what you're working on." action={emptyAction} />
        )
      ) : (
        <div aria-busy={threads.isFetching || undefined}>
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" aria-label="Discussions">
            {threads.data.items.map((t) => <ThreadItem key={t.id} t={t} />)}
          </ul>
          <Pagination
            page={threads.data.page}
            pageSize={threads.data.page_size}
            total={threads.data.total}
            onPage={(p) => {
              set({ page: String(p) });
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
          />
        </div>
      )}
    </div>
  );
}

/** Category navigation: chips on small screens, a vertical list on large screens. */
export function CategoryNav({ current }: { current?: string }) {
  const cats = useCategories();
  const total = cats.data?.reduce((n, c) => n + c.thread_count, 0);
  const item = (href: string, label: string, count: number | undefined, active: boolean) => (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex shrink-0 items-center justify-between gap-3 rounded-[var(--radius-md)] px-3 py-2 text-sm transition-colors",
        active ? "bg-accent-soft font-medium text-accent-strong" : "text-muted hover:bg-surface-2 hover:text-fg",
      )}
    >
      <span className="truncate">{label}</span>
      {count !== undefined ? <span className="text-xs tabular-nums text-subtle">{compactNumber(count)}</span> : null}
    </Link>
  );
  return (
    <nav aria-label="Discussion categories">
      <h2 className="mb-2 hidden px-3 text-xs font-medium uppercase tracking-wide text-subtle lg:block">Categories</h2>
      {cats.isPending ? (
        <div className="flex gap-2 lg:flex-col" role="status" aria-label="Loading categories">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-9 w-28 lg:w-full" />)}
        </div>
      ) : cats.isError ? (
        <p className="px-3 text-sm text-muted">
          Categories unavailable.{" "}
          <button type="button" className="text-accent-strong hover:underline" onClick={() => cats.refetch()}>Retry</button>
        </p>
      ) : (
        <ul className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0">
          <li>{item("/discussions", "All discussions", total, !current)}</li>
          {cats.data.map((c) => (
            <li key={c.id}>{item(`/discussions/c/${c.slug}`, c.name, c.thread_count, current === c.slug)}</li>
          ))}
        </ul>
      )}
    </nav>
  );
}
