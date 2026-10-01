"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookMarked, CircleDot, GitMerge, GitPullRequest, Info, Sparkles } from "lucide-react";
import Link from "next/link";

import { IssueItem, RegisterRepoDialog, RepoCardView, osKeys } from "@/components/catalog/repo";
import type { GitHubAccountStatus, IssueRow, OpenSourceOverview, RepoCard } from "@/components/catalog/types";
import { HBarList } from "@/components/charts/charts";
import { Avatar } from "@/components/ui/avatar";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { Container, PageHeader, Section } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton, SkeletonCards, SkeletonRows } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber, formatDate } from "@/lib/format";
import { hasRole, useMe } from "@/lib/hooks";
import type { Page } from "@/lib/types";

function GitHubCta({ oauthEnabled }: { oauthEnabled: boolean | undefined }) {
  const me = useMe();
  const account = useQuery({
    queryKey: osKeys.account,
    queryFn: () => get<GitHubAccountStatus>("/opensource/github/account"),
    enabled: Boolean(me.data),
    staleTime: 60_000,
  });
  if (me.isPending) return null;
  if (!me.data) {
    return (
      <InlineNotice
        tone="info"
        title="Get credit for your pull requests"
        action={<LinkButton href="/login?next=%2Fopen-source" size="sm" variant="secondary">Sign in</LinkButton>}
      >
        Sign in and link your GitHub account — merged PRs to registered repositories then appear on your profile.
      </InlineNotice>
    );
  }
  if (oauthEnabled === false || account.data?.oauth_enabled === false) {
    return (
      <InlineNotice tone="warning" title="GitHub linking is not configured on this deployment">
        Repositories and issues can still be browsed, but contributions can&apos;t be attributed to members until an administrator enables the GitHub integration.
      </InlineNotice>
    );
  }
  if (account.isPending) return <Skeleton className="h-14 w-full" />;
  if (account.data?.connected) {
    return (
      <InlineNotice
        tone="success"
        title={`GitHub connected as @${account.data.login}`}
        action={<LinkButton href="/settings/integrations" size="sm" variant="ghost">Manage</LinkButton>}
      >
        Merged pull requests you author on registered repositories count toward your profile{account.data.connected_at ? ` (linked ${formatDate(account.data.connected_at)})` : ""}.
      </InlineNotice>
    );
  }
  return (
    <InlineNotice
      tone="info"
      title="Connect GitHub to get credit for your contributions"
      action={<LinkButton href="/settings/integrations" size="sm">Connect GitHub</LinkButton>}
    >
      Linking your account lets us attribute merged pull requests and verify you as a project maintainer.
    </InlineNotice>
  );
}

export default function OpenSourceHubPage() {
  const me = useMe();
  const signedIn = Boolean(me.data);
  const overview = useQuery({ queryKey: osKeys.overview, queryFn: () => get<OpenSourceOverview>("/opensource/overview") });
  const repoQuery = { sort: "stars", page_size: 6 };
  const repos = useQuery({ queryKey: osKeys.repos(repoQuery), queryFn: () => get<Page<RepoCard>>("/opensource/repos", repoQuery) });
  const issueQuery = { beginner: true, page_size: 5 };
  const issues = useQuery({ queryKey: osKeys.issues(issueQuery), queryFn: () => get<Page<IssueRow>>("/opensource/issues", issueQuery) });
  const isMod = hasRole(me.data, "moderator");
  const o = overview.data;
  const anyDemo = (repos.data?.items ?? []).some((r) => r.is_demo) || (issues.data?.items ?? []).some((i) => i.repo.is_demo);

  return (
    <Container className="pb-16">
      <PageHeader
        eyebrow="Contribute"
        title="Open source hub"
        description="Campus open-source projects, beginner-friendly issues and merged pull requests — attributed to members through their linked GitHub accounts."
        actions={
          <>
            <LinkButton href="/open-source/issues" variant="secondary" icon={<CircleDot className="h-4 w-4" aria-hidden />}>Find issues</LinkButton>
            {signedIn ? <RegisterRepoDialog /> : <LinkButton href="/login?next=%2Fopen-source" variant="secondary">Sign in to register a repo</LinkButton>}
          </>
        }
      />

      <GitHubCta oauthEnabled={o?.github.oauth_enabled} />

      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {overview.isPending ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-[var(--radius-lg)]" />)
        ) : overview.isError ? (
          <div className="col-span-full"><ErrorState error={overview.error} onRetry={() => overview.refetch()} /></div>
        ) : (
          <>
            <Stat label="Repositories" value={compactNumber(o!.stats.repositories)} icon={<BookMarked className="h-4 w-4" aria-hidden />} />
            <Stat label="Open issues" value={compactNumber(o!.stats.open_issues)} icon={<CircleDot className="h-4 w-4" aria-hidden />} />
            <Stat label="Beginner-friendly issues" value={compactNumber(o!.stats.beginner_issues)} icon={<Sparkles className="h-4 w-4" aria-hidden />} />
            <Stat label="Merged PRs (90 days)" value={compactNumber(o!.stats.merged_prs_90d)} icon={<GitMerge className="h-4 w-4" aria-hidden />} />
          </>
        )}
      </div>
      {anyDemo ? <p className="mt-2 text-xs text-subtle">Includes synthetic demo repositories and issues created by the seed script.</p> : null}

      <div className="mt-2 grid gap-8 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <Section
            title="Popular repositories"
            action={<Link href="/open-source/repos" className="inline-flex items-center gap-1 text-sm text-accent-strong hover:underline">All repositories <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>}
          >
            {repos.isPending ? (
              <SkeletonCards count={4} className="lg:grid-cols-2" />
            ) : repos.isError ? (
              <ErrorState error={repos.error} onRetry={() => repos.refetch()} />
            ) : repos.data.items.length === 0 ? (
              <EmptyState
                icon={<BookMarked className="h-5 w-5" />}
                title="No repositories registered yet"
                description="Register a public GitHub repository to list its issues here."
                action={signedIn ? <RegisterRepoDialog /> : undefined}
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {repos.data.items.map((r) => <RepoCardView key={r.id} repo={r} canSync={signedIn} />)}
              </div>
            )}
          </Section>

          <Section
            title="Good first issues"
            description="Open issues labelled for newcomers. Promoted issues are picked by maintainers."
            action={<Link href="/open-source/issues?beginner=true" className="inline-flex items-center gap-1 text-sm text-accent-strong hover:underline">All beginner issues <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>}
          >
            {issues.isPending ? (
              <SkeletonRows rows={5} />
            ) : issues.isError ? (
              <ErrorState error={issues.error} onRetry={() => issues.refetch()} />
            ) : issues.data.items.length === 0 ? (
              <EmptyState icon={<GitPullRequest className="h-5 w-5" />} title="No beginner issues right now" description="Check back after the next sync, or browse all open issues." action={<LinkButton href="/open-source/issues" variant="secondary">Browse all issues</LinkButton>} />
            ) : (
              <Card>
                <ul className="divide-y divide-border">
                  {issues.data.items.map((i) => <IssueItem key={i.id} issue={i} canPromote={isMod} />)}
                </ul>
              </Card>
            )}
          </Section>
        </div>

        <aside className="space-y-6 py-6" aria-label="Community">
          <Card>
            <CardHeader title="Top contributors" description="Merged PRs in the last 12 months" />
            <CardBody>
              {overview.isPending ? (
                <SkeletonRows rows={4} />
              ) : !o || o.top_contributors.length === 0 ? (
                <p className="text-sm text-subtle">No attributed contributions yet. Link GitHub and get a PR merged to appear here.</p>
              ) : (
                <ol className="space-y-3">
                  {o.top_contributors.map((c, i) => (
                    <li key={c.user.id} className="flex items-center gap-3">
                      <span className="w-5 text-right text-xs tabular-nums text-subtle">{i + 1}</span>
                      <Link href={`/u/${c.user.handle}`} className="flex min-w-0 flex-1 items-center gap-2 text-sm text-fg hover:text-accent-strong">
                        <Avatar name={c.user.display_name} src={c.user.avatar_url} size={28} />
                        <span className="truncate">{c.user.display_name}</span>
                      </Link>
                      <span className="inline-flex items-center gap-1 text-xs tabular-nums text-muted">
                        <GitMerge className="h-3.5 w-3.5" aria-hidden /> {c.merged_prs}
                        <span className="sr-only"> merged pull requests</span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Languages" description="Registered repositories by primary language" />
            <CardBody>
              {overview.isPending ? (
                <SkeletonRows rows={4} />
              ) : !o || o.languages.length === 0 ? (
                <p className="text-sm text-subtle">No language data yet.</p>
              ) : (
                <>
                  <HBarList label="Repositories by language" data={o.languages.map((l) => ({ label: l.language, value: l.repositories }))} />
                  <div className="mt-4 flex flex-wrap gap-1.5">
                    {o.languages.map((l) => (
                      <Link key={l.language} href={`/open-source/issues?language=${encodeURIComponent(l.language)}`} className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted hover:text-fg">
                        {l.language} issues
                      </Link>
                    ))}
                  </div>
                </>
              )}
            </CardBody>
          </Card>

          {o?.attribution_note ? (
            <Card>
              <CardHeader title="How attribution works" />
              <CardBody className="flex gap-2 text-sm text-muted">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
                <div className="space-y-2">
                  <p>{o.attribution_note}</p>
                  <p className="text-xs text-subtle">
                    {o.github.webhooks_enabled
                      ? "Repositories update in near real time through GitHub webhooks, with periodic syncs as a fallback."
                      : "Repositories refresh through periodic syncs; GitHub webhooks are not configured on this deployment."}
                  </p>
                </div>
              </CardBody>
            </Card>
          ) : null}
        </aside>
      </div>
    </Container>
  );
}
