"use client";
import { Menu } from "@base-ui-components/react/menu";
import { Bell, CheckCheck } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useNotifications, useInvalidate } from "@/lib/queries";
import { timeAgo } from "@/lib/utils";

export function NotificationsMenu() {
  const { data } = useNotifications();
  const invalidate = useInvalidate();
  const items = data?.items ?? [];
  const unread = items.filter((n) => !n.read_at).length;

  async function markAll() {
    await api.post("/notifications/read-all").catch(() => {});
    invalidate("notifications");
  }

  return (
    <Menu.Root>
      <Menu.Trigger aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"} className="relative rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)] focus-ring">
        <Bell className="h-4 w-4" aria-hidden />
        {unread > 0 ? <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-[var(--color-critical)] px-1 text-[9px] font-semibold text-white">{unread}</span> : null}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={8} align="end">
          <Menu.Popup className="z-50 w-80 rounded-[var(--radius-lg)] border border-[var(--color-border-strong)] bg-[var(--color-surface-2)] shadow-[var(--shadow-lg)]">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
              <span className="text-sm font-semibold">Notifications</span>
              {unread > 0 ? (
                <button type="button" onClick={markAll} className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                  <CheckCheck className="h-3.5 w-3.5" /> Mark all read
                </button>
              ) : null}
            </div>
            <div className="max-h-96 overflow-y-auto">
              {items.length === 0 ? (
                <p className="px-3 py-8 text-center text-sm text-[var(--color-text-subtle)]">You're all caught up.</p>
              ) : (
                items.slice(0, 12).map((n) => (
                  <Menu.Item
                    key={n.id}
                    render={<Link href={safeLink(n.link)} />}
                    className="block border-b border-[var(--color-border)]/60 px-3 py-2.5 outline-none data-[highlighted]:bg-[var(--color-surface-3)]"
                  >
                    <div className="flex items-start gap-2">
                      {!n.read_at ? (
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent)]" aria-label="Unread" />
                      ) : (
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0" />
                      )}
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{n.title}</p>
                        {n.body ? <p className="mt-0.5 line-clamp-2 text-xs text-[var(--color-text-muted)]">{n.body}</p> : null}
                        <p className="mt-0.5 text-[10px] text-[var(--color-text-subtle)]">{timeAgo(n.created_at)}</p>
                      </div>
                    </div>
                  </Menu.Item>
                ))
              )}
            </div>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** Notification links are generated server-side, but are still constrained to in-app paths. */
function safeLink(link: string | null): string {
  return link && link.startsWith("/") && !link.startsWith("//") ? link : "/dashboard";
}
