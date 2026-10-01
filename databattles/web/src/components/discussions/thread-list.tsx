"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Check,
  CircleCheck,
  EyeOff,
  GitPullRequest,
  GraduationCap,
  Hash,
  Layers,
  LifeBuoy,
  Lock,
  Megaphone,
  MessageSquare,
  MessagesSquare,
  Pin,
  Search,
  Sparkles,
  Trophy,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Avatar } from "@/components/ui/avatar";
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

/** Glyph for a category (known slugs get a matching icon; anything else gets a neutral hash). */
const CATEGORY_ICONS: Record<string, LucideIcon> = {
  announcements: Megaphone,
  general: MessagesSquare,
  help: LifeBuoy,
  competitions: Trophy,
  learning: GraduationCap,
  "open-source": GitPullRequest,
  showcase: Sparkles,
};

export function CategoryIcon({ slug, className }: { slug: string | null | undefined; className?: string }) {
  const Icon = (slug ? CATEGORY_ICONS[slug] : undefined) ?? Hash;
  return <Icon className={className} aria-hidden />;
}

function ThreadItem({ t }: { t: ThreadRow }) {
  return (
    <li
      className={cn(
        "relative grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3.5 px-4 py-4 transition-colors hover:bg-surface-2/50 sm:px-5 md:grid-cols-[auto_minmax(0,1fr)_5.5rem_8.5rem] md:items-center",
        t.pinned && "bg-accent-soft/25",
      )}
    >
      {t.pinned || t.is_announcement ? (
        <span aria-hidden className={cn("absolute inset-y-3 left-0 w-0.5 rounded-full", t.is_announcement ? "bg-info" : "bg-brand")} />
      ) : null}
      <span className="relative mt-0.5 md:mt-0" aria-hidden>
        <Avatar name={t.author?.display_name ?? "Deleted user"} src={t.author?.avatar_url} size={36} />
        {t.has_accepted_answer ? (
          <span className="absolute -bottom-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-success text-bg ring-2 ring-surface">
            <Check className="h-2.5 w-2.5" strokeWidth={3} />
          </span>
        ) : null}
      </span>
      <div className="min-w-0">
        <ThreadFlags t={t} className="mb-1.5" />
        <Link
          href={`/discussions/t/${t.id}`}
          className="rounded-sm text-[15px] font-medium leading-snug tracking-[-0.01em] text-fg transition-colors hover:text-accent-strong focus-visible:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
        >
          {t.title}
        </Link>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
          {t.author ? (
            <Link href={`/u/${t.author.handle}`} className="font-medium text-muted transition-colors hover:text-accent-strong">{t.author.display_name}</Link>
          ) : (
            <span>Deleted user</span>
          )}
          <span aria-hidden>·</span>
          <time dateTime={t.created_at} title={formatDateTime(t.created_at)}>started {relativeTime(t.created_at)}</time>
          <span aria-hidden className="md:hidden">·</span>
          <time className="md:hidden" dateTime={t.last_activity_at} title={formatDateTime(t.last_activity_at)}>active {relativeTime(t.last_activity_at)}</time>
          <span aria-hidden className="md:hidden">·</span>
          <span className="tabular inline-flex items-center gap-1 md:hidden">
            <MessageSquare className="h-3 w-3" aria-hidden />
            {compactNumber(t.reply_count)} {t.reply_count === 1 ? "reply" : "replies"}
          </span>
        </div>
      </div>
      <div className="hidden flex-col items-center text-center md:flex">
        <span
          className={cn(
            "tabular inline-flex h-8 min-w-12 items-center justify-center gap-1 rounded-[var(--radius-sm)] border px-2 text-sm font-semibold",
            t.has_accepted_answer ? "border-[color-mix(in_oklab,var(--success)_35%,transparent)] bg-success-soft text-success" : t.reply_count > 0 ? "border-border bg-bg-elevated text-fg" : "border-dashed border-border text-subtle",
          )}
        >
          {t.has_accepted_answer ? <CircleCheck className="h-3.5 w-3.5" aria-hidden /> : null}
          {compactNumber(t.reply_count)}
          <span className="sr-only">{t.reply_count === 1 ? " reply" : " replies"}{t.has_accepted_answer ? ", answered" : ""}</span>
        </span>
      </div>
      <div className="hidden text-right md:block">
        <span className="sr-only">Last activity </span>
        <time className="text-sm text-muted" dateTime={t.last_activity_at} title={formatDateTime(t.last_activity_at)}>{relativeTime(t.last_activity_at)}</time>
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
      <div role="search" className="mb-5 flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-glass p-3 shadow-card backdrop-blur-md sm:flex-row sm:items-center">
        <div className="relative min-w-0 flex-1">
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
        <div className="flex min-w-0 items-center gap-3">
          <Select aria-label="Sort discussions" value={state.sort} onChange={(e) => set({ sort: e.target.value })} className="min-w-0 flex-1 sm:w-44 sm:flex-none">
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </Select>
          {signedIn ? (
            <Checkbox
              label="Mine"
              className="h-10 shrink-0 items-center rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 px-3 [&_input]:mt-0"
              checked={state.mine === "1"}
              onChange={(e) => set({ mine: e.target.checked ? "1" : "" })}
            />
          ) : null}
        </div>
      </div>

      {threads.isPending ? (
        <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card" role="status" aria-label="Loading discussions">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3.5 px-4 py-4 sm:px-5" style={{ opacity: 1 - i * 0.1 }}>
              <Skeleton className="h-9 w-9 shrink-0 rounded-full" />
              <div className="min-w-0 flex-1">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="mt-2 h-3 w-1/3" />
              </div>
              <Skeleton className="hidden h-8 w-12 md:block" />
              <Skeleton className="hidden h-3 w-24 md:block" />
            </div>
          ))}
        </div>
      ) : threads.isError ? (
        <ErrorState error={threads.error} onRetry={() => threads.refetch()} />
      ) : threads.data.items.length === 0 ? (
        filtered ? (
          <NoResults onReset={() => { setSearch(""); reset(); }} />
        ) : (
          <EmptyState icon={<MessagesSquare />} title="No discussions yet" description="Start the conversation — ask a question or share what you're working on." action={emptyAction} />
        )
      ) : (
        <div aria-busy={threads.isFetching || undefined} className={cn("transition-opacity", threads.isPlaceholderData && "opacity-70")}>
          <div className="mb-3 flex items-center gap-3 text-sm text-muted" aria-live="polite">
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
              <span className="tabular font-medium text-fg">{threads.data.total.toLocaleString()}</span> {threads.data.total === 1 ? "discussion" : "discussions"}
            </span>
          </div>
          <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
            <div aria-hidden className="hidden grid-cols-[2.25rem_minmax(0,1fr)_5.5rem_8.5rem] gap-x-3.5 border-b border-border bg-bg-elevated/70 px-5 py-2.5 text-eyebrow text-subtle md:grid">
              <span className="col-span-2">Topic</span>
              <span className="text-center">Replies</span>
              <span className="text-right">Activity</span>
            </div>
            <ul className="divide-y divide-border" aria-label="Discussions">
              {threads.data.items.map((t) => <ThreadItem key={t.id} t={t} />)}
            </ul>
          </div>
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

/** Category navigation: chips on small screens, a vertical channel list (with descriptions) on large screens. */
export function CategoryNav({ current }: { current?: string }) {
  const cats = useCategories();
  const total = cats.data?.reduce((n, c) => n + c.thread_count, 0);
  const item = (href: string, label: string, count: number | undefined, active: boolean, opts: { icon: ReactNode; description?: string | null; staffOnly?: boolean }) => (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex h-9 shrink-0 items-center gap-2 rounded-full border px-3 text-sm transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
        "lg:h-auto lg:items-start lg:gap-3 lg:rounded-[var(--radius-md)] lg:border-transparent lg:px-2.5 lg:py-2",
        active
          ? "border-[color-mix(in_oklab,var(--accent)_40%,transparent)] bg-accent-soft text-accent-strong lg:bg-accent-soft"
          : "border-border bg-bg-elevated/60 text-muted hover:border-border-strong hover:text-fg lg:bg-transparent lg:hover:bg-surface-2",
      )}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center [&_svg]:h-3.5 [&_svg]:w-3.5",
          "lg:mt-px lg:h-7 lg:w-7 lg:rounded-lg lg:border lg:shadow-[inset_0_1px_0_var(--hairline-highlight)]",
          active ? "lg:border-[color-mix(in_oklab,var(--accent)_35%,transparent)] lg:bg-surface-2" : "text-subtle lg:border-border lg:bg-surface-2 group-hover:text-fg",
        )}
      >
        {opts.icon}
      </span>
      <span className="min-w-0 lg:flex-1">
        <span className={cn("flex items-center gap-1.5 truncate", active ? "font-medium" : "lg:text-fg")}>
          <span className="truncate">{label}</span>
          {opts.staffOnly ? (
            <>
              <Lock className="h-3 w-3 shrink-0 text-subtle" aria-hidden />
              <span className="sr-only">(staff-only posting)</span>
            </>
          ) : null}
        </span>
        {opts.description ? <span className="mt-0.5 hidden truncate text-xs text-subtle lg:block">{opts.description}</span> : null}
      </span>
      {count !== undefined ? <span className="tabular text-xs text-subtle lg:mt-1">{compactNumber(count)}</span> : null}
    </Link>
  );
  return (
    <nav aria-label="Discussion categories">
      <h2 className="mb-3 hidden px-2.5 text-eyebrow text-subtle lg:block">Categories</h2>
      {cats.isPending ? (
        <div className="flex gap-2 lg:flex-col" role="status" aria-label="Loading categories">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-9 w-28 rounded-full lg:h-11 lg:w-full lg:rounded-[var(--radius-md)]" />)}
        </div>
      ) : cats.isError ? (
        <p className="px-3 text-sm text-muted">
          Categories unavailable.{" "}
          <button type="button" className="text-accent-strong hover:underline" onClick={() => cats.refetch()}>Retry</button>
        </p>
      ) : (
        <ul className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:-mx-6 sm:px-6 lg:mx-0 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:px-0">
          <li>{item("/discussions", "All discussions", total, !current, { icon: <Layers />, description: "Threads from every category" })}</li>
          {cats.data.map((c) => (
            <li key={c.id}>
              {item(`/discussions/c/${c.slug}`, c.name, c.thread_count, current === c.slug, {
                icon: <CategoryIcon slug={c.slug} />,
                description: c.description,
                staffOnly: c.staff_only_posting,
              })}
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
