"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, type ReactNode } from "react";

import { CompetitionProvider, ck, useCompetitionQuery, useSlug } from "@/components/competition/context";
import { CompetitionHero } from "@/components/competition/hero";
import type { Announcement } from "@/components/competition/types";
import { Container } from "@/components/ui/page";
import { ErrorState, NotFoundState, PermissionDenied, SignInPrompt, Skeleton } from "@/components/ui/states";
import { NavTabs } from "@/components/ui/tabs";
import { ApiError, get } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { CompetitionDetail } from "@/lib/types";

function HeroSkeleton() {
  return (
    <div role="status" aria-label="Loading competition">
      <Skeleton className="h-32 w-full rounded-none sm:h-44" />
      <Container className="relative -mt-20 sm:-mt-24">
        <div className="rounded-[var(--radius-xl)] border border-border bg-surface p-5 sm:p-7">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="mt-4 h-8 w-2/3" />
          <Skeleton className="mt-3 h-4 w-1/2" />
          <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
          </div>
        </div>
        <Skeleton className="mt-6 h-10 w-full" />
      </Container>
    </div>
  );
}

function CountPill({ n, label }: { n: number; label: string }) {
  if (!n) return null;
  return (
    <>
      <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 text-xs font-semibold text-accent-strong" aria-hidden>
        {n}
      </span>
      <span className="sr-only">, {label}</span>
    </>
  );
}

function CompetitionTabs({ comp }: { comp: CompetitionDetail }) {
  const base = `/competitions/${comp.slug}`;
  const v = comp.viewer;
  // Unread announcements for the badge on "Overview" (shares the cache with the overview page).
  const announcements = useQuery({
    queryKey: ck.announcements(comp.slug),
    queryFn: () => get<Announcement[]>(`/competitions/${encodeURIComponent(comp.slug)}/announcements`),
    enabled: v.is_authenticated && comp.announcement_count > 0,
    staleTime: 60_000,
  });
  const unread = announcements.data?.filter((a) => !a.is_read && a.status === "published").length ?? 0;
  const showData = Boolean(comp.dataset) || comp.starter_assets.length > 0 || comp.scoring_mode === "automatic";
  return (
    <NavTabs
      className="mt-6"
      items={[
        { href: base, label: <>Overview<CountPill n={unread} label={`${unread} unread announcements`} /></>, exact: true },
        { href: `${base}/data`, label: "Data", hidden: !showData },
        { href: `${base}/leaderboard`, label: comp.status === "completed" ? "Results" : "Leaderboard", hidden: comp.scoring_mode === "none" },
        { href: `${base}/submissions`, label: "Submissions", hidden: !v.is_participant || comp.scoring_mode === "none" },
        {
          href: `${base}/team`,
          label: <>Team<CountPill n={v.pending_invitations} label={`${v.pending_invitations} pending invitations`} /></>,
          hidden: !v.is_participant && v.pending_invitations === 0,
        },
        { href: `${base}/discussion`, label: "Discussion" },
        { href: `${base}/manage`, label: "Manage", hidden: !v.roles.includes("organizer") },
      ]}
    />
  );
}

export default function CompetitionLayout({ children }: { children: ReactNode }) {
  const slug = useSlug();
  const query = useCompetitionQuery(slug);
  const me = useMe();
  const title = query.data?.title;

  useEffect(() => {
    if (title) document.title = `${title} · DataBattles`;
  }, [title]);

  // The viewer context is per-user: refetch once when the session changes (sign-in/out in another tab or flow).
  const viewerAuthed = query.data?.viewer.is_authenticated;
  const meAuthed = me.isSuccess ? Boolean(me.data) : undefined;
  const synced = useRef("");
  const refetch = query.refetch;
  useEffect(() => {
    if (viewerAuthed === undefined || meAuthed === undefined || viewerAuthed === meAuthed) return;
    const key = `${slug}|${meAuthed}`;
    if (synced.current === key) return;
    synced.current = key;
    void refetch();
  }, [viewerAuthed, meAuthed, slug, refetch]);

  if (query.isPending) return <HeroSkeleton />;

  if (query.isError) {
    const e = query.error;
    const signedOut = me.isSuccess && me.data === null;
    return (
      <Container className="py-12">
        {e instanceof ApiError && e.status === 404 ? (
          <div className="space-y-4">
            <NotFoundState what="competition" />
            {signedOut ? (
              <div className="text-center"><SignInPrompt text="Private and invite-only competitions are visible after signing in." /></div>
            ) : null}
          </div>
        ) : e instanceof ApiError && e.status === 403 ? (
          <PermissionDenied message={e.message} signIn={signedOut} />
        ) : e instanceof ApiError && e.status === 401 ? (
          <PermissionDenied signIn />
        ) : (
          <ErrorState error={e} onRetry={() => query.refetch()} />
        )}
      </Container>
    );
  }

  const comp = query.data;
  return (
    <CompetitionProvider value={comp}>
      <CompetitionHero comp={comp} />
      <Container className="pb-16">
        <CompetitionTabs comp={comp} />
        <div className="pt-6">{children}</div>
      </Container>
    </CompetitionProvider>
  );
}
