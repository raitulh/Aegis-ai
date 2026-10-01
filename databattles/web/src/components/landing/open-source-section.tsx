"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CircleDot, GitMerge, GitPullRequest, MessageSquare, Sparkles } from "lucide-react";
import Link from "next/link";

import { osKeys } from "@/components/catalog/repo";
import type { IssueRow, OpenSourceOverview } from "@/components/catalog/types";
import { CountUp } from "@/components/motion/count-up";
import { Reveal } from "@/components/motion/reveal";
import { Avatar } from "@/components/ui/avatar";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber, relativeTime } from "@/lib/format";
import { useInView } from "@/lib/motion";
import type { Page } from "@/lib/types";
import { SectionHeading } from "./section-heading";

const ISSUE_QUERY = { beginner: true, page_size: 4 };

/** Campus open source: live hub stats, a stream of beginner-friendly issues and top contributors. */
export function OpenSourceSection() {
  const [ref, near] = useInView<HTMLElement>({ rootMargin: "400px 0px" });
  const overview = useQuery({ queryKey: osKeys.overview, queryFn: () => get<OpenSourceOverview>("/opensource/overview"), enabled: near, staleTime: 60_000 });
  const issues = useQuery({ queryKey: osKeys.issues(ISSUE_QUERY), queryFn: () => get<Page<IssueRow>>("/opensource/issues", ISSUE_QUERY), enabled: near, staleTime: 60_000 });
  const stats = overview.data?.stats;
  const tiles = [
    { label: "Repositories", value: stats?.repositories, icon: GitPullRequest },
    { label: "Open issues", value: stats?.open_issues, icon: CircleDot },
    { label: "Beginner-friendly", value: stats?.beginner_issues, icon: Sparkles },
    { label: "PRs merged · 90d", value: stats?.merged_prs_90d, icon: GitMerge },
  ];

  return (
    <section ref={ref} className="relative py-20 sm:py-28" aria-label="Open source">
      <Container>
        <SectionHeading
          index="06"
          eyebrow="Contribute"
          title="Your first merged PR, attributed to you."
          description="Campus open-source projects with beginner-friendly issues. Link GitHub once and merged pull requests show up on your profile."
          action={
            <LinkButton href="/open-source" variant="secondary" icon={<ArrowRight className="h-4 w-4" />}>
              Open source hub
            </LinkButton>
          }
        />

        <div className="mt-12 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
          <Reveal className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border shadow-card">
              {tiles.map((t) => (
                <div key={t.label} className="bg-surface p-5">
                  <p className="flex items-center gap-1.5 text-eyebrow text-subtle">
                    <t.icon className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> {t.label}
                  </p>
                  <p className="mt-3 text-[2rem] font-semibold leading-none tracking-[-0.035em] text-fg">
                    {overview.isPending ? <span className="skeleton inline-block h-8 w-12 align-middle" /> : <CountUp value={t.value} format={compactNumber} />}
                  </p>
                </div>
              ))}
            </div>
            <div className="rounded-[var(--radius-xl)] border border-border bg-surface p-5 shadow-card">
              <p className="text-eyebrow text-subtle">Top contributors · merged PRs</p>
              {overview.isPending ? (
                <div className="mt-4 flex gap-3">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-9 w-9 rounded-full" />)}</div>
              ) : overview.data?.top_contributors.length ? (
                <ul className="mt-4 space-y-2.5">
                  {overview.data.top_contributors.slice(0, 4).map((c, i) => {
                    const max = overview.data!.top_contributors[0].merged_prs || 1;
                    return (
                      <li key={c.user.id}>
                        <Link href={`/u/${c.user.handle}`} className="group flex items-center gap-3">
                          <span className="tabular w-4 font-mono text-[11px] text-subtle">{i + 1}</span>
                          <Avatar name={c.user.display_name} src={c.user.avatar_url} size={28} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-fg transition-colors group-hover:text-accent-strong">{c.user.display_name}</span>
                            <span className="mt-1 block h-1 rounded-full bg-surface-3">
                              <span className="block h-full rounded-full bg-brand" style={{ width: `${(c.merged_prs / max) * 100}%` }} />
                            </span>
                          </span>
                          <span className="tabular text-sm font-medium text-fg">{c.merged_prs}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="mt-3 text-sm text-muted">No merged pull requests attributed yet.</p>
              )}
            </div>
          </Reveal>

          <Reveal delay={80} className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
            <div className="flex items-center justify-between border-b border-border bg-bg-elevated/70 px-5 py-3">
              <p className="flex items-center gap-2 text-sm font-medium text-fg">
                <Sparkles className="h-4 w-4 text-accent-strong" aria-hidden /> Good first issues
              </p>
              <Link href="/open-source/issues?beginner=true" className="text-xs text-accent-strong hover:underline">See all</Link>
            </div>
            {issues.isPending ? (
              <div className="divide-y divide-border" role="status" aria-label="Loading issues">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="px-5 py-4">
                    <Skeleton className="h-3 w-32" />
                    <Skeleton className="mt-2.5 h-4 w-3/4" />
                  </div>
                ))}
              </div>
            ) : issues.data?.items.length ? (
              <ul className="divide-y divide-border">
                {issues.data.items.map((it) => (
                  <li key={it.id}>
                    <Link href="/open-source/issues?beginner=true" className="group flex gap-3.5 px-5 py-4 transition-colors hover:bg-surface-2/60">
                      <CircleDot className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-[11px] text-subtle">
                          {it.repo.full_name} #{it.number}
                          {it.repo.language ? <span className="ml-2 text-muted">· {it.repo.language}</span> : null}
                        </span>
                        <span className="mt-1 block text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{it.title}</span>
                        <span className="mt-2 flex flex-wrap items-center gap-1.5">
                          {it.labels.slice(0, 3).map((l) => (
                            <span key={l} className="rounded-full border border-border bg-bg-elevated px-2 py-0.5 text-[10.5px] text-muted">{l}</span>
                          ))}
                          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-subtle">
                            <MessageSquare className="h-3 w-3" aria-hidden /> {it.comments}
                            {it.updated_at ? <span className="ml-2">{relativeTime(it.updated_at)}</span> : null}
                          </span>
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-5 py-10 text-center text-sm text-muted">No beginner issues right now — check back after the next sync.</p>
            )}
          </Reveal>
        </div>
      </Container>
    </section>
  );
}
