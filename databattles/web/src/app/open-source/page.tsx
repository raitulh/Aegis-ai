"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookMarked, CircleDot, GitBranch, GitMerge, GitPullRequest, Info, Languages, Sparkles, Trophy, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { CatalogRowsSkeleton, IssueItem, RegisterRepoDialog, RepoRow, osKeys } from "@/components/catalog/repo";
import type { GitHubAccountStatus, IssueRow, OpenSourceOverview, RepoCard } from "@/components/catalog/types";
import { HBarList } from "@/components/charts/charts";
import { Reveal } from "@/components/motion/reveal";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { RankBadge } from "@/components/ui/extras";
import { ProgressBar } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, formatDate } from "@/lib/format";
import { hasRole, useMe } from "@/lib/hooks";
import type { Page } from "@/lib/types";

type Tone = "info" | "warning" | "success" | "neutral";

/** Text-labelled status pill (the label carries the meaning; the dot only echoes it). */
function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <Badge tone={tone}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {children}
    </Badge>
  );
}

/** Body of the GitHub link panel: one of the account states below, with its action. */
function GitHubState({ tone, status, title, children, action }: { tone: Tone; status: string; title: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-eyebrow text-subtle">
          <GitBranch className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> GitHub link
        </p>
        <StatusPill tone={tone}>{status}</StatusPill>
      </div>
      <h2 className="mt-4 text-[15px] font-semibold leading-snug tracking-[-0.01em] text-fg">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-muted">{children}</p>
      {action ? <div className="mt-auto pt-5">{action}</div> : null}
    </div>
  );
}

function GitHubPanel({ github }: { github: OpenSourceOverview["github"] | undefined }) {
  const oauthEnabled = github?.oauth_enabled;
  const me = useMe();
  const account = useQuery({
    queryKey: osKeys.account,
    queryFn: () => get<GitHubAccountStatus>("/opensource/github/account"),
    enabled: Boolean(me.data),
    staleTime: 60_000,
  });

  let body: ReactNode;
  if (me.isPending) {
    body = (
      <div className="flex-1" role="status" aria-label="Loading GitHub status">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-5 h-4 w-3/4" />
        <Skeleton className="mt-2.5 h-3 w-full" />
        <Skeleton className="mt-1.5 h-3 w-2/3" />
      </div>
    );
  } else if (!me.data) {
    body = (
      <GitHubState
        tone="info"
        status="Signed out"
        title="Get credit for your pull requests"
        action={<LinkButton href="/login?next=%2Fopen-source" size="sm" variant="secondary">Sign in</LinkButton>}
      >
        Sign in and link your GitHub account — merged PRs to registered repositories then appear on your profile.
      </GitHubState>
    );
  } else if (oauthEnabled === false || account.data?.oauth_enabled === false) {
    body = (
      <GitHubState tone="warning" status="Not configured" title="GitHub linking is not configured on this deployment">
        Repositories and issues can still be browsed, but contributions can&apos;t be attributed to members until an administrator enables the GitHub integration.
      </GitHubState>
    );
  } else if (account.isPending) {
    body = (
      <div className="flex-1" role="status" aria-label="Loading GitHub status">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-5 h-4 w-3/4" />
        <Skeleton className="mt-2.5 h-3 w-full" />
      </div>
    );
  } else if (account.data?.connected) {
    body = (
      <GitHubState
        tone="success"
        status="Connected"
        title={`GitHub connected as @${account.data.login}`}
        action={<LinkButton href="/settings/integrations" size="sm" variant="ghost">Manage</LinkButton>}
      >
        Merged pull requests you author on registered repositories count toward your profile{account.data.connected_at ? ` (linked ${formatDate(account.data.connected_at)})` : ""}.
      </GitHubState>
    );
  } else {
    body = (
      <GitHubState
        tone="info"
        status="Not linked"
        title="Connect GitHub to get credit for your contributions"
        action={<LinkButton href="/settings/integrations" size="sm">Connect GitHub</LinkButton>}
      >
        Linking your account lets us attribute merged pull requests and verify you as a project maintainer.
      </GitHubState>
    );
  }

  return (
    <section aria-label="GitHub account" className="relative flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-5 shadow-card">
      <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full opacity-60 blur-2xl" style={{ background: "radial-gradient(closest-side, var(--ambient-b), transparent)" }} />
      <div className="relative flex flex-1 flex-col">{body}</div>
      <dl className="relative mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border text-xs">
        <div className="bg-bg-elevated px-3 py-2.5">
          <dt className="text-eyebrow text-subtle">Account linking</dt>
          <dd className="mt-1 font-medium text-fg">{github ? (github.oauth_enabled ? "Enabled" : "Not configured") : "—"}</dd>
        </div>
        <div className="bg-bg-elevated px-3 py-2.5">
          <dt className="text-eyebrow text-subtle">Repo updates</dt>
          <dd className="mt-1 font-medium text-fg">{github ? (github.webhooks_enabled ? "Webhooks + syncs" : "Periodic syncs") : "—"}</dd>
        </div>
      </dl>
    </section>
  );
}

/** Hub stats as one gap-px tile grid. Values are the overview's real counts. */
function StatsPanel({ overview }: { overview: ReturnType<typeof useOverview> }) {
  const s = overview.data?.stats;
  const beginnerShare = s && s.open_issues > 0 ? Math.round((s.beginner_issues / s.open_issues) * 100) : null;
  const tiles = [
    { label: "Repositories", value: s?.repositories, icon: BookMarked, hint: "Public and registered", share: null },
    { label: "Open issues", value: s?.open_issues, icon: CircleDot, hint: "Across registered repos", share: null },
    { label: "Beginner-friendly issues", value: s?.beginner_issues, icon: Sparkles, hint: beginnerShare !== null ? `${beginnerShare}% of open issues` : "Labelled for newcomers", share: beginnerShare },
    { label: "Merged PRs (90 days)", value: s?.merged_prs_90d, icon: GitMerge, hint: "On registered repos", share: null },
  ];
  if (overview.isError) {
    return (
      <div className="min-w-0">
        <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
      </div>
    );
  }
  return (
    <div className="relative min-w-0">
      <div aria-hidden className="pointer-events-none absolute inset-x-8 top-0 z-10 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
      <dl className="grid h-full grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border shadow-card">
        {tiles.map((t) => (
          <div key={t.label} className="group flex min-w-0 flex-col justify-between gap-6 bg-surface p-4 transition-colors duration-300 hover:bg-surface-2/70 sm:p-5">
            <dt className="flex items-start gap-1.5 text-eyebrow text-subtle">
              <t.icon className="mt-px h-3.5 w-3.5 shrink-0 text-accent-strong" aria-hidden />
              <span className="min-w-0">{t.label}</span>
            </dt>
            <dd>
              <span className="block text-[2rem] font-semibold leading-none tracking-[-0.035em] text-fg sm:text-[2.35rem]">
                {overview.isPending ? (
                  <span className="skeleton inline-block h-8 w-14 align-middle" />
                ) : (
                  <span className="tabular">{typeof t.value === "number" ? compactNumber(t.value) : "—"}</span>
                )}
              </span>
              <span className="mt-2 block text-xs text-subtle">{overview.isPending ? "\u00a0" : t.hint}</span>
              {typeof t.share === "number" ? (
                <ProgressBar value={t.share} className="mt-3 h-1" label={`${t.share}% of open issues are beginner-friendly`} />
              ) : (
                <span aria-hidden className="mt-3 block h-1" />
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function useOverview() {
  return useQuery({ queryKey: osKeys.overview, queryFn: () => get<OpenSourceOverview>("/opensource/overview") });
}

/** Section title row used down the page: mono eyebrow, title, optional description and a link. */
function SectionTitle({ id, eyebrow, title, description, action }: { id: string; eyebrow: string; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <p className="text-eyebrow text-subtle">{eyebrow}</p>
        <h2 id={id} className="mt-1.5 text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">{title}</h2>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

const linkCls = "inline-flex min-h-9 items-center gap-1 text-sm text-accent-strong hover:underline";

/** Panel chrome shared by the catalogue blocks: a quiet title bar over hairline-divided rows. */
function Panel({ icon: Icon, label, meta, children, className }: { icon: LucideIcon; label: ReactNode; meta?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card", className)}>
      <div className="flex items-center gap-2 border-b border-border bg-bg-elevated/70 px-4 py-2.5 sm:px-5">
        <Icon className="h-3.5 w-3.5 shrink-0 text-accent-strong" aria-hidden />
        <span className="min-w-0 truncate text-eyebrow text-subtle">{label}</span>
        {meta ? <span className="tabular ml-auto shrink-0 font-mono text-[11px] text-subtle">{meta}</span> : null}
      </div>
      {children}
    </div>
  );
}

export default function OpenSourceHubPage() {
  const me = useMe();
  const signedIn = Boolean(me.data);
  const overview = useOverview();
  const repoQuery = { sort: "stars", page_size: 6 };
  const repos = useQuery({ queryKey: osKeys.repos(repoQuery), queryFn: () => get<Page<RepoCard>>("/opensource/repos", repoQuery) });
  const issueQuery = { beginner: true, page_size: 5 };
  const issues = useQuery({ queryKey: osKeys.issues(issueQuery), queryFn: () => get<Page<IssueRow>>("/opensource/issues", issueQuery) });
  const isMod = hasRole(me.data, "moderator");
  const o = overview.data;
  const anyDemo = (repos.data?.items ?? []).some((r) => r.is_demo) || (issues.data?.items ?? []).some((i) => i.repo.is_demo);
  const topPrs = o?.top_contributors[0]?.merged_prs || 1;

  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Contribute"
        icon={<GitPullRequest />}
        title="Open source hub"
        description="Campus open-source projects, beginner-friendly issues and merged pull requests — attributed to members through their linked GitHub accounts."
        actions={
          <>
            <LinkButton href="/open-source/issues" variant="secondary" icon={<CircleDot className="h-4 w-4" aria-hidden />}>Find issues</LinkButton>
            {signedIn ? <RegisterRepoDialog /> : <LinkButton href="/login?next=%2Fopen-source" variant="secondary">Sign in to register a repo</LinkButton>}
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 animate-rise [animation-delay:80ms] lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <StatsPanel overview={overview} />
        <GitHubPanel github={o?.github} />
      </div>
      {anyDemo ? <p className="mt-3 text-xs text-subtle">Includes synthetic demo repositories and issues created by the seed script.</p> : null}

      <div className="mt-14 grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_20rem] lg:gap-10">
        <div className="min-w-0 space-y-14">
          <section aria-labelledby="popular-repos">
            <SectionTitle
              id="popular-repos"
              eyebrow="Repositories"
              title="Popular repositories"
              action={<Link href="/open-source/repos" className={linkCls}>All repositories <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>}
            />
            {repos.isPending ? (
              <Panel icon={BookMarked} label="repositories · sorted by stars"><CatalogRowsSkeleton rows={3} label="Loading repositories" /></Panel>
            ) : repos.isError ? (
              <ErrorState error={repos.error} onRetry={() => repos.refetch()} />
            ) : repos.data.items.length === 0 ? (
              <EmptyState
                icon={<BookMarked />}
                title="No repositories registered yet"
                description="Register a public GitHub repository to list its issues here."
                action={signedIn ? <RegisterRepoDialog /> : undefined}
              />
            ) : (
              <Panel icon={BookMarked} label="repositories · sorted by stars" meta={`${repos.data.total} registered`}>
                <ul className="divide-y divide-border">
                  {repos.data.items.map((r, i) => <RepoRow key={r.id} repo={r} rank={i + 1} canSync={signedIn} />)}
                </ul>
              </Panel>
            )}
          </section>

          <section aria-labelledby="good-first-issues">
            <Reveal>
              <SectionTitle
                id="good-first-issues"
                eyebrow="Start here"
                title="Good first issues"
                description="Open issues labelled for newcomers. Promoted issues are picked by maintainers."
                action={<Link href="/open-source/issues?beginner=true" className={linkCls}>All beginner issues <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>}
              />
              {issues.isPending ? (
                <Panel icon={CircleDot} label="issues · beginner friendly"><CatalogRowsSkeleton rows={4} label="Loading issues" /></Panel>
              ) : issues.isError ? (
                <ErrorState error={issues.error} onRetry={() => issues.refetch()} />
              ) : issues.data.items.length === 0 ? (
                <EmptyState icon={<GitPullRequest />} title="No beginner issues right now" description="Check back after the next sync, or browse all open issues." action={<LinkButton href="/open-source/issues" variant="secondary">Browse all issues</LinkButton>} />
              ) : (
                <Panel icon={CircleDot} label="issues · beginner friendly" meta={`${issues.data.total} open`}>
                  <ul className="divide-y divide-border">
                    {issues.data.items.map((i) => <IssueItem key={i.id} issue={i} canPromote={isMod} />)}
                  </ul>
                </Panel>
              )}
            </Reveal>
          </section>
        </div>

        <aside aria-label="Community" className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card">
            <section aria-labelledby="top-contributors" className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 id="top-contributors" className="flex items-center gap-1.5 text-sm font-semibold tracking-[-0.01em] text-fg">
                    <Trophy className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> Top contributors
                  </h2>
                  <p className="mt-0.5 text-xs text-muted">Merged PRs in the last 12 months</p>
                </div>
              </div>
              {overview.isPending ? (
                <div className="mt-4 space-y-3" role="status" aria-label="Loading contributors">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-3">
                      <Skeleton className="h-6 w-6 rounded-full" />
                      <Skeleton className="h-7 w-7 rounded-full" />
                      <Skeleton className="h-3 flex-1" />
                    </div>
                  ))}
                </div>
              ) : !o || o.top_contributors.length === 0 ? (
                <p className="mt-4 text-sm text-subtle">No attributed contributions yet. Link GitHub and get a PR merged to appear here.</p>
              ) : (
                <ol className="mt-4 space-y-3.5">
                  {o.top_contributors.map((c, i) => (
                    <li key={c.user.id} className="flex items-center gap-3">
                      <RankBadge rank={i + 1} size="sm" />
                      <Link href={`/u/${c.user.handle}`} className="group flex min-w-0 flex-1 items-center gap-2.5 rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">
                        <Avatar name={c.user.display_name} src={c.user.avatar_url} size={28} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm text-fg transition-colors group-hover:text-accent-strong">{c.user.display_name}</span>
                          <span className="mt-1 block h-1 rounded-full bg-surface-3" aria-hidden>
                            <span className="block h-full rounded-full bg-brand" style={{ width: `${(c.merged_prs / topPrs) * 100}%` }} />
                          </span>
                        </span>
                      </Link>
                      <span className="tabular inline-flex shrink-0 items-center gap-1 text-sm font-medium text-fg">
                        <GitMerge className="h-3.5 w-3.5 text-subtle" aria-hidden /> {c.merged_prs}
                        <span className="sr-only"> merged pull requests</span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section aria-labelledby="languages" className="p-5">
              <h2 id="languages" className="flex items-center gap-1.5 text-sm font-semibold tracking-[-0.01em] text-fg">
                <Languages className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> Languages
              </h2>
              <p className="mt-0.5 text-xs text-muted">Registered repositories by primary language</p>
              <div className="mt-4">
                {overview.isPending ? (
                  <div className="space-y-3" role="status" aria-label="Loading languages">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div key={i}>
                        <Skeleton className="h-3 w-1/3" />
                        <Skeleton className="mt-2 h-1.5 w-full" />
                      </div>
                    ))}
                  </div>
                ) : !o || o.languages.length === 0 ? (
                  <p className="text-sm text-subtle">No language data yet.</p>
                ) : (
                  <>
                    <HBarList label="Repositories by language" data={o.languages.map((l) => ({ label: l.language, value: l.repositories }))} />
                    <div className="mt-4 flex flex-wrap gap-1.5">
                      {o.languages.map((l) => (
                        <Link
                          key={l.language}
                          href={`/open-source/issues?language=${encodeURIComponent(l.language)}`}
                          className="inline-flex min-h-8 items-center gap-1 rounded-full border border-border bg-bg-elevated px-2.5 text-xs text-muted transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                        >
                          <CircleDot className="h-3 w-3 text-subtle" aria-hidden /> {l.language} issues
                        </Link>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </section>

            {o?.attribution_note ? (
              <section aria-labelledby="attribution" className="bg-bg-elevated/40 p-5">
                <h2 id="attribution" className="flex items-center gap-1.5 text-sm font-semibold tracking-[-0.01em] text-fg">
                  <Info className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> How attribution works
                </h2>
                <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted">
                  <p>{o.attribution_note}</p>
                  <p className="text-xs text-subtle">
                    {o.github.webhooks_enabled
                      ? "Repositories update in near real time through GitHub webhooks, with periodic syncs as a fallback."
                      : "Repositories refresh through periodic syncs; GitHub webhooks are not configured on this deployment."}
                  </p>
                </div>
              </section>
            ) : null}
          </div>
        </aside>
      </div>
    </Container>
  );
}
