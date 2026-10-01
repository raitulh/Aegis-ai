"use client";

import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import {
  Award,
  Bell,
  CalendarClock,
  CheckCheck,
  GitMerge,
  Gavel,
  GraduationCap,
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
import { Select } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows, Spinner } from "@/components/ui/states";
import { errorMessage, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
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

function NotificationRow({ n, onRead, busy }: { n: Notification; onRead: (id: string) => Promise<void>; busy: boolean }) {
  const router = useRouter();
  const Icon = KIND_ICON[n.kind] ?? Bell;
  const unread = !n.read_at;
  const label = NOTIFICATION_KINDS[n.kind]?.label ?? titleCase(n.kind);
  const external = n.link && !n.link.startsWith("/");

  const open = async () => {
    if (unread) await onRead(n.id).catch(() => undefined);
    if (!n.link) return;
    if (external) window.open(n.link, "_blank", "noopener,noreferrer");
    else router.push(n.link);
  };

  return (
    <li className={cn("group relative flex gap-3 px-4 py-3.5 transition-colors", unread ? "bg-accent-soft/40" : "hover:bg-surface-2/60")}>
      <span className={cn("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full", unread ? "bg-accent-soft text-accent-strong" : "bg-surface-2 text-muted")}>
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {n.link ? (
            <button type="button" onClick={open} className="text-left text-sm font-medium text-fg hover:text-accent-strong hover:underline">
              {n.title}
            </button>
          ) : (
            <p className="text-sm font-medium text-fg">{n.title}</p>
          )}
          {n.group_count > 1 ? <Badge tone="accent" title="Similar updates were grouped together">{n.group_count} updates</Badge> : null}
          {unread ? <span className="sr-only">(unread)</span> : null}
        </div>
        {n.body ? <p className="mt-0.5 line-clamp-2 text-sm text-muted">{n.body}</p> : null}
        <p className="mt-1 text-xs text-subtle">
          {label} · <time dateTime={n.created_at} title={formatDateTime(n.created_at)}>{relativeTime(n.created_at)}</time>
        </p>
      </div>
      <div className="flex shrink-0 items-start gap-2">
        {unread ? (
          <>
            <span className="mt-2 h-2 w-2 rounded-full bg-accent" aria-hidden />
            <Button variant="ghost" size="sm" loading={busy} onClick={() => onRead(n.id)} aria-label={`Mark “${n.title}” as read`}>
              <span className="hidden sm:inline">Mark read</span>
              <CheckCheck className="h-4 w-4 sm:hidden" aria-hidden />
            </Button>
          </>
        ) : null}
      </div>
    </li>
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

  if (me.isPending || !me.data) return <Spinner />;
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const anyUnread = me.data.unread_notifications > 0 || items.some((n) => !n.read_at);

  return (
    <Container size="md">
      <PageHeader
        title="Notifications"
        description="Updates about your competitions, teams, credentials and discussions."
        actions={
          <>
            <LinkButton href="/settings/notifications" variant="ghost" icon={<Settings className="h-4 w-4" />}>Preferences</LinkButton>
            <Button variant="secondary" icon={<CheckCheck className="h-4 w-4" />} loading={markingAll} disabled={!anyUnread} onClick={markAll}>
              Mark all read
            </Button>
          </>
        }
      />
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="inline-flex rounded-[var(--radius-md)] border border-border bg-surface p-0.5" role="group" aria-label="Filter by read state">
          {[
            { v: "", label: "All" },
            { v: "1", label: "Unread" },
          ].map((o) => (
            <button
              key={o.v || "all"}
              type="button"
              aria-pressed={filters.unread === o.v}
              onClick={() => setFilters({ unread: o.v })}
              className={cn(
                "rounded-[calc(var(--radius-md)-2px)] px-3 py-1.5 text-sm font-medium transition-colors",
                filters.unread === o.v ? "bg-surface-3 text-fg" : "text-muted hover:text-fg",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          <span className="shrink-0">Type</span>
          <Select value={filters.kind ?? ""} onChange={(e) => setFilters({ kind: e.target.value })} className="h-9 sm:w-56">
            <option value="">All types</option>
            {Object.entries(NOTIFICATION_KINDS).map(([k, m]) => (
              <option key={k} value={k}>{m.label}</option>
            ))}
          </Select>
        </label>
      </div>

      <div className="pb-16">
        {list.isPending ? (
          <SkeletonRows rows={6} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !items.length ? (
          unreadOnly || kind ? (
            <EmptyState
              icon={<Bell className="h-5 w-5" />}
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
            <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
              {items.map((n) => (
                <NotificationRow key={n.id} n={n} onRead={markRead} busy={busyIds.has(n.id)} />
              ))}
            </ul>
            <div className="mt-4 flex justify-center">
              {list.hasNextPage ? (
                <Button variant="secondary" loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>Load more</Button>
              ) : (
                <p className="text-xs text-subtle">You&apos;ve reached the end.</p>
              )}
            </div>
          </>
        )}
      </div>
    </Container>
  );
}

export default function NotificationsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <NotificationsInner />
    </Suspense>
  );
}
