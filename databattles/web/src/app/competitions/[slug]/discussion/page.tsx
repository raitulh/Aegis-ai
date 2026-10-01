"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CheckCircle2, EyeOff, Lock, Megaphone, MessageSquare, MessagesSquare, Pin, Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";

import { Block } from "@/components/competition/block";
import { useCompetition } from "@/components/competition/context";
import type { ThreadsResponse } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/extras";
import { Input } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SignInPrompt, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useDebounced, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 20;
const DEFAULTS = { sort: "activity", q: "", page: "1" };
const SORTS = [
  { value: "activity", label: "Recent activity" },
  { value: "new", label: "Newest" },
  { value: "replies", label: "Most replies" },
  { value: "unanswered", label: "Unanswered" },
];

function ThreadsSkeleton() {
  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" role="status" aria-label="Loading threads">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 border-b border-border px-4 py-4 last:border-b-0" style={{ opacity: 1 - i * 0.1 }}>
          <Skeleton className="h-11 w-12 rounded-[var(--radius-md)]" />
          <div className="flex-1">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="mt-2 h-3 w-1/3" />
          </div>
          <Skeleton className="hidden h-3 w-24 sm:block" />
        </div>
      ))}
    </div>
  );
}

function Discussion() {
  const comp = useCompetition();
  const slug = comp.slug;
  const me = useMe();
  const [state, set, reset] = useUrlState(DEFAULTS);
  const page = Math.max(1, Number.parseInt(state.page || "1", 10) || 1);

  const [q, setQ] = useState(state.q);
  const debounced = useDebounced(q.trim(), 350);
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
    if (debounced !== committed.current) {
      committed.current = debounced;
      setRef.current({ q: debounced });
    }
  }, [debounced]);

  const params = { competition: slug, sort: state.sort, q: state.q || undefined, page, page_size: PAGE_SIZE };
  const query = useQuery({
    queryKey: qk.threads(params),
    queryFn: ({ signal }) => get<ThreadsResponse>("/discussions/threads", params, signal),
    placeholderData: keepPreviousData,
  });
  const newHref = `/discussions/new?competition=${encodeURIComponent(slug)}`;

  return (
    <div className="space-y-6">
      <Block
        as="div"
        eyebrow="Community"
        title="Discussion"
        icon={<MessagesSquare />}
        description="Ask questions, share ideas and find teammates. Don't share code or predictions privately outside your team."
        action={
          me.data ? (
            <LinkButton href={newHref} icon={<Plus className="h-4 w-4" aria-hidden />}>New thread</LinkButton>
          ) : me.isSuccess ? (
            <SignInPrompt text="Sign in to start a thread." />
          ) : null
        }
      >
        <div className="flex flex-col gap-3 rounded-[var(--radius-xl)] border border-border surface-glass p-3 shadow-card lg:flex-row lg:items-center">
          <form role="search" className="relative min-w-0 flex-1" onSubmit={(e) => e.preventDefault()}>
            <label htmlFor="thread-search" className="sr-only">Search threads</label>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
            <Input id="thread-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search this competition's threads…" className="pl-9 pr-9" maxLength={80} />
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
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 text-eyebrow text-subtle">Sort</span>
            <SegmentedControl label="Sort threads" value={state.sort} onChange={(v) => set({ sort: v })} options={SORTS} size="sm" className="min-w-0 [&>button]:h-9 sm:[&>button]:h-7" />
          </div>
        </div>
      </Block>

      {query.isPending ? (
        <ThreadsSkeleton />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : query.data.items.length === 0 ? (
        state.q || state.sort === "unanswered" ? (
          <NoResults
            onReset={() => {
              committed.current = "";
              setQ("");
              reset();
            }}
          />
        ) : (
          <EmptyState
            icon={<MessageSquare />}
            title="No threads yet"
            description="Start the conversation — questions about the data, evaluation or team-ups are all welcome."
            action={me.data ? <LinkButton href={newHref}>Start a thread</LinkButton> : undefined}
          />
        )
      ) : (
        <>
          <p className="text-xs text-subtle">
            <span className="tabular font-medium text-muted">{formatNumber(query.data.total)}</span> {query.data.total === 1 ? "thread" : "threads"}
            {query.isFetching ? " · updating…" : ""}
          </p>
          <ul className={cn("overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card transition-opacity", query.isPlaceholderData && "opacity-60")}>
            {query.data.items.map((t) => (
              <li
                key={t.id}
                className={cn(
                  "relative flex items-start gap-4 border-b border-border px-4 py-3.5 transition-colors last:border-b-0 hover:bg-surface-2/50 sm:items-center",
                  t.pinned && "bg-info-soft/40",
                )}
              >
                {t.pinned ? <span aria-hidden className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-info" /> : null}
                <div
                  className={cn(
                    "flex h-11 w-12 shrink-0 flex-col items-center justify-center rounded-[var(--radius-md)] border",
                    t.has_accepted_answer ? "border-[color-mix(in_oklab,var(--success)_30%,transparent)] bg-success-soft text-success" : "border-border bg-bg-elevated text-fg",
                  )}
                >
                  <span className="tabular text-sm font-semibold leading-none">{formatNumber(t.reply_count)}</span>
                  <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.12em] text-subtle">{t.reply_count === 1 ? "reply" : "replies"}</span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {t.pinned ? <Pin className="h-3.5 w-3.5 text-info" aria-label="Pinned" /> : null}
                    <Link
                      href={`/discussions/t/${t.id}`}
                      className="rounded-sm font-medium text-fg transition-colors hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                    >
                      {t.title}
                    </Link>
                    {t.is_announcement ? <Badge tone="info" icon={<Megaphone className="h-3 w-3" aria-hidden />}>Announcement</Badge> : null}
                    {t.has_accepted_answer ? <Badge tone="success" icon={<CheckCircle2 className="h-3 w-3" aria-hidden />}>Answered</Badge> : null}
                    {t.locked ? <Badge tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Locked</Badge> : null}
                    {t.hidden ? <Badge tone="warning" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Hidden</Badge> : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
                    <UserLink user={t.author} size={16} className="text-xs" />
                    <span aria-hidden>·</span>
                    <span>started <time dateTime={t.created_at} title={formatDateTime(t.created_at)}>{relativeTime(t.created_at)}</time></span>
                    <span aria-hidden className="sm:hidden">·</span>
                    <time dateTime={t.last_activity_at} title={formatDateTime(t.last_activity_at)} className="sm:hidden">active {relativeTime(t.last_activity_at)}</time>
                  </div>
                </div>
                <time
                  dateTime={t.last_activity_at}
                  title={formatDateTime(t.last_activity_at)}
                  className="hidden shrink-0 text-right text-xs text-muted sm:block"
                >
                  active {relativeTime(t.last_activity_at)}
                </time>
              </li>
            ))}
          </ul>
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={(p) => set({ page: String(p) })} />
        </>
      )}
    </div>
  );
}

export default function CompetitionDiscussionPage() {
  return (
    <Suspense fallback={<ThreadsSkeleton />}>
      <Discussion />
    </Suspense>
  );
}
