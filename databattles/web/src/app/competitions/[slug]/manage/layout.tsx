"use client";

import {
  BarChart3,
  CalendarClock,
  ClipboardList,
  FlaskConical,
  Gavel,
  LayoutDashboard,
  Megaphone,
  Settings2,
  Trophy,
  UserCog,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { useCompetitionDetail, useManage } from "@/components/organizer/shared";
import { LinkButton } from "@/components/ui/button";
import { NotFoundState, PermissionDenied, Skeleton, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";

function ManageNav({ slug, scoringMode }: { slug: string; scoringMode: string }) {
  const pathname = usePathname();
  const base = `/competitions/${slug}/manage`;
  const items = [
    { href: base, label: "Overview", icon: LayoutDashboard, exact: true },
    { href: `${base}/settings`, label: "Settings", icon: Settings2 },
    { href: `${base}/evaluation`, label: "Evaluation", icon: FlaskConical, hidden: scoringMode !== "automatic" },
    { href: `${base}/judging`, label: "Judging", icon: Gavel, hidden: scoringMode !== "judged" },
    { href: `${base}/participants`, label: "Participants", icon: Users },
    { href: `${base}/submissions`, label: "Submissions", icon: ClipboardList, hidden: scoringMode !== "automatic" },
    { href: `${base}/announcements`, label: "Announcements", icon: Megaphone },
    { href: `${base}/people`, label: "Staff & sponsors", icon: UserCog },
    { href: `${base}/schedule`, label: "Schedule & awards", icon: CalendarClock },
    { href: `${base}/results`, label: "Results & certificates", icon: Trophy },
    { href: `${base}/analytics`, label: "Analytics", icon: BarChart3 },
  ].filter((i) => !i.hidden);
  return (
    <nav aria-label="Manage competition" className="lg:sticky lg:top-20 lg:self-start">
      <p className="mb-2 hidden px-3 text-xs font-medium uppercase tracking-wider text-subtle lg:block">Organizer</p>
      <ul className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0">
        {items.map((i) => {
          const active = i.exact ? pathname === i.href : pathname === i.href || pathname.startsWith(i.href + "/");
          return (
            <li key={i.href} className="shrink-0">
              <Link
                href={i.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] px-3 py-2 text-sm transition-colors",
                  active ? "bg-surface-2 font-medium text-fg" : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                <i.icon className={cn("h-4 w-4", active ? "text-accent-strong" : "text-subtle")} aria-hidden />
                {i.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export default function ManageLayout({ children }: { children: ReactNode }) {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  const detail = useCompetitionDetail(slug);

  if (manage.isPending) {
    return (
      <div className="grid gap-6 lg:grid-cols-[210px_minmax(0,1fr)]" role="status" aria-label="Loading organizer tools">
        <div className="flex gap-2 lg:flex-col">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9 w-28 lg:w-full" />)}
        </div>
        <div className="space-y-4">
          <Skeleton className="h-8 w-1/3" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  if (manage.isError) {
    const e = manage.error;
    if (e instanceof ApiError && e.status === 401) return <PermissionDenied signIn />;
    if (e instanceof ApiError && e.status === 403) {
      return (
        <div className="space-y-4">
          <PermissionDenied message="Only organizers of this competition can open the organizer tools. Ask an organizer to add you as staff." />
          {detail.data?.viewer.roles.includes("judge") ? (
            <div className="flex justify-center">
              <LinkButton href={`/judge/${slug}`} variant="secondary" icon={<Gavel className="h-4 w-4" />}>Open your judging queue</LinkButton>
            </div>
          ) : null}
        </div>
      );
    }
    if (e instanceof ApiError && e.status === 404) return <NotFoundState what="competition" />;
    return <ErrorState error={e} onRetry={() => manage.refetch()} />;
  }

  const scoringMode = String(manage.data.raw.scoring_mode ?? "automatic");
  return (
    <div className="grid gap-6 lg:grid-cols-[210px_minmax(0,1fr)]">
      <ManageNav slug={slug} scoringMode={scoringMode} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
