"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Award,
  BadgeCheck,
  BookOpen,
  CalendarDays,
  ExternalLink,
  EyeOff,
  FolderGit2,
  GitBranch,
  GitFork,
  GitMerge,
  Globe,
  GraduationCap,
  Lock,
  Mail,
  Medal,
  Pencil,
  ShieldCheck,
  Trophy,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ActivityHeatmap } from "@/components/charts/charts";
import { CountUp } from "@/components/motion/count-up";
import { Reveal } from "@/components/motion/reveal";
import { BadgeIcon } from "@/components/profile/badge-icon";
import { BadgeOrbit } from "@/components/profile/badge-orbit";
import type { PublicProfile } from "@/components/profile/types";
import { Avatar } from "@/components/ui/avatar";
import { Badge, DemoBadge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { RankBadge } from "@/components/ui/extras";
import { Prose } from "@/components/ui/markdown";
import { Cover } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, NotFoundState, Skeleton } from "@/components/ui/states";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { ApiError, get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatNumber, formatScore, relativeTime, titleCase } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

const linkCls = "rounded-sm transition-colors hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";

/** Small marker shown only to the profile owner for items/sections the public cannot see. */
function OwnerOnly({ children = "Hidden from public" }: { children?: ReactNode }) {
  return (
    <Badge tone="outline" icon={<EyeOff className="h-3 w-3" aria-hidden />} title="Only you can see this. Change it in settings.">
      {children}
    </Badge>
  );
}

/** Portfolio section: a heading with a mono eyebrow instead of yet another bordered card. */
function PortfolioSection({
  id,
  eyebrow,
  title,
  description,
  action,
  children,
  className,
}: {
  id: string;
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Reveal className={className}>
      <section aria-labelledby={id}>
        <div className="mb-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {eyebrow ? <p className="mb-1.5 text-eyebrow text-subtle">{eyebrow}</p> : null}
            <h2 id={id} className="text-lg font-semibold tracking-[-0.02em] text-fg">{title}</h2>
            {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
        </div>
        {children}
      </section>
    </Reveal>
  );
}

const panel = "rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card";

function ProfileSkeleton() {
  return (
    <Container className="pb-16">
      <div role="status" aria-label="Loading profile" className="mt-6 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface sm:mt-8">
        <Skeleton className="h-28 w-full rounded-none sm:h-44" />
        <div className="px-5 pb-6 sm:px-8">
          <Skeleton className="-mt-12 h-24 w-24 rounded-full sm:-mt-14 sm:h-28 sm:w-28" />
          <Skeleton className="mt-5 h-8 w-64 max-w-full" />
          <Skeleton className="mt-3 h-4 w-80 max-w-full" />
          <Skeleton className="mt-5 h-4 w-full max-w-xl" />
        </div>
        <div className="grid grid-cols-2 gap-px border-t border-border bg-border sm:grid-cols-4 xl:grid-cols-7">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="bg-surface px-5 py-4">
              <Skeleton className="h-2.5 w-16" />
              <Skeleton className="mt-3 h-6 w-10" />
            </div>
          ))}
        </div>
      </div>
    </Container>
  );
}

// ----------------------------------------------------------------------------- hero

function VerifiedSection({ p }: { p: PublicProfile }) {
  const v = p.verified;
  const any = v.university || v.github_login || v.email_verified;
  return (
    <section aria-labelledby="verified-title" className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-bg-elevated/70 p-5 shadow-[inset_0_1px_0_var(--hairline-highlight)]">
      <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_70%)]" />
      <div className="relative">
        <h2 id="verified-title" className="flex items-center gap-2 text-sm font-semibold text-fg">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-success-soft text-success">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          </span>
          Verified by DataBattles
        </h2>
        <p className="mt-1 text-xs text-muted">Checked by the platform — not self-reported.</p>
        {!any ? (
          <p className="mt-4 text-sm text-muted">No verified information yet.</p>
        ) : (
          <ul className="mt-4 space-y-3 text-sm">
            {v.university ? (
              <li className="flex items-start gap-2.5">
                <GraduationCap className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <Link href={`/orgs/${v.university.slug}`} className={cn("font-medium text-fg", linkCls)}>{v.university.name}</Link>
                  <VerifiedBadge label="Verified member" title="Membership verified by the university (email domain or administrator)" />
                </span>
              </li>
            ) : null}
            {v.github_login ? (
              <li className="flex items-start gap-2.5">
                <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <a href={`https://github.com/${v.github_login}`} target="_blank" rel="noopener noreferrer" className={cn("font-mono text-[13px] font-medium text-fg", linkCls)}>
                    @{v.github_login}
                  </a>
                  <VerifiedBadge label="GitHub linked" title="Linked through GitHub OAuth" />
                </span>
              </li>
            ) : null}
            {v.email_verified ? (
              <li className="flex items-start gap-2.5">
                <Mail className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-fg">Email address</span>
                  <VerifiedBadge label="Verified" title="The email address is confirmed (it is never shown publicly)" />
                </span>
              </li>
            ) : null}
          </ul>
        )}
      </div>
    </section>
  );
}

const STATS: { key: keyof PublicProfile["stats"]; label: string; icon: LucideIcon; tone: string }[] = [
  { key: "competitions", label: "Competitions", icon: Trophy, tone: "text-accent-strong" },
  { key: "top10_finishes", label: "Top-10 finishes", icon: Medal, tone: "text-warning" },
  { key: "certificates", label: "Certificates", icon: Award, tone: "text-success" },
  { key: "badges", label: "Badges", icon: BadgeCheck, tone: "text-cyan" },
  { key: "projects", label: "Projects", icon: FolderGit2, tone: "text-info" },
  { key: "courses_completed", label: "Courses", icon: GraduationCap, tone: "text-accent-strong" },
  { key: "merged_prs", label: "Merged PRs", icon: GitMerge, tone: "text-success" },
];

function Hero({ p }: { p: PublicProfile }) {
  const uni = p.verified.university ?? p.self_declared.university;
  return (
    <section aria-label="Profile summary" className="relative mt-6 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface shadow-elevated sm:mt-8">
      <Cover style={p.cover_style} className="h-28 sm:h-44">
        <div aria-hidden className="absolute inset-x-0 bottom-0 h-16 bg-[linear-gradient(to_top,var(--surface),transparent)]" />
      </Cover>
      {p.is_self ? (
        <div className="absolute right-4 top-4 z-20 flex flex-wrap justify-end gap-2 sm:right-6 sm:top-5">
          <LinkButton href="/settings/profile" size="sm" className="max-sm:h-9" icon={<Pencil className="h-3.5 w-3.5" />}>Edit profile</LinkButton>
          <LinkButton href="/settings/privacy" size="sm" variant="secondary" className="bg-[var(--glass-strong)] backdrop-blur-md max-sm:h-9" icon={<Lock className="h-3.5 w-3.5" />}>Privacy</LinkButton>
        </div>
      ) : null}
      <div className="relative z-10 grid gap-6 px-5 pb-6 sm:px-8 sm:pb-8 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-10">
        <div className="min-w-0">
          <div className="-mt-12 flex items-end gap-4 sm:-mt-16">
            <span className="relative shrink-0 rounded-full bg-brand p-[3px] shadow-glow">
              <Avatar name={p.display_name} src={p.avatar_url} size={104} className="ring-4 ring-surface" />
            </span>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-title min-w-0 break-words text-fg">{p.display_name}</h1>
            {p.is_demo ? <DemoBadge /> : null}
          </div>
          <p className="mt-2 flex flex-col gap-1 text-sm text-muted sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-4">
            <span className="font-mono text-[13px] text-fg/90">@{p.handle}</span>
            {uni ? (
              <>
                <Link href={`/orgs/${uni.slug}`} className={cn("inline-flex min-w-0 items-center gap-1.5", linkCls)}>
                  <GraduationCap className="h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden />
                  <span className="truncate">{uni.name}</span>
                  {p.verified.university ? <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-success" aria-label="Verified member" /> : null}
                </Link>
              </>
            ) : null}
            <span className="inline-flex items-center gap-1.5">
              <CalendarDays className="h-3.5 w-3.5 text-subtle" aria-hidden />
              joined {formatDate(p.joined_at, { month: "long", year: "numeric" })}
            </span>
          </p>
          {p.headline ? <p className="mt-4 max-w-2xl text-[15px] font-medium leading-relaxed text-fg">{p.headline}</p> : null}
          {p.bio_html ? (
            <div className="mt-2 max-w-2xl">
              <Prose html={p.bio_html} className="text-sm leading-relaxed [&_p]:text-muted" />
              <div className="mt-2.5">
                <SelfDeclaredBadge />
              </div>
            </div>
          ) : p.is_self ? (
            <p className="mt-3 text-sm text-muted">
              A few sentences about what you&apos;re learning and building helps teammates find you.{" "}
              <Link href="/settings/profile" className="font-medium text-accent-strong hover:underline">Write a bio</Link>
            </p>
          ) : null}
        </div>
        <div className="min-w-0 animate-rise [animation-delay:120ms] lg:pt-6">
          <VerifiedSection p={p} />
        </div>
      </div>
      {/* The ::after grid item fills the empty last cell on 2- and 4-column layouts, so the dl holds only dt/dd groups. */}
      <dl className="grid grid-cols-2 gap-px border-t border-border bg-border after:bg-surface after:content-[''] sm:grid-cols-4 xl:grid-cols-7 xl:after:hidden">
        {STATS.map((s) => (
          <div key={s.key} className="min-w-0 bg-surface px-5 py-4 xl:px-4">
            <dt className="flex items-center gap-1.5 text-eyebrow text-subtle xl:tracking-[0.1em]">
              <s.icon className={cn("h-3.5 w-3.5 shrink-0", s.tone)} aria-hidden />
              <span className="truncate">{s.label}</span>
            </dt>
            <dd className="mt-2 text-2xl font-semibold leading-none tracking-[-0.03em] text-fg">
              <CountUp value={p.stats[s.key]} />
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// ----------------------------------------------------------------------------- aside

function SelfDeclaredSection({ p, privacy }: { p: PublicProfile; privacy: Record<string, boolean> | null }) {
  const s = p.self_declared;
  const hiddenFlag = (key: string) => (privacy && privacy[key] === false ? <OwnerOnly /> : null);
  const rows: { key: string; label: string; value: ReactNode; flag?: string }[] = [];
  if (s.university) rows.push({ key: "uni", label: "University", value: <Link href={`/orgs/${s.university.slug}`} className={linkCls}>{s.university.name}</Link>, flag: "show_university" });
  if (s.department) rows.push({ key: "dept", label: "Department", value: s.department, flag: "show_department" });
  if (s.graduation_year) rows.push({ key: "grad", label: "Graduation year", value: String(s.graduation_year), flag: "show_graduation_year" });
  if (p.website_url)
    rows.push({
      key: "web",
      label: "Website",
      value: (
        <a href={p.website_url} target="_blank" rel="noopener noreferrer nofollow" className={cn("inline-flex items-center gap-1 break-all", linkCls)}>
          {p.website_url.replace(/^https?:\/\//, "")} <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
        </a>
      ),
    });
  const empty = !rows.length && !s.skills.length && !(p.is_self && s.interests.length);
  return (
    <section aria-labelledby="about-title" className={cn(panel, "p-5")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="about-title" className="text-sm font-semibold text-fg">About</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-muted">Provided by the user and not verified by the platform.</p>
        </div>
        <SelfDeclaredBadge />
      </div>
      {empty ? (
        <p className="mt-4 text-sm text-muted">Nothing shared yet.</p>
      ) : (
        <div className="mt-4 space-y-5">
          {rows.length ? (
            <dl className="space-y-3 text-sm">
              {rows.map((r) => (
                <div key={r.key} className="flex flex-col border-l border-border pl-3">
                  <dt className="text-eyebrow text-subtle">{r.label}</dt>
                  <dd className="mt-1 flex flex-wrap items-center gap-2 text-fg">
                    {r.value}
                    {p.is_self && r.flag ? hiddenFlag(r.flag) : null}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {s.skills.length ? (
            <div>
              <p className="mb-2 flex items-center gap-2 text-eyebrow text-subtle">Skills {p.is_self ? hiddenFlag("show_skills") : null}</p>
              <div className="flex flex-wrap gap-1.5">
                {s.skills.map((sk) => (
                  <span key={sk} className="rounded-md border border-border bg-bg-elevated px-2 py-0.5 font-mono text-xs text-muted">{sk}</span>
                ))}
              </div>
            </div>
          ) : null}
          {p.is_self && s.interests.length ? (
            <div>
              <p className="mb-2 flex items-center gap-2 text-eyebrow text-subtle">Interests <OwnerOnly>Only visible to you</OwnerOnly></p>
              <div className="flex flex-wrap gap-1.5">
                {s.interests.map((i) => <Badge key={i}>{i}</Badge>)}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------------- sections

function ResultsList({ p }: { p: PublicProfile }) {
  if (!p.results.length) return <EmptyState icon={<Trophy className="h-5 w-5" />} title="No final results yet" description="Results appear here after a competition's leaderboard is finalized." />;
  return (
    <ul className={cn(panel, "divide-y divide-border overflow-hidden")}>
      {p.results.map((r) => (
        <li key={r.id} className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-4 sm:px-5">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <RankBadge rank={r.rank} size="lg" />
            <div className="min-w-0">
              <Link href={`/competitions/${r.competition.slug}`} className={cn("font-medium text-fg", linkCls)}>{r.competition.title}</Link>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                {r.label ? <Badge tone="accent">{r.label}</Badge> : null}
                {r.is_demo ? <DemoBadge /> : null}
                {p.is_self && r.hidden ? <OwnerOnly /> : null}
              </div>
            </div>
          </div>
          <dl className="flex gap-6 pl-[3.25rem] text-sm sm:pl-0">
            <div>
              <dt className="text-eyebrow text-subtle">Rank</dt>
              <dd className="tabular mt-0.5 text-fg">{r.rank ? <>#{r.rank} <span className="text-muted">of {formatNumber(r.total_ranked)}</span></> : "—"}</dd>
            </div>
            <div>
              <dt className="text-eyebrow text-subtle">Final score</dt>
              <dd className="tabular mt-0.5 font-mono text-fg">{formatScore(r.score)}</dd>
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
    <PortfolioSection id="joined" title="Competitions joined" description={`${p.competitions.length} most recent`}>
      <ul className={cn(panel, "divide-y divide-border overflow-hidden")}>
        {p.competitions.map((c) => (
          <li key={c.slug} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3 text-sm sm:px-5">
            <Trophy className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
            <Link href={`/competitions/${c.slug}`} className={cn("min-w-0 flex-1 truncate text-fg", linkCls)}>{c.title}</Link>
            <StatusBadge status={c.status} />
            <span className="tabular text-xs text-subtle" title={formatDateTime(c.joined_at)}>joined {formatDate(c.joined_at)}</span>
          </li>
        ))}
      </ul>
    </PortfolioSection>
  );
}

function CertificatesList({ p, privacyHidden }: { p: PublicProfile; privacyHidden: boolean }) {
  return (
    <PortfolioSection
      id="certificates"
      eyebrow="Platform-issued"
      title="Certificates"
      description="Each certificate links to its public verification page."
      action={p.is_self && privacyHidden ? <OwnerOnly>Section hidden</OwnerOnly> : null}
    >
      {!p.certificates.length ? (
        <p className="text-sm text-muted">No certificates to show.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {p.certificates.map((c) => (
            <li key={c.public_id} className="flex">
              <Link
                href={`/verify/${c.public_id}`}
                className={cn(panel, "lift group relative flex w-full flex-col overflow-hidden p-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]")}
              >
                <div aria-hidden className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-success-soft blur-2xl" />
                <span className="relative flex items-start justify-between gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-success-soft text-success">
                    <Award className="h-4.5 w-4.5" aria-hidden />
                  </span>
                  <VerifiedBadge label="Verified" title="A valid certificate issued on DataBattles — open it to check its public verification page" />
                </span>
                <span className="relative mt-3 block font-semibold text-fg transition-colors group-hover:text-accent-strong">{c.result_label}</span>
                <span className="relative block text-sm text-muted">{c.event_title}</span>
                <span className="relative mt-3 flex flex-wrap items-center gap-1.5 text-xs text-subtle">
                  {c.issuer_name} · {formatDate(c.issued_at)}
                  {c.is_demo ? <DemoBadge /> : null}
                  {p.is_self && c.hidden ? <OwnerOnly /> : null}
                </span>
                <span className="relative mt-3 flex items-center justify-between gap-2 border-t border-border pt-3 font-mono text-[11px] text-subtle">
                  <span className="truncate tracking-[0.08em]">{c.public_id}</span>
                  <ArrowUpRight className="h-3.5 w-3.5 shrink-0 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PortfolioSection>
  );
}

function BadgesList({ p, privacyHidden }: { p: PublicProfile; privacyHidden: boolean }) {
  return (
    <PortfolioSection id="badges" eyebrow="Platform-awarded" title="Badges" action={p.is_self && privacyHidden ? <OwnerOnly>Section hidden</OwnerOnly> : null}>
      {!p.badges.length ? (
        <p className="text-sm text-muted">No badges to show.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {p.badges.map((b) => (
            <li key={b.id} className="flex">
              <Link
                href={`/badges/${b.public_id}`}
                className={cn(panel, "lift group flex w-full gap-3 p-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]")}
              >
                <BadgeIcon icon={b.icon} color={b.color} size={40} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{b.name}</span>
                  <span className="line-clamp-2 block text-xs leading-relaxed text-muted">{b.description}</span>
                  <span className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Badge tone="outline">{b.manual ? "Awarded by organizer" : "Automatic"}</Badge>
                    {b.rarity_label ? <Badge>{b.rarity_label}</Badge> : null}
                    {p.is_self && b.hidden ? <OwnerOnly /> : null}
                  </span>
                  <span className="mt-1.5 block text-[11px] text-subtle">{titleCase(b.category)} · {formatDate(b.awarded_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PortfolioSection>
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
    <ul className="grid gap-4 sm:grid-cols-2">
      {p.projects.map((pr) => (
        <li key={pr.slug} className={cn(panel, "lift group relative flex flex-col overflow-hidden")}>
          <Cover style={pr.cover_style} interactive className="h-28">
            {pr.is_demo ? (
              <div className="absolute left-3 top-3">
                <DemoBadge />
              </div>
            ) : null}
          </Cover>
          <div className="flex flex-1 flex-col p-4 sm:p-5">
            <Link href={`/projects/${pr.slug}`} className={cn("font-semibold tracking-[-0.015em] text-fg", linkCls)}>{pr.title}</Link>
            <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted">{pr.summary}</p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {pr.is_open_source ? <Badge tone="info" icon={<GitFork className="h-3 w-3" aria-hidden />}>Open source</Badge> : null}
              {pr.tags.slice(0, 4).map((t) => (
                <span key={t} className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-muted">{t}</span>
              ))}
            </div>
            {pr.repo_url || pr.demo_url ? (
              <div className="mt-auto flex gap-4 border-t border-border pt-3 text-xs">
                {pr.repo_url ? <a href={pr.repo_url} target="_blank" rel="noopener noreferrer" className={cn("inline-flex items-center gap-1 text-muted hover:text-fg", linkCls)}>Repository <ExternalLink className="h-3 w-3" aria-hidden /></a> : null}
                {pr.demo_url ? <a href={pr.demo_url} target="_blank" rel="noopener noreferrer" className={cn("inline-flex items-center gap-1 text-muted hover:text-fg", linkCls)}>Demo <ExternalLink className="h-3 w-3" aria-hidden /></a> : null}
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
    <ul className={cn(panel, "divide-y divide-border overflow-hidden")}>
      {p.courses.map((c) => (
        <li key={c.slug} className="flex items-center gap-3 px-4 py-3.5 sm:px-5">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-md)] border border-border bg-accent-soft text-accent-strong">
            <GraduationCap className="h-4 w-4" aria-hidden />
          </span>
          <Link href={`/learn/${c.slug}`} className={cn("min-w-0 flex-1 truncate text-sm font-medium text-fg", linkCls)}>{c.title}</Link>
          <span className="tabular shrink-0 text-xs text-subtle">Completed {formatDate(c.completed_at)}</span>
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
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card sm:grid-cols-3">
        <div className="bg-surface px-5 py-4">
          <dt className="text-eyebrow text-subtle">Merged PRs</dt>
          <dd className="tabular mt-2 text-2xl font-semibold leading-none tracking-[-0.03em] text-fg">{formatNumber(c.merged_count)}</dd>
        </div>
        <div className="bg-surface px-5 py-4">
          <dt className="text-eyebrow text-subtle">{c.repositories === 1 ? "Repository" : "Repositories"}</dt>
          <dd className="tabular mt-2 text-2xl font-semibold leading-none tracking-[-0.03em] text-fg">{formatNumber(c.repositories)}</dd>
        </div>
        <div className="col-span-2 min-w-0 bg-surface px-5 py-4 sm:col-span-1">
          <dt className="text-eyebrow text-subtle">GitHub</dt>
          <dd className="mt-2 flex min-w-0 items-center gap-1.5">
            <a href={`https://github.com/${c.github_login}`} target="_blank" rel="noopener noreferrer" className={cn("truncate font-mono text-sm font-medium text-fg", linkCls)}>
              @{c.github_login}
            </a>
            <ExternalLink className="h-3 w-3 shrink-0 text-subtle" aria-hidden />
          </dd>
        </div>
      </dl>
      <p className="flex flex-wrap items-center gap-2 text-xs text-subtle">
        <span>Merged pull requests across public repositories registered on DataBattles, attributed through the linked GitHub account.</span>
        {p.is_self && privacyHidden ? <OwnerOnly>Section hidden</OwnerOnly> : null}
      </p>
      {!c.items.length ? (
        <p className="text-sm text-muted">No merged pull requests in registered repositories yet.</p>
      ) : (
        <ul className={cn(panel, "divide-y divide-border overflow-hidden")}>
          {c.items.map((pr, i) => (
            <li key={`${pr.repo}#${pr.number}-${i}`} className="flex flex-col gap-1 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-4 sm:px-5">
              <span className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong sm:flex">
                <GitMerge className="h-3.5 w-3.5" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                {pr.url ? (
                  <a href={pr.url} target="_blank" rel="noopener noreferrer" className={cn("text-sm font-medium text-fg", linkCls)}>
                    {pr.title}
                  </a>
                ) : (
                  <span className="text-sm font-medium text-fg">{pr.title}</span>
                )}
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="font-mono">{pr.repo}#{pr.number}</span>
                  {pr.merged_at ? <span title={formatDateTime(pr.merged_at)}>merged {relativeTime(pr.merged_at)}</span> : null}
                  {pr.is_demo ? <DemoBadge /> : null}
                </p>
              </div>
              {pr.additions !== null || pr.deletions !== null ? (
                <span className="tabular shrink-0 font-mono text-xs">
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

type TimelineItem = PublicProfile["timeline"][number];

function TimelineList({ items, offset = 0 }: { items: TimelineItem[]; offset?: number }) {
  return (
    <ol className="relative space-y-4 before:absolute before:bottom-2 before:left-[13px] before:top-2 before:w-px before:bg-[linear-gradient(to_bottom,var(--border-strong),var(--border)_70%,transparent)]">
      {items.map((t, i) => {
        const external = t.url && !t.url.startsWith("/");
        return (
          <li key={`${t.kind}-${offset + i}`} className="relative flex items-start gap-3.5">
            <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface-2 text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)]">
              {TIMELINE_ICON[t.kind] ?? <CalendarDays className="h-3.5 w-3.5" aria-hidden />}
            </span>
            <div className="min-w-0 pt-1">
              {t.url ? (
                external ? (
                  <a href={t.url} target="_blank" rel="noopener noreferrer" className={cn("text-sm text-fg", linkCls)}>{t.title}</a>
                ) : (
                  <Link href={t.url} className={cn("text-sm text-fg", linkCls)}>{t.title}</Link>
                )
              ) : (
                <span className="text-sm text-fg">{t.title}</span>
              )}
              {t.at ? <p className="text-xs text-subtle" title={formatDateTime(t.at)}>{relativeTime(t.at)}</p> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const TIMELINE_PREVIEW = 6;

function Timeline({ p }: { p: PublicProfile }) {
  if (!p.timeline.length) return <p className="text-sm text-muted">No recent activity to show.</p>;
  const first = p.timeline.slice(0, TIMELINE_PREVIEW);
  const rest = p.timeline.slice(TIMELINE_PREVIEW);
  return (
    <div>
      <TimelineList items={first} />
      {rest.length ? (
        <details className="group/more mt-4">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm pl-[42px] text-xs font-medium text-accent-strong hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] group-open/more:hidden [&::-webkit-details-marker]:hidden">
            Show {rest.length} earlier {rest.length === 1 ? "event" : "events"}
          </summary>
          <TimelineList items={rest} offset={TIMELINE_PREVIEW} />
        </details>
      ) : null}
    </div>
  );
}

/** How many of the latest badges the showcase draws — the orbit and the accessible list always show the same ones. */
const BADGE_SHOWCASE = 6;

/** Badge constellation (only the person's real badges) beside an accessible list of exactly the same badges. */
function BadgeShowcase({ p }: { p: PublicProfile }) {
  const latest = p.badges.slice(0, BADGE_SHOWCASE);
  return (
    <div className={cn(panel, "relative grid items-center gap-6 overflow-hidden p-5 sm:grid-cols-[248px_minmax(0,1fr)] sm:p-6")}>
      <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-25 [mask-image:radial-gradient(ellipse_at_left,black,transparent_60%)]" />
      <BadgeOrbit badges={latest} total={p.badges.length} label={p.badges.length === 1 ? "badge" : "badges"} className="relative mx-auto max-w-[220px] sm:max-w-[248px]" />
      <ul className="relative space-y-2.5" aria-label={p.badges.length > latest.length ? `Latest ${latest.length} of ${p.badges.length} badges` : "Latest badges"}>
        {latest.map((b) => (
          <li key={b.id}>
            <Link href={`/badges/${b.public_id}`} className={cn("group flex items-center gap-3", linkCls)}>
              <BadgeIcon icon={b.icon} color={b.color} size={32} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{b.name}</span>
                <span className="block truncate text-xs text-subtle">
                  {titleCase(b.category)}
                  {b.rarity_label ? ` · ${b.rarity_label}` : ""} · {formatDate(b.awarded_at)}
                </span>
              </span>
              <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-subtle transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ----------------------------------------------------------------------------- page

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

      <Container className="pb-16">
        <Hero p={p} />
        {p.is_self ? (
          <p className="mt-4 flex items-start gap-2 rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 px-3.5 py-2.5 text-xs leading-relaxed text-muted">
            <EyeOff className="mt-px h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden />
            <span>
              You&apos;re viewing your own profile. Items marked “Hidden from public” are only visible to you.
              {!p.indexable ? " Search engines are asked not to index this page." : null}
            </span>
          </p>
        ) : null}

        <div className="mt-8 grid grid-cols-1 items-start gap-8 lg:grid-cols-12">
          <div className="min-w-0 lg:col-span-8">
            <Tabs tabs={tabs}>
              <TabPanel value="overview" className="space-y-10">
                {p.activity ? (
                  <PortfolioSection
                    id="activity"
                    eyebrow="Contribution heatmap"
                    title="Activity"
                    description="Aggregate daily counts only."
                    action={hidden("show_activity") ? <OwnerOnly>Section hidden</OwnerOnly> : null}
                  >
                    <div className={cn(panel, "p-5")}>
                      {/* Phones get a shorter window of the same data so the latest weeks are visible without scrolling. */}
                      <div className="sm:hidden">
                        <ActivityHeatmap counts={p.activity} days={126} label={`${p.display_name}'s activity`} />
                      </div>
                      <div className="hidden sm:block">
                        <ActivityHeatmap counts={p.activity} days={365} label={`${p.display_name}'s activity`} />
                      </div>
                    </div>
                  </PortfolioSection>
                ) : null}
                {p.badges.length ? (
                  <PortfolioSection id="constellation" eyebrow="Achievement constellation" title="Badges earned" description="Awarded by the platform when the criteria were met.">
                    <BadgeShowcase p={p} />
                  </PortfolioSection>
                ) : null}
                {p.results.length ? (
                  <PortfolioSection id="top-results" eyebrow="Recorded by the platform" title="Competition results">
                    <ResultsList p={{ ...p, results: p.results.slice(0, 5) }} />
                  </PortfolioSection>
                ) : null}
                {p.timeline.length ? (
                  <PortfolioSection id="timeline" eyebrow="Recent" title="Timeline">
                    <div className={cn(panel, "p-5")}>
                      <Timeline p={p} />
                    </div>
                  </PortfolioSection>
                ) : null}
                {!p.activity && !p.badges.length && !p.results.length && !p.timeline.length ? (
                  <EmptyState
                    title="Nothing to show yet"
                    description={
                      p.is_self
                        ? "Join a competition, finish a course or link GitHub — results and credentials appear here automatically."
                        : "This profile hasn't shared any activity."
                    }
                  />
                ) : null}
              </TabPanel>
              <TabPanel value="competitions" className="space-y-10">
                <PortfolioSection id="all-results" eyebrow="Recorded by the platform" title="Final results">
                  <ResultsList p={p} />
                </PortfolioSection>
                <JoinedList p={p} />
              </TabPanel>
              <TabPanel value="credentials" className="space-y-10">
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
          <aside className="min-w-0 space-y-4 lg:sticky lg:top-24 lg:col-span-4" aria-label="Profile details">
            <SelfDeclaredSection p={p} privacy={p.is_self ? privacy : null} />
            <p className="flex items-start gap-2 px-1 text-xs leading-relaxed text-subtle">
              <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              Results, certificates and badges are recorded by the platform. The bio and everything under “About” are self-declared.
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
