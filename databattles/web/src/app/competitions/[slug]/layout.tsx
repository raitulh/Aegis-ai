"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { CompetitionProvider, ck, useCompetitionQuery, useSlug } from "@/components/competition/context";
import { CompetitionHero, CompetitionHeroSkeleton } from "@/components/competition/hero";
import type { Announcement } from "@/components/competition/types";
import { Container } from "@/components/ui/page";
import { ErrorState, NotFoundState, PermissionDenied, SignInPrompt } from "@/components/ui/states";
import { NavTabs } from "@/components/ui/tabs";
import { ApiError, get } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { CompetitionDetail } from "@/lib/types";

function CountPill({ n, label }: { n: number; label: string }) {
  if (!n) return null;
  return (
    <>
      <span className="tabular ml-1.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-accent-soft px-1 text-[11px] font-semibold text-accent-strong ring-1 ring-inset ring-[color-mix(in_oklab,var(--accent)_28%,transparent)]" aria-hidden>
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

  // On narrow screens the tab strip scrolls sideways: keep the active tab in view (horizontal only, never the page).
  const pathname = usePathname();
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const nav = wrap.current?.querySelector("nav");
    const active = nav?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return;
    const left = active.offsetLeft - nav.offsetLeft;
    if (left < nav.scrollLeft || left + active.offsetWidth > nav.scrollLeft + nav.clientWidth) {
      nav.scrollLeft = Math.max(0, left - 16);
    }
  }, [pathname]);

  return (
    // `contents` keeps the sticky tab bar's containing block the page container.
    <div ref={wrap} className="contents">
      <NavTabs
        sticky
        className="mt-8"
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
    </div>
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

  if (query.isPending) return <CompetitionHeroSkeleton />;

  if (query.isError) {
    const e = query.error;
    const signedOut = me.isSuccess && me.data === null;
    return (
      <Container className="py-12">
        <h1 className="sr-only">Competition unavailable</h1>
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
        <div className="pt-8">{children}</div>
      </Container>
    </CompetitionProvider>
  );
}
