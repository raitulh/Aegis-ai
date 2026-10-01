"use client";

import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import {
  Award,
  Bell,
  BellOff,
  CalendarClock,
  Check,
  CheckCheck,
  GitMerge,
  Gavel,
  GraduationCap,
  Inbox,
  Medal,
  Megaphone,
  MessageSquare,
  Settings,
  ShieldAlert,
  Trophy,
  University,
  UploadCloud,
  Users,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { NOTIFICATION_KINDS } from "@/components/profile/notification-kinds";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/extras";
import { Select } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { errorMessage, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { useNow, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CursorPage, Notification } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 20;

const KIND_ICON: Record<string, LucideIcon> = {
  team_invite: Users,
  team_update: Users,
  submission_scored: UploadCloud,
  submission_rejected: XCircle,
  deadline_reminder: CalendarClock,
  results_published: Trophy,
  certificate_issued: Award,
  badge_awarded: Medal,
  mention: MessageSquare,
  reply: MessageSquare,
  announcement: Megaphone,
  contribution_merged: GitMerge,
  github_sync: GitMerge,
  moderation: ShieldAlert,
  org_membership: University,
  course_completed: GraduationCap,
  judging: Gavel,
};

/** Icon chip tint per notification group (the kind label is always shown too, so colour is never the only cue). */
const GROUP_TONE: Record<string, string> = {
  Competitions: "bg-accent-soft text-accent-strong",
  Achievements: "bg-success-soft text-success",
  Community: "bg-cyan-soft text-cyan",
  "Open source": "bg-info-soft text-info",
  Account: "bg-warning-soft text-warning",
};

function dayKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** "Today", "Yesterday", a weekday within the last week, otherwise a date — all in the viewer's time zone. */
function dayLabel(iso: string, now: Date): string {
  const d = new Date(iso);
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(now) - start(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff > 1 && diff < 7) return formatDate(d, { weekday: "long" });
  return formatDate(d, d.getFullYear() === now.getFullYear() ? { month: "long", day: "numeric" } : { dateStyle: "long" });
}

function groupByDay(items: Notification[], now: Date) {
  const groups: { key: string; label: string; items: Notification[] }[] = [];
  for (const n of items) {
    const key = dayKey(n.created_at);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(n);
    else groups.push({ key, label: dayLabel(n.created_at, now), items: [n] });
  }
  return groups;
}

function NotificationRow({ n, onRead, busy }: { n: Notification; onRead: (id: string) => Promise<void>; busy: boolean }) {
  const router = useRouter();
  const Icon = KIND_ICON[n.kind] ?? Bell;
  const unread = !n.read_at;
  const meta = NOTIFICATION_KINDS[n.kind];
  const label = meta?.label ?? titleCase(n.kind);
  const tone = (meta && GROUP_TONE[meta.group]) ?? "bg-surface-3 text-muted";
  const external = n.link && !n.link.startsWith("/");

  const open = async () => {
    if (unread) await onRead(n.id).catch(() => undefined);
    if (!n.link) return;
    if (external) window.open(n.link, "_blank", "noopener,noreferrer");
    else router.push(n.link);
  };

  return (
    <li className={cn("group relative flex gap-3.5 px-4 py-4 transition-colors sm:px-5", unread ? "bg-accent-soft/35 hover:bg-accent-soft/50" : "hover:bg-surface-2/50")}>
      {unread ? <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 rounded-full bg-brand" /> : null}
      <span className={cn("relative mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ring-inset ring-border", tone, !unread && "opacity-70 saturate-50")}>
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {n.link ? (
            <button
              type="button"
              onClick={open}
              className={cn(
                "rounded-sm text-left text-sm hover:text-accent-strong hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                unread ? "font-semibold text-fg" : "font-medium text-fg/85",
              )}
            >
              {n.title}
            </button>
          ) : (
            <p className={cn("text-sm", unread ? "font-semibold text-fg" : "font-medium text-fg/85")}>{n.title}</p>
          )}
          {n.group_count > 1 ? (
            <Badge tone="accent" title="Similar updates were grouped together">
              <span className="tabular">{n.group_count}</span> updates
            </Badge>
          ) : null}
          {unread ? <span className="sr-only">(unread)</span> : null}
        </div>
        {n.body ? <p className={cn("mt-0.5 line-clamp-2 text-sm", unread ? "text-muted" : "text-subtle")}>{n.body}</p> : null}
        <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-xs text-subtle">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.12em]">{label}</span>
          <span aria-hidden>·</span>
          <time dateTime={n.created_at} title={formatDateTime(n.created_at)}>{relativeTime(n.created_at)}</time>
        </p>
      </div>
      <div className="flex shrink-0 items-start gap-2">
        {unread ? (
          <>
            <span className="mt-3 hidden h-2 w-2 rounded-full bg-accent shadow-[0_0_10px_var(--accent)] sm:block" aria-hidden />
            <Button variant="ghost" size="sm" loading={busy} onClick={() => onRead(n.id)} aria-label={`Mark “${n.title}” as read`} className="max-sm:h-9 max-sm:w-9 max-sm:px-0">
              <span className="hidden sm:inline">Mark read</span>
              {busy ? null : <Check className="h-4 w-4 sm:hidden" aria-hidden />}
            </Button>
          </>
        ) : null}
      </div>
    </li>
  );
}

function ListSkeleton() {
  return (
    <div role="status" aria-label="Loading notifications" className="space-y-8">
      {[4, 3].map((rows, g) => (
        <div key={g}>
          <Skeleton className="mb-3 h-3 w-20" />
          <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
            {Array.from({ length: rows }).map((_, i) => (
              <div key={i} className="flex items-start gap-3.5 px-5 py-4">
                <Skeleton className="h-9 w-9 shrink-0 rounded-xl" />
                <div className="flex-1">
                  <Skeleton className="h-3.5 w-2/3" />
                  <Skeleton className="mt-2 h-3 w-1/2" />
                  <Skeleton className="mt-2.5 h-2.5 w-28" />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function NotificationsInner() {
  const me = useRequireAuth();
  const qc = useQueryClient();
  const [filters, setFilters] = useUrlState({ unread: "", kind: "" });
  const unreadOnly = filters.unread === "1";
  const kind = filters.kind || undefined;
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [markingAll, setMarkingAll] = useState(false);
  const now = useNow(60_000);

  const list = useInfiniteQuery({
    queryKey: qk.notifications({ unread: unreadOnly, kind: kind ?? null }),
    queryFn: ({ pageParam, signal }) =>
      get<CursorPage<Notification>>("/notifications", { cursor: pageParam, limit: PAGE_SIZE, unread: unreadOnly || undefined, kind }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: Boolean(me.data),
  });

  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ["notifications", "list"] }), qc.invalidateQueries({ queryKey: qk.unread }), qc.invalidateQueries({ queryKey: qk.me })]);

  const markRead = async (id: string) => {
    setBusyIds((s) => new Set(s).add(id));
    try {
      await post("/notifications/read", { ids: [id] });
      await refresh();
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    } finally {
      setBusyIds((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
    }
  };

  const markAll = async () => {
    setMarkingAll(true);
    try {
      await post("/notifications/read", { all: true });
      await refresh();
      toast.success("All notifications marked as read");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setMarkingAll(false);
    }
  };

  if (me.isPending || !me.data) return <PageSkeleton />;
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const anyUnread = me.data.unread_notifications > 0 || items.some((n) => !n.read_at);
  const groups = groupByDay(items, now);
  const unreadCount = me.data.unread_notifications;

  return (
    <Container size="md">
      <PageHeader
        eyebrow="Inbox"
        icon={<Bell />}
        title="Notifications"
        description="Updates about your competitions, teams, credentials and discussions."
        meta={
          unreadCount > 0 ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_8px_var(--accent)]" aria-hidden />
              <span className="tabular font-medium text-fg">{formatNumber(unreadCount)}</span> unread
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <CheckCheck className="h-3.5 w-3.5 text-success" aria-hidden /> You&apos;re all caught up
            </span>
          )
        }
        actions={
          <>
            <LinkButton href="/settings/notifications" variant="ghost" icon={<Settings className="h-4 w-4" />}>Preferences</LinkButton>
            <Button variant="secondary" icon={<CheckCheck className="h-4 w-4" />} loading={markingAll} disabled={!anyUnread} onClick={markAll}>
              Mark all read
            </Button>
          </>
        }
      />
      <div className="sticky top-14 z-20 -mx-4 mb-6 border-y border-border bg-[var(--glass-strong)] px-4 py-2.5 backdrop-blur-xl sm:mx-0 sm:rounded-[var(--radius-lg)] sm:border sm:px-3 lg:top-[4.75rem]">
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
          <SegmentedControl
            label="Filter by read state"
            value={filters.unread === "1" ? "1" : "all"}
            onChange={(v) => setFilters({ unread: v === "1" ? "1" : "" })}
            options={[
              { value: "all", label: "All", icon: <Inbox /> },
              { value: "1", label: "Unread", icon: <Bell />, count: unreadCount > 0 ? unreadCount : undefined },
            ]}
          />
          <label className="flex items-center gap-2 text-sm text-muted">
            <span className="shrink-0 text-eyebrow text-subtle">Type</span>
            <Select value={filters.kind ?? ""} onChange={(e) => setFilters({ kind: e.target.value })} className="h-9 min-w-0 flex-1 sm:w-56 sm:flex-none">
              <option value="">All types</option>
              {Object.entries(NOTIFICATION_KINDS).map(([k, m]) => (
                <option key={k} value={k}>{m.label}</option>
              ))}
            </Select>
          </label>
        </div>
      </div>

      <div className="pb-16">
        {list.isPending ? (
          <ListSkeleton />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !items.length ? (
          unreadOnly || kind ? (
            <EmptyState
              icon={<BellOff className="h-5 w-5" />}
              title={unreadOnly ? "No unread notifications" : "No notifications of this type"}
              description="You're all caught up."
              action={<Button variant="secondary" onClick={() => setFilters({ unread: "", kind: "" })}>Show all notifications</Button>}
            />
          ) : (
            <EmptyState
              icon={<Bell className="h-5 w-5" />}
              title="No notifications yet"
              description="Join a competition or enroll in a course — score updates, team invites and certificates will show up here."
              action={<LinkButton href="/competitions">Browse competitions</LinkButton>}
            />
          )
        ) : (
          <>
            <div className="space-y-8">
              {groups.map((g, gi) => {
                const unreadInGroup = g.items.filter((n) => !n.read_at).length;
                // While more pages exist, the last loaded day may be cut at a page boundary: show its counts as "N+".
                const partial = gi === groups.length - 1 && Boolean(list.hasNextPage);
                const plus = partial ? "+" : "";
                return (
                  <section key={g.key} aria-labelledby={`day-${g.key}`} className={gi < 2 ? "animate-rise" : undefined} style={gi < 2 ? { animationDelay: `${gi * 60}ms` } : undefined}>
                    <div className="mb-2.5 flex items-center gap-3">
                      <h2 id={`day-${g.key}`} className="text-eyebrow text-fg">{g.label}</h2>
                      <span aria-hidden className="h-px flex-1 bg-border" />
                      <span className="tabular text-xs text-subtle">
                        {g.items.length}
                        {plus} {g.items.length === 1 && !partial ? "update" : "updates"}
                        {unreadInGroup ? (
                          <span className="text-accent-strong">
                            {" "}· {unreadInGroup}
                            {plus} unread
                          </span>
                        ) : null}
                      </span>
                    </div>
                    <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
                      {g.items.map((n) => (
                        <NotificationRow key={n.id} n={n} onRead={markRead} busy={busyIds.has(n.id)} />
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
            <div className="mt-6 flex justify-center">
              {list.hasNextPage ? (
                <Button variant="secondary" loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>Load more</Button>
              ) : (
                <p className="inline-flex items-center gap-2 text-xs text-subtle">
                  <span aria-hidden className="h-px w-8 bg-border" />
                  You&apos;ve reached the end.
                  <span aria-hidden className="h-px w-8 bg-border" />
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </Container>
  );
}

function PageSkeleton() {
  return (
    <Container size="md" className="pb-16 pt-10 sm:pt-12">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="mt-4 h-9 w-56" />
      <Skeleton className="mb-10 mt-4 h-4 w-80 max-w-full" />
      <ListSkeleton />
    </Container>
  );
}

export default function NotificationsPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <NotificationsInner />
    </Suspense>
  );
}
