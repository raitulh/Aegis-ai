"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Award,
  BadgeCheck,
  BookOpen,
  CalendarDays,
  ExternalLink,
  EyeOff,
  GitBranch,
  GitFork,
  GitMerge,
  Globe,
  GraduationCap,
  Lock,
  Mail,
  Medal,
  Pencil,
  Trophy,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ActivityHeatmap } from "@/components/charts/charts";
import { BadgeIcon } from "@/components/profile/badge-icon";
import type { PublicProfile } from "@/components/profile/types";
import { Avatar } from "@/components/ui/avatar";
import { Badge, DemoBadge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { Prose } from "@/components/ui/markdown";
import { Cover } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, NotFoundState, Skeleton } from "@/components/ui/states";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { ApiError, get } from "@/lib/api";
import { formatDate, formatDateTime, formatNumber, formatScore, relativeTime, titleCase } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

/** Small marker shown only to the profile owner for items/sections the public cannot see. */
function OwnerOnly({ children = "Hidden from public" }: { children?: ReactNode }) {
  return (
    <Badge tone="outline" icon={<EyeOff className="h-3 w-3" aria-hidden />} title="Only you can see this. Change it in settings.">
      {children}
    </Badge>
  );
}

function SectionCard({ title, description, action, children }: { title: ReactNode; description?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={description} action={action} />
      <CardBody>{children}</CardBody>
    </Card>
  );
}

function ProfileSkeleton() {
  return (
    <div role="status" aria-label="Loading profile">
      <Skeleton className="h-40 w-full rounded-none sm:h-52" />
      <Container>
        <div className="-mt-12 flex items-end gap-4">
          <Skeleton className="h-24 w-24 rounded-full" />
          <div className="flex-1 pb-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="mt-2 h-4 w-72" />
          </div>
        </div>
        <div className="mt-8 grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      </Container>
    </div>
  );
}

function VerifiedSection({ p }: { p: PublicProfile }) {
  const v = p.verified;
  const any = v.university || v.github_login || v.email_verified;
  return (
    <SectionCard title={<span className="inline-flex items-center gap-1.5"><BadgeCheck className="h-4 w-4 text-success" aria-hidden />Verified by DataBattles</span>} description="Checked by the platform — not self-reported.">
      {!any ? (
        <p className="text-sm text-muted">No verified information yet.</p>
      ) : (
        <ul className="space-y-3 text-sm">
          {v.university ? (
            <li className="flex flex-wrap items-center gap-2">
              <GraduationCap className="h-4 w-4 text-subtle" aria-hidden />
              <Link href={`/orgs/${v.university.slug}`} className="font-medium text-fg hover:text-accent-strong">{v.university.name}</Link>
              <VerifiedBadge label="Verified member" title="Membership verified by the university (email domain or administrator)" />
            </li>
          ) : null}
          {v.github_login ? (
            <li className="flex flex-wrap items-center gap-2">
              <GitBranch className="h-4 w-4 text-subtle" aria-hidden />
              <a href={`https://github.com/${v.github_login}`} target="_blank" rel="noopener noreferrer" className="font-medium text-fg hover:text-accent-strong">
                @{v.github_login}
              </a>
              <VerifiedBadge label="GitHub linked" title="Linked through GitHub OAuth" />
            </li>
          ) : null}
          {v.email_verified ? (
            <li className="flex items-center gap-2">
              <Mail className="h-4 w-4 text-subtle" aria-hidden />
              <span className="text-fg">Email address</span>
              <VerifiedBadge label="Verified" title="The email address is confirmed (it is never shown publicly)" />
            </li>
          ) : null}
        </ul>
      )}
    </SectionCard>
  );
}

function SelfDeclaredSection({ p, privacy }: { p: PublicProfile; privacy: Record<string, boolean> | null }) {
  const s = p.self_declared;
  const hiddenFlag = (key: string) => (privacy && privacy[key] === false ? <OwnerOnly /> : null);
  const rows: { key: string; label: string; value: ReactNode; flag?: string }[] = [];
  if (s.university) rows.push({ key: "uni", label: "University", value: <Link href={`/orgs/${s.university.slug}`} className="hover:text-accent-strong">{s.university.name}</Link>, flag: "show_university" });
  if (s.department) rows.push({ key: "dept", label: "Department", value: s.department, flag: "show_department" });
  if (s.graduation_year) rows.push({ key: "grad", label: "Graduation year", value: String(s.graduation_year), flag: "show_graduation_year" });
  if (p.website_url)
    rows.push({
      key: "web",
      label: "Website",
      value: (
        <a href={p.website_url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 break-all hover:text-accent-strong">
          {p.website_url.replace(/^https?:\/\//, "")} <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
        </a>
      ),
    });
  const empty = !rows.length && !s.skills.length && !(p.is_self && s.interests.length);
  return (
    <SectionCard title="About" description="Provided by the user and not verified by the platform." action={<SelfDeclaredBadge />}>
      {empty ? (
        <p className="text-sm text-muted">Nothing shared yet.</p>
      ) : (
        <div className="space-y-4">
          {rows.length ? (
            <dl className="space-y-2.5 text-sm">
              {rows.map((r) => (
                <div key={r.key} className="flex flex-col">
                  <dt className="text-xs text-subtle">{r.label}</dt>
                  <dd className="flex flex-wrap items-center gap-2 text-fg">
                    {r.value}
                    {p.is_self && r.flag ? hiddenFlag(r.flag) : null}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {s.skills.length ? (
            <div>
              <p className="mb-1.5 flex items-center gap-2 text-xs text-subtle">Skills {p.is_self ? hiddenFlag("show_skills") : null}</p>
              <div className="flex flex-wrap gap-1.5">
                {s.skills.map((sk) => (
                  <span key={sk} className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-xs text-muted">{sk}</span>
                ))}
              </div>
            </div>
          ) : null}
          {p.is_self && s.interests.length ? (
            <div>
              <p className="mb-1.5 flex items-center gap-2 text-xs text-subtle">Interests <OwnerOnly>Only visible to you</OwnerOnly></p>
              <div className="flex flex-wrap gap-1.5">
                {s.interests.map((i) => <Badge key={i}>{i}</Badge>)}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </SectionCard>
  );
}

function ResultsList({ p }: { p: PublicProfile }) {
  if (!p.results.length) return <EmptyState icon={<Trophy className="h-5 w-5" />} title="No final results yet" description="Results appear here after a competition's leaderboard is finalized." />;
  return (
    <ul className="divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface">
      {p.results.map((r) => (
        <li key={r.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <Link href={`/competitions/${r.competition.slug}`} className="font-medium text-fg hover:text-accent-strong">{r.competition.title}</Link>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
              {r.label ? <Badge tone="accent">{r.label}</Badge> : null}
              {p.is_self && r.hidden ? <OwnerOnly /> : null}
            </div>
          </div>
          <dl className="flex gap-6 text-sm">
            <div>
              <dt className="text-xs text-subtle">Rank</dt>
              <dd className="tabular-nums text-fg">{r.rank ? <>#{r.rank} <span className="text-muted">of {formatNumber(r.total_ranked)}</span></> : "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-subtle">Final score</dt>
              <dd className="font-mono tabular-nums text-fg">{formatScore(r.score)}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

function JoinedList({ p }: { p: PublicProfile }) {
  if (!p.competitions.length) return null;
  return (
    <SectionCard title="Competitions joined" description={`${p.competitions.length} most recent`}>
      <ul className="space-y-2.5">
        {p.competitions.map((c) => (
          <li key={c.slug} className="flex flex-wrap items-center gap-2 text-sm">
            <Link href={`/competitions/${c.slug}`} className="min-w-0 flex-1 truncate text-fg hover:text-accent-strong">{c.title}</Link>
            <StatusBadge status={c.status} />
            <span className="text-xs text-subtle" title={formatDateTime(c.joined_at)}>joined {formatDate(c.joined_at)}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function CertificatesList({ p, privacyHidden }: { p: PublicProfile; privacyHidden: boolean }) {
  return (
    <SectionCard
      title="Certificates"
      description="Each certificate links to its public verification page."
      action={p.is_self && privacyHidden ? <OwnerOnly>Section hidden</OwnerOnly> : null}
    >
      {!p.certificates.length ? (
        <p className="text-sm text-muted">No certificates to show.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {p.certificates.map((c) => (
            <li key={c.public_id}>
              <Link href={`/verify/${c.public_id}`} className="flex h-full flex-col rounded-[var(--radius-md)] border border-border p-3 transition-colors hover:border-border-strong hover:bg-surface-2">
                <span className="flex items-start gap-2">
                  <Award className="mt-0.5 h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-fg">{c.result_label}</span>
                    <span className="block text-sm text-muted">{c.event_title}</span>
                  </span>
                </span>
                <span className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-subtle">
                  {c.issuer_name} · {formatDate(c.issued_at)}
                  {c.is_demo ? <DemoBadge /> : null}
                  {p.is_self && c.hidden ? <OwnerOnly /> : null}
                </span>
                <span className="mt-1 font-mono text-[11px] text-subtle">{c.public_id}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

function BadgesList({ p, privacyHidden }: { p: PublicProfile; privacyHidden: boolean }) {
  return (
    <SectionCard title="Badges" action={p.is_self && privacyHidden ? <OwnerOnly>Section hidden</OwnerOnly> : null}>
      {!p.badges.length ? (
        <p className="text-sm text-muted">No badges to show.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {p.badges.map((b) => (
            <li key={b.id}>
              <Link href={`/badges/${b.public_id}`} className="flex h-full gap-3 rounded-[var(--radius-md)] border border-border p-3 transition-colors hover:border-border-strong hover:bg-surface-2">
                <BadgeIcon icon={b.icon} color={b.color} size={40} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-fg">{b.name}</span>
                  <span className="line-clamp-2 block text-xs text-muted">{b.description}</span>
                  <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <Badge tone="outline">{b.manual ? "Awarded by organizer" : "Automatic"}</Badge>
                    {b.rarity_label ? <Badge>{b.rarity_label}</Badge> : null}
                    {p.is_self && b.hidden ? <OwnerOnly /> : null}
                  </span>
                  <span className="mt-1 block text-[11px] text-subtle">{titleCase(b.category)} · {formatDate(b.awarded_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

function ProjectsGrid({ p }: { p: PublicProfile }) {
  if (!p.projects.length) {
    return (
      <EmptyState
        icon={<GitFork className="h-5 w-5" />}
        title="No projects yet"
        action={p.is_self ? <LinkButton href="/projects/new" variant="secondary">Share a project</LinkButton> : undefined}
      />
    );
  }
  return (
    <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {p.projects.map((pr) => (
        <li key={pr.slug} className="flex flex-col overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface transition-colors hover:border-border-strong">
          <Cover style={pr.cover_style} className="h-24" />
          <div className="flex flex-1 flex-col p-4">
            <Link href={`/projects/${pr.slug}`} className="font-semibold text-fg hover:text-accent-strong">{pr.title}</Link>
            <p className="mt-1 line-clamp-2 text-sm text-muted">{pr.summary}</p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {pr.is_open_source ? <Badge tone="info" icon={<GitFork className="h-3 w-3" aria-hidden />}>Open source</Badge> : null}
              {pr.tags.slice(0, 4).map((t) => <Badge key={t}>{t}</Badge>)}
            </div>
            {pr.repo_url || pr.demo_url ? (
              <div className="mt-auto flex gap-3 pt-3 text-xs">
                {pr.repo_url ? <a href={pr.repo_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-muted hover:text-fg">Repository <ExternalLink className="h-3 w-3" aria-hidden /></a> : null}
                {pr.demo_url ? <a href={pr.demo_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-muted hover:text-fg">Demo <ExternalLink className="h-3 w-3" aria-hidden /></a> : null}
              </div>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

function CoursesList({ p }: { p: PublicProfile }) {
  if (!p.courses.length) {
    return <EmptyState icon={<BookOpen className="h-5 w-5" />} title="No completed courses yet" action={p.is_self ? <LinkButton href="/learn" variant="secondary">Take a course</LinkButton> : undefined} />;
  }
  return (
    <ul className="divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface">
      {p.courses.map((c) => (
        <li key={c.slug} className="flex items-center gap-3 px-4 py-3">
          <GraduationCap className="h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
          <Link href={`/learn/${c.slug}`} className="min-w-0 flex-1 truncate text-sm font-medium text-fg hover:text-accent-strong">{c.title}</Link>
          <span className="shrink-0 text-xs text-subtle">Completed {formatDate(c.completed_at)}</span>
        </li>
      ))}
    </ul>
  );
}

function ContributionsList({ p, privacyHidden }: { p: PublicProfile; privacyHidden: boolean }) {
  const c = p.contributions;
  if (!c) {
    return <EmptyState icon={<GitMerge className="h-5 w-5" />} title="Open-source contributions are not shared" description="This person has chosen not to show GitHub contributions." />;
  }
  if (!c.github_login) {
    return (
      <EmptyState
        icon={<GitMerge className="h-5 w-5" />}
        title="No GitHub account linked"
        description="Contributions are attributed only through a GitHub account linked with OAuth."
        action={p.is_self ? <LinkButton href="/settings/integrations" variant="secondary">Connect GitHub</LinkButton> : undefined}
      />
    );
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
        <span>
          <span className="font-semibold text-fg">{formatNumber(c.merged_count)}</span> merged pull requests across{" "}
          <span className="font-semibold text-fg">{formatNumber(c.repositories)}</span> {c.repositories === 1 ? "repository" : "repositories"} (public repos registered on DataBattles)
        </span>
        {p.is_self && privacyHidden ? <OwnerOnly>Section hidden</OwnerOnly> : null}
      </div>
      {!c.items.length ? (
        <p className="text-sm text-muted">No merged pull requests in registered repositories yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface">
          {c.items.map((pr) => (
            <li key={pr.url} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
              <GitMerge className="hidden h-4 w-4 shrink-0 text-accent-strong sm:block" aria-hidden />
              <div className="min-w-0 flex-1">
                <a href={pr.url} target="_blank" rel="noopener noreferrer" className="text-sm font-medium text-fg hover:text-accent-strong">
                  {pr.title}
                </a>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="font-mono">{pr.repo}#{pr.number}</span>
                  {pr.merged_at ? <span title={formatDateTime(pr.merged_at)}>merged {relativeTime(pr.merged_at)}</span> : null}
                  {pr.is_demo ? <DemoBadge /> : null}
                </p>
              </div>
              {pr.additions !== null || pr.deletions !== null ? (
                <span className="shrink-0 font-mono text-xs">
                  <span className="text-success">+{formatNumber(pr.additions ?? 0)}</span>{" "}
                  <span className="text-danger">−{formatNumber(pr.deletions ?? 0)}</span>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const TIMELINE_ICON: Record<string, ReactNode> = {
  competition_joined: <Trophy className="h-3.5 w-3.5" aria-hidden />,
  badge: <Medal className="h-3.5 w-3.5" aria-hidden />,
  course_completed: <GraduationCap className="h-3.5 w-3.5" aria-hidden />,
  certificate: <Award className="h-3.5 w-3.5" aria-hidden />,
  contribution: <GitMerge className="h-3.5 w-3.5" aria-hidden />,
};

function Timeline({ p }: { p: PublicProfile }) {
  if (!p.timeline.length) return <p className="text-sm text-muted">No recent activity to show.</p>;
  return (
    <ol className="relative space-y-4 border-l border-border pl-5">
      {p.timeline.map((t, i) => {
        const external = t.url && !t.url.startsWith("/");
        return (
          <li key={`${t.kind}-${i}`} className="relative">
            <span className="absolute -left-[31px] flex h-5 w-5 items-center justify-center rounded-full border border-border bg-surface-2 text-muted">
              {TIMELINE_ICON[t.kind] ?? <CalendarDays className="h-3.5 w-3.5" aria-hidden />}
            </span>
            {t.url ? (
              external ? (
                <a href={t.url} target="_blank" rel="noopener noreferrer" className="text-sm text-fg hover:text-accent-strong">{t.title}</a>
              ) : (
                <Link href={t.url} className="text-sm text-fg hover:text-accent-strong">{t.title}</Link>
              )
            ) : (
              <span className="text-sm text-fg">{t.title}</span>
            )}
            {t.at ? <p className="text-xs text-subtle" title={formatDateTime(t.at)}>{relativeTime(t.at)}</p> : null}
          </li>
        );
      })}
    </ol>
  );
}

function Profile({ p, privacy }: { p: PublicProfile; privacy: Record<string, boolean> | null }) {
  useEffect(() => {
    const prev = document.title;
    document.title = `${p.display_name} (@${p.handle}) · DataBattles`;
    return () => {
      document.title = prev;
    };
  }, [p.display_name, p.handle]);
  const hidden = (key: string) => Boolean(p.is_self && privacy && privacy[key] === false);
  const credCount = p.certificates.length + p.badges.length;
  const tabs = [
    { value: "overview", label: "Overview" },
    { value: "competitions", label: "Competitions", count: p.competitions.length },
    { value: "credentials", label: "Credentials", count: credCount },
    { value: "projects", label: "Projects", count: p.projects.length },
    { value: "learning", label: "Learning", count: p.courses.length },
    { value: "open-source", label: "Open source", count: p.contributions?.merged_count ?? 0 },
  ];

  return (
    <>
      {/* React 19 hoists <meta> into <head>. The page renders client-side, so only crawlers that run JS see this. */}
      {!p.indexable ? <meta name="robots" content="noindex, nofollow" /> : null}

      <Cover style={p.cover_style} className="h-36 sm:h-52" />
      <Container>
        <div className="-mt-12 flex flex-col gap-4 sm:-mt-14 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex min-w-0 items-end gap-4">
            <Avatar name={p.display_name} src={p.avatar_url} size={104} className="ring-4 ring-bg" />
            <div className="min-w-0 pb-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="truncate text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{p.display_name}</h1>
                {p.is_demo ? <DemoBadge /> : null}
              </div>
              <p className="text-sm text-muted">
                @{p.handle} · joined {formatDate(p.joined_at, { month: "long", year: "numeric" })}
              </p>
            </div>
          </div>
          {p.is_self ? (
            <div className="flex flex-wrap gap-2 sm:pb-1">
              <LinkButton href="/settings/profile" icon={<Pencil className="h-4 w-4" />}>Edit profile</LinkButton>
              <LinkButton href="/settings/privacy" variant="secondary" icon={<Lock className="h-4 w-4" />}>Privacy</LinkButton>
            </div>
          ) : null}
        </div>
        {p.headline ? <p className="mt-4 max-w-3xl text-base text-fg">{p.headline}</p> : null}
        {p.is_self ? (
          <p className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-surface-2 px-2.5 py-1.5 text-xs text-muted">
            <EyeOff className="h-3.5 w-3.5" aria-hidden />
            You&apos;re viewing your own profile. Items marked “Hidden from public” are only visible to you.
            {!p.indexable ? " Search engines are asked not to index this page." : null}
          </p>
        ) : null}

        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <Stat label="Competitions" value={formatNumber(p.stats.competitions)} />
          <Stat label="Top-10 finishes" value={formatNumber(p.stats.top10_finishes)} />
          <Stat label="Certificates" value={formatNumber(p.stats.certificates)} />
          <Stat label="Badges" value={formatNumber(p.stats.badges)} />
          <Stat label="Projects" value={formatNumber(p.stats.projects)} />
          <Stat label="Courses" value={formatNumber(p.stats.courses_completed)} />
          <Stat label="Merged PRs" value={formatNumber(p.stats.merged_prs)} />
        </div>

        <div className="mt-8 grid gap-6 pb-16 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 lg:order-1">
            <Tabs tabs={tabs}>
              <TabPanel value="overview" className="space-y-6">
                {p.bio_html ? (
                  <SectionCard title="Bio" action={<SelfDeclaredBadge />}>
                    <Prose html={p.bio_html} />
                  </SectionCard>
                ) : p.is_self ? (
                  <EmptyState title="Add a bio" description="A few sentences about what you're learning and building helps teammates find you." action={<LinkButton href="/settings/profile" variant="secondary">Write a bio</LinkButton>} />
                ) : null}
                {p.activity ? (
                  <SectionCard title="Activity" description="Aggregate daily counts only." action={hidden("show_activity") ? <OwnerOnly>Section hidden</OwnerOnly> : null}>
                    <ActivityHeatmap counts={p.activity} days={365} label={`${p.display_name}'s activity`} />
                  </SectionCard>
                ) : null}
                {p.results.length ? (
                  <section aria-labelledby="top-results">
                    <h2 id="top-results" className="mb-3 text-sm font-semibold text-fg">Competition results</h2>
                    <ResultsList p={{ ...p, results: p.results.slice(0, 5) }} />
                  </section>
                ) : null}
                {p.timeline.length ? (
                  <SectionCard title="Timeline">
                    <Timeline p={p} />
                  </SectionCard>
                ) : null}
                {!p.bio_html && !p.activity && !p.results.length && !p.timeline.length && !p.is_self ? (
                  <EmptyState title="Nothing to show yet" description="This profile hasn't shared any activity." />
                ) : null}
              </TabPanel>
              <TabPanel value="competitions" className="space-y-6">
                <section aria-labelledby="all-results">
                  <h2 id="all-results" className="mb-3 text-sm font-semibold text-fg">Final results</h2>
                  <ResultsList p={p} />
                </section>
                <JoinedList p={p} />
              </TabPanel>
              <TabPanel value="credentials" className="space-y-6">
                {p.is_self ? (
                  <p className="text-sm text-muted">
                    Choose which credentials appear publicly in <Link href="/settings/achievements" className="text-accent-strong hover:underline">achievement settings</Link>.
                  </p>
                ) : null}
                <CertificatesList p={p} privacyHidden={hidden("show_certificates")} />
                <BadgesList p={p} privacyHidden={hidden("show_badges")} />
              </TabPanel>
              <TabPanel value="projects">
                <ProjectsGrid p={p} />
              </TabPanel>
              <TabPanel value="learning">
                <CoursesList p={p} />
              </TabPanel>
              <TabPanel value="open-source">
                <ContributionsList p={p} privacyHidden={hidden("show_contributions")} />
              </TabPanel>
            </Tabs>
          </div>
          <aside className="space-y-6 lg:order-2" aria-label="Profile details">
            <VerifiedSection p={p} />
            <SelfDeclaredSection p={p} privacy={p.is_self ? privacy : null} />
            <p className="flex items-start gap-2 px-1 text-xs text-subtle">
              <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              Results, certificates and badges are recorded by the platform. Everything under “About” is self-declared.
            </p>
          </aside>
        </div>
      </Container>
    </>
  );
}

export default function PublicProfilePage() {
  const { handle } = useParams<{ handle: string }>();
  const clean = decodeURIComponent(handle ?? "").replace(/^@/, "").toLowerCase();
  const me = useMe();
  const q = useQuery({ queryKey: qk.profile(clean), queryFn: () => get<PublicProfile>(`/users/${encodeURIComponent(clean)}`), enabled: Boolean(clean) });

  if (q.isPending) return <ProfileSkeleton />;
  if (q.isError) {
    return (
      <Container className="py-16">
        {q.error instanceof ApiError && q.error.status === 404 ? <NotFoundState what="profile" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      </Container>
    );
  }
  return <Profile p={q.data} privacy={q.data.is_self ? (me.data?.privacy ?? null) : null} />;
}
