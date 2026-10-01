"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CheckCircle2, EyeOff, Lock, Megaphone, MessageSquare, Pin, Plus, Search } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";

import { useCompetition } from "@/components/competition/context";
import type { ThreadsResponse } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SignInPrompt, SkeletonRows } from "@/components/ui/states";
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
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-fg">Discussion</h2>
          <p className="text-sm text-muted">Ask questions, share ideas and find teammates. Don&apos;t share code or predictions privately outside your team.</p>
        </div>
        {me.data ? (
          <LinkButton href={newHref} icon={<Plus className="h-4 w-4" aria-hidden />}>New thread</LinkButton>
        ) : me.isSuccess ? (
          <SignInPrompt text="Sign in to start a thread." />
        ) : null}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <form role="search" className="relative flex-1" onSubmit={(e) => e.preventDefault()}>
          <label htmlFor="thread-search" className="sr-only">Search threads</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input id="thread-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search this competition's threads…" className="pl-9" maxLength={80} />
        </form>
        <div className="flex items-center gap-2">
          <label htmlFor="thread-sort" className="text-sm text-muted">Sort</label>
          <Select id="thread-sort" value={state.sort} onChange={(e) => set({ sort: e.target.value })} className="w-44">
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </Select>
        </div>
      </div>

      {query.isPending ? (
        <SkeletonRows rows={6} />
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
            icon={<MessageSquare className="h-5 w-5" />}
            title="No threads yet"
            description="Start the conversation — questions about the data, evaluation or team-ups are all welcome."
            action={me.data ? <LinkButton href={newHref}>Start a thread</LinkButton> : undefined}
          />
        )
      ) : (
        <>
          <ul className={cn("divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface", query.isPlaceholderData && "opacity-60")}>
            {query.data.items.map((t) => (
              <li key={t.id} className="flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {t.pinned ? <Pin className="h-3.5 w-3.5 text-info" aria-label="Pinned" /> : null}
                    <Link href={`/discussions/t/${t.id}`} className="font-medium text-fg hover:text-accent-strong">{t.title}</Link>
                    {t.is_announcement ? <Badge tone="info" icon={<Megaphone className="h-3 w-3" aria-hidden />}>Announcement</Badge> : null}
                    {t.has_accepted_answer ? <Badge tone="success" icon={<CheckCircle2 className="h-3 w-3" aria-hidden />}>Answered</Badge> : null}
                    {t.locked ? <Badge tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Locked</Badge> : null}
                    {t.hidden ? <Badge tone="warning" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Hidden</Badge> : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-subtle">
                    <UserLink user={t.author} size={16} className="text-xs" />
                    <span aria-hidden>·</span>
                    <span>started <time dateTime={t.created_at} title={formatDateTime(t.created_at)}>{relativeTime(t.created_at)}</time></span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4 text-xs text-muted sm:text-right">
                  <span className="inline-flex items-center gap-1" aria-label={`${t.reply_count} replies`}>
                    <MessageSquare className="h-3.5 w-3.5" aria-hidden /> {formatNumber(t.reply_count)}
                  </span>
                  <time dateTime={t.last_activity_at} title={formatDateTime(t.last_activity_at)}>active {relativeTime(t.last_activity_at)}</time>
                </div>
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
    <Suspense fallback={<SkeletonRows rows={6} />}>
      <Discussion />
    </Suspense>
  );
}
