"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BookOpen,
  Building2,
  ChevronRight,
  Clock,
  ExternalLink,
  FolderGit2,
  Globe,
  Handshake,
  KeyRound,
  LogOut,
  MailCheck,
  MapPin,
  Settings,
  Trophy,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { OrgLogo, OrgVerificationBadge, ROLE_LABELS, VERIFICATION_METHOD_LABELS } from "@/components/orgs/org-ui";
import { AccentEdge, AccentGlow, DomainChips, FactGrid, OrgTypeIcon, SectionTitle, ListCount } from "@/components/orgs/org-visuals";
import type { OrgDetail } from "@/components/orgs/types";
import { Badge, DemoBadge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { MetaItem } from "@/components/ui/extras";
import { Prose } from "@/components/ui/markdown";
import { Cover } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton, SignInPrompt } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation, useMe, useNow } from "@/lib/hooks";
import { qk } from "@/lib/query";

function hostLabel(url: string) {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Chips layered on generated cover art. */
const glassChip = "border-0 bg-black/45 text-white ring-white/15 backdrop-blur-md";
const rowLink =
  "group flex min-w-0 items-center gap-3 px-4 py-3.5 transition-colors duration-200 hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] sm:px-5";
const iconChip = "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-2 text-subtle transition-colors group-hover:text-accent-strong";

function ViewerBox({ org }: { org: OrgDetail }) {
  const me = useMe();
  const v = org.viewer;
  const [lastOwner, setLastOwner] = useState<string | null>(null);
  const leave = useApiMutation(() => post<{ message: string }>(`/orgs/${org.slug}/membership/leave`), {
    success: (r) => r.message,
    invalidate: [qk.org(org.slug), qk.me, ["orgs", "mine"]],
    onError: (e) => setLastOwner(e.code === "last_owner" ? e.message : null),
  });
  const canJoinSomehow = org.domain_verification_available || org.allow_membership_requests;
  const active = v.membership_status === "active";
  const pending = v.membership_status === "pending";
  // Long organization names wrap inside full-width CTAs instead of overflowing.
  const wrapCta = "h-auto min-h-10 w-full whitespace-normal py-2.5 text-center leading-snug";

  return (
    <section aria-labelledby="membership-heading" className="relative flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-xl)] border border-border surface-glass shadow-card">
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
        <h2 id="membership-heading" className="text-eyebrow text-subtle">Your membership</h2>
        {active ? <Badge tone="accent">{ROLE_LABELS[v.role ?? "member"]}</Badge> : pending ? <StatusBadge status="pending" /> : null}
      </div>
      <div className="space-y-4 px-5 py-4">
        {me.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : !me.data ? (
          <>
            <SignInPrompt text="Members can verify with their institutional email or request to join." />
            {canJoinSomehow ? <LinkButton href={`/login?next=${encodeURIComponent(`/orgs/${org.slug}/join`)}`} className={wrapCta}>Join {org.name}</LinkButton> : null}
          </>
        ) : active ? (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {v.verified ? <VerifiedBadge label="Verified member" /> : <SelfDeclaredBadge />}
            </div>
            <p className="text-sm leading-relaxed text-muted">
              {v.verified
                ? "Your membership is verified and is shown as verified on your profile."
                : "Your membership is not verified, so your profile won’t show it as verified."}
            </p>
            {!v.verified && org.domain_verification_available ? (
              <LinkButton href={`/orgs/${org.slug}/join`} variant="secondary" size="sm" className="w-full" icon={<MailCheck className="h-4 w-4" />}>
                Verify with institutional email
              </LinkButton>
            ) : null}
          </>
        ) : pending ? (
          <>
            <p className="flex items-start gap-2.5 text-sm leading-relaxed text-muted">
              <Clock className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
              Your request to join is waiting for an administrator to review it. You’ll get a notification.
            </p>
            {org.domain_verification_available ? (
              <LinkButton href={`/orgs/${org.slug}/join`} variant="secondary" size="sm" className="w-full" icon={<MailCheck className="h-4 w-4" />}>
                Verify instantly with institutional email
              </LinkButton>
            ) : null}
          </>
        ) : canJoinSomehow ? (
          <>
            <p className="text-sm leading-relaxed text-muted">
              {v.membership_status === "rejected"
                ? "Your previous request was declined. You can try again or verify with your institutional email."
                : org.domain_verification_available
                  ? "Join instantly with your institutional email, or request membership."
                  : "Request membership and an administrator will review it."}
            </p>
            <LinkButton href={`/orgs/${org.slug}/join`} className={wrapCta}>Join {org.name}</LinkButton>
          </>
        ) : (
          <p className="flex items-start gap-2.5 text-sm leading-relaxed text-muted">
            <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
            Membership is by invitation only. Ask an administrator of {org.name} for an invite link.
          </p>
        )}

        {lastOwner ? (
          <InlineNotice tone="warning" title="You’re the last owner">
            {lastOwner} Promote another member to owner from the members page first.
          </InlineNotice>
        ) : null}

        {me.data && (active || pending) ? (
          <ConfirmDialog
            trigger={
              <Button variant="ghost" size="sm" className="w-full text-danger" icon={<LogOut className="h-4 w-4" />}>
                {pending ? "Withdraw request" : "Leave organization"}
              </Button>
            }
            title={pending ? `Withdraw your request to ${org.name}?` : `Leave ${org.name}?`}
            description={
              pending
                ? "You can request again later."
                : "Your verified membership will be removed from your profile. To rejoin you’ll need to verify or be approved again."
            }
            confirmLabel={pending ? "Withdraw" : "Leave"}
            onConfirm={async () => {
              setLastOwner(null);
              await leave.mutateAsync(undefined).catch(() => undefined);
            }}
          />
        ) : null}
      </div>
      {v.can_manage_content ? (
        <div className="grid gap-2 border-t border-border bg-bg-elevated/40 px-5 py-4">
          <LinkButton href={`/orgs/${org.slug}/admin`} variant="secondary" size="sm" icon={<Settings className="h-4 w-4" />}>
            {v.can_manage ? "Admin" : "Manage"}
          </LinkButton>
          {org.type === "sponsor" ? (
            <LinkButton href={`/orgs/${org.slug}/sponsor`} variant="secondary" size="sm" icon={<BarChart3 className="h-4 w-4" />}>
              Sponsor dashboard
            </LinkButton>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function joinMode(org: OrgDetail): string {
  if (org.domain_verification_available && org.allow_membership_requests) return "Email or request";
  if (org.domain_verification_available) return "Institutional email";
  if (org.allow_membership_requests) return "Request to join";
  return "Invite only";
}

function OrgHeader({ org }: { org: OrgDetail }) {
  const location = [org.city, org.country].filter(Boolean).join(", ");
  return (
    <header className="relative isolate overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-card animate-rise">
      <AccentEdge color={org.accent_color} />
      <AccentGlow color={org.accent_color} strength={24} className="-right-24 -top-40 h-[26rem] w-[44rem] max-w-full" />
      <AccentGlow color={org.accent_color} strength={10} className="-left-32 bottom-0 h-64 w-96 max-w-full" />
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-35 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_60%)]" />

      <div className="grid grid-cols-1 gap-6 p-5 sm:p-8 lg:grid-cols-[minmax(0,1fr)_21rem] lg:gap-10">
        <div className="flex min-w-0 flex-col">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
            <OrgLogo name={org.name} logoUrl={org.logo_url} accentColor={org.accent_color} size={76} />
            <div className="min-w-0">
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-eyebrow text-subtle">
                <span className="inline-flex items-center gap-1.5 text-accent-strong">
                  <OrgTypeIcon type={org.type} className="h-3.5 w-3.5" />
                  {titleCase(org.type)}
                </span>
              </p>
              <h1 className="mt-2.5 text-title text-fg [overflow-wrap:anywhere]">{org.name}</h1>
              {org.tagline ? <p className="mt-2.5 max-w-2xl text-[15px] leading-relaxed text-muted">{org.tagline}</p> : null}
              <div className="mt-4 flex flex-wrap items-center gap-1.5">
                <OrgVerificationBadge status={org.verification_status} />
                {org.is_demo ? <DemoBadge /> : null}
              </div>
            </div>
          </div>

          {location || org.website_url || org.departments.length ? (
            <div aria-hidden className="min-h-6 grow" />
          ) : null}
          {location || org.website_url || org.departments.length ? (
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border pt-5 text-sm text-muted">
              {location ? <span className="inline-flex items-center gap-1.5"><MapPin className="h-4 w-4 text-subtle" aria-hidden />{location}</span> : null}
              {org.website_url ? (
                <a
                  href={org.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-w-0 items-center gap-1.5 rounded-sm transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                >
                  <Globe className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
                  <span className="truncate">{hostLabel(org.website_url)}</span>
                  <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              ) : null}
              {org.departments.length ? (
                <span className="inline-flex items-center gap-1.5"><Building2 className="h-4 w-4 text-subtle" aria-hidden /><span className="tabular">{org.departments.length}</span> departments</span>
              ) : null}
            </div>
          ) : null}
        </div>

        <ViewerBox org={org} />
      </div>

      <FactGrid className="border-t border-border bg-bg-elevated/30" cols="grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
        <MetaItem icon={<Users />} label="Members">
          <span className="tabular">{compactNumber(org.member_count)}</span>
        </MetaItem>
        <MetaItem icon={<Trophy />} label="Competitions">
          <ListCount n={org.competitions.length} />
          {org.sponsored_competitions.length ? (
            <span className="tabular ml-1.5 text-xs font-normal text-subtle">+{org.sponsored_competitions.length} sponsored</span>
          ) : null}
        </MetaItem>
        <MetaItem icon={<BookOpen />} label="Courses">
          <ListCount n={org.courses.length} />
        </MetaItem>
        <MetaItem icon={<FolderGit2 />} label="Projects">
          <ListCount n={org.projects.length} />
        </MetaItem>
        <MetaItem icon={<KeyRound />} label="Joining">
          {joinMode(org)}
        </MetaItem>
      </FactGrid>
    </header>
  );
}

function OrgPageSkeleton() {
  return (
    <Container className="pb-20">
      <div role="status" aria-label="Loading organization">
        <Skeleton className="mt-6 h-4 w-48" />
        <div className="mt-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface">
          <div className="grid gap-6 p-5 sm:p-8 lg:grid-cols-[minmax(0,1fr)_21rem]">
            <div className="flex gap-5">
              <Skeleton className="h-[76px] w-[76px] shrink-0 rounded-2xl" />
              <div className="flex-1 space-y-3">
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-8 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-5 w-56 rounded-full" />
              </div>
            </div>
            <Skeleton className="h-44 w-full rounded-[var(--radius-xl)]" />
          </div>
          <div className="grid grid-cols-2 gap-4 border-t border-border p-5 sm:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i}>
                <Skeleton className="h-2.5 w-16" />
                <Skeleton className="mt-2 h-5 w-12" />
              </div>
            ))}
          </div>
        </div>
        <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="space-y-3">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-40 w-full rounded-[var(--radius-lg)]" />
          </div>
          <Skeleton className="h-48 w-full rounded-[var(--radius-lg)]" />
        </div>
      </div>
    </Container>
  );
}

export default function OrgPage() {
  const { slug } = useParams<{ slug: string }>();
  const org = useQuery({ queryKey: qk.org(slug), queryFn: () => get<OrgDetail>(`/orgs/${slug}`) });
  const now = useNow(60_000);

  if (org.isPending) return <OrgPageSkeleton />;
  if (org.isError) {
    return (
      <Container className="py-10">
        {org.error instanceof ApiError && org.error.status === 404 ? (
          <EmptyState title="Organization not found" description="It may have been renamed or removed." action={<LinkButton href="/orgs" variant="secondary">Browse organizations</LinkButton>} />
        ) : (
          <ErrorState error={org.error} onRetry={() => org.refetch()} />
        )}
      </Container>
    );
  }
  const o = org.data;
  const nothing = !o.competitions.length && !o.sponsored_competitions.length && !o.courses.length && !o.projects.length;
  const showDomains = o.domain_verification_available && o.email_domains.length > 0;
  const hasAside = o.departments.length > 0 || showDomains;

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <ol className="flex min-w-0 items-center gap-1.5">
          <li><Link href="/orgs" className="transition-colors hover:text-fg">Organizations</Link></li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="min-w-0 truncate text-muted" aria-current="page">{o.name}</li>
        </ol>
      </nav>

      <div className="mt-5">
        <OrgHeader org={o} />
      </div>

      <div className={cn("mt-10 grid grid-cols-1 gap-10", hasAside && "lg:grid-cols-[minmax(0,1fr)_20rem]")}>
        <div className="min-w-0 space-y-12">
          {o.description_html ? (
            <section aria-labelledby="org-about">
              <SectionTitle id="org-about" eyebrow="Profile" title="About" />
              <div className="max-w-3xl">
                <Prose html={o.description_html} />
              </div>
            </section>
          ) : null}

          {o.competitions.length ? (
            <section aria-labelledby="org-competitions">
              <SectionTitle
                id="org-competitions"
                eyebrow="Compete"
                title="Hosted competitions"
                description={o.viewer.membership_status === "active" ? "Includes members-only competitions you can see as a member." : undefined}
              />
              <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {o.competitions.map((c) => (
                  <li key={c.slug} className="min-w-0">
                    <Link
                      href={`/competitions/${c.slug}`}
                      className="group lift flex h-full min-w-0 flex-col overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                    >
                      <Cover style={c.cover_style} interactive className="h-24 shrink-0">
                        <div className="absolute inset-x-3 top-3 flex flex-wrap items-center gap-1.5">
                          <StatusBadge status={c.status} className={glassChip} />
                          {c.visibility === "university" ? <Badge tone="info" className={glassChip} title="Visible only to members of this organization">Members only</Badge> : null}
                        </div>
                      </Cover>
                      <div className="flex flex-1 flex-col p-4">
                        <h3 className="line-clamp-2 text-[15px] font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong">{c.title}</h3>
                        <div aria-hidden className="min-h-3 grow" />
                        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border pt-3 text-xs text-muted">
                          <span className="inline-flex items-center gap-1.5">
                            <Users className="h-3.5 w-3.5 text-subtle" aria-hidden />
                            <span className="tabular">{compactNumber(c.participant_count)}</span> participants
                          </span>
                          {c.ends_at ? (
                            <span className="inline-flex items-center gap-1.5" title={formatDateTime(c.ends_at)}>
                              <Clock className="h-3.5 w-3.5 text-subtle" aria-hidden />
                              {new Date(c.ends_at).getTime() > now.getTime() ? "Ends" : "Ended"} {relativeTime(c.ends_at)}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {o.sponsored_competitions.length ? (
            <section aria-labelledby="org-sponsored">
              <SectionTitle id="org-sponsored" eyebrow="Support" title="Sponsored competitions" />
              <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
                {o.sponsored_competitions.map((c) => (
                  <li key={c.slug}>
                    <Link href={`/competitions/${c.slug}`} className={rowLink}>
                      <span className={iconChip}><Handshake className="h-4 w-4" aria-hidden /></span>
                      <span className="min-w-0 flex-1 truncate font-medium text-fg transition-colors group-hover:text-accent-strong">{c.title}</span>
                      <Badge tone="outline">{titleCase(c.tier)}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {o.courses.length || o.projects.length ? (
            <div className={cn("grid grid-cols-1 gap-10", o.courses.length && o.projects.length ? "md:grid-cols-2 md:gap-6" : undefined)}>
              {o.courses.length ? (
                <section aria-labelledby="org-courses" className="min-w-0">
                  <SectionTitle id="org-courses" eyebrow="Learn" title="Courses" />
                  <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
                    {o.courses.map((c) => (
                      <li key={c.slug}>
                        <Link href={`/learn/${c.slug}`} className={rowLink}>
                          <span className={iconChip}><BookOpen className="h-4 w-4" aria-hidden /></span>
                          <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{c.title}</span>
                          <Badge>{titleCase(c.difficulty)}</Badge>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {o.projects.length ? (
                <section aria-labelledby="org-projects" className="min-w-0">
                  <SectionTitle id="org-projects" eyebrow="Build" title="Projects" />
                  <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
                    {o.projects.map((p) => (
                      <li key={p.slug}>
                        <Link href={`/projects/${p.slug}`} className={cn(rowLink, "items-start")}>
                          <span className={iconChip}><FolderGit2 className="h-4 w-4" aria-hidden /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{p.title}</span>
                            {p.summary ? <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-muted">{p.summary}</span> : null}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </div>
          ) : null}

          {nothing && !o.description_html ? (
            <EmptyState icon={<Trophy />} title="Nothing published yet" description={`${o.name} hasn’t published competitions, courses or projects yet.`} />
          ) : null}
        </div>

        {hasAside ? (
          <aside className="min-w-0 space-y-4 lg:sticky lg:top-24 lg:self-start" aria-label="Organization details">
            {o.departments.length ? (
              <section aria-labelledby="org-departments" className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card">
                <div className="flex items-baseline justify-between gap-3">
                  <h2 id="org-departments" className="text-sm font-semibold tracking-[-0.01em] text-fg">Departments</h2>
                  <span className="tabular text-xs text-subtle">{o.departments.length} listed</span>
                </div>
                <ul className="mt-3 flex flex-wrap gap-1.5">
                  {o.departments.map((d) => <li key={d.id}><Badge tone="outline">{d.name}</Badge></li>)}
                </ul>
              </section>
            ) : null}
            {showDomains ? (
              <section aria-labelledby="org-domains" className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card">
                <h2 id="org-domains" className="flex items-center gap-2 text-sm font-semibold tracking-[-0.01em] text-fg">
                  <MailCheck className="h-4 w-4 text-success" aria-hidden /> Institutional email
                </h2>
                <p className="mt-1 text-xs leading-relaxed text-muted">Addresses on these domains can verify membership.</p>
                <DomainChips domains={o.email_domains} className="mt-3" />
                <p className="mt-3 text-xs leading-relaxed text-subtle">{VERIFICATION_METHOD_LABELS.domain} verification is confirmed by a one-time link sent to that inbox.</p>
              </section>
            ) : null}
          </aside>
        ) : null}
      </div>
    </Container>
  );
}
