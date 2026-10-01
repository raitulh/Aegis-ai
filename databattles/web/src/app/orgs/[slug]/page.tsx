"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3, BookOpen, ExternalLink, FolderGit2, Globe, Handshake, LogOut, MapPin, Settings, Trophy, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { OrgLogo, OrgVerificationBadge, ROLE_LABELS, VERIFICATION_METHOD_LABELS } from "@/components/orgs/org-ui";
import type { OrgDetail } from "@/components/orgs/types";
import { Badge, DemoBadge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Prose } from "@/components/ui/markdown";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton, SignInPrompt } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { compactNumber, formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

function hostLabel(url: string) {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

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

  return (
    <Card>
      <CardHeader title="Your membership" />
      <CardBody className="space-y-4">
        {me.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : !me.data ? (
          <>
            <SignInPrompt text="Members can verify with their institutional email or request to join." />
            {canJoinSomehow ? <LinkButton href={`/login?next=${encodeURIComponent(`/orgs/${org.slug}/join`)}`} className="w-full">Join {org.name}</LinkButton> : null}
          </>
        ) : active ? (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="accent">{ROLE_LABELS[v.role ?? "member"]}</Badge>
              {v.verified ? <VerifiedBadge label="Verified member" /> : <SelfDeclaredBadge />}
            </div>
            <p className="text-sm text-muted">
              {v.verified
                ? "Your membership is verified and is shown as verified on your profile."
                : "Your membership is not verified, so your profile won’t show it as verified."}
            </p>
            {!v.verified && org.domain_verification_available ? (
              <LinkButton href={`/orgs/${org.slug}/join`} variant="secondary" size="sm" className="w-full">Verify with institutional email</LinkButton>
            ) : null}
          </>
        ) : pending ? (
          <>
            <StatusBadge status="pending" />
            <p className="text-sm text-muted">Your request to join is waiting for an administrator to review it. You’ll get a notification.</p>
            {org.domain_verification_available ? (
              <LinkButton href={`/orgs/${org.slug}/join`} variant="secondary" size="sm" className="w-full">Verify instantly with institutional email</LinkButton>
            ) : null}
          </>
        ) : canJoinSomehow ? (
          <>
            <p className="text-sm text-muted">
              {v.membership_status === "rejected"
                ? "Your previous request was declined. You can try again or verify with your institutional email."
                : org.domain_verification_available
                  ? "Join instantly with your institutional email, or request membership."
                  : "Request membership and an administrator will review it."}
            </p>
            <LinkButton href={`/orgs/${org.slug}/join`} className="w-full">Join {org.name}</LinkButton>
          </>
        ) : (
          <p className="text-sm text-muted">Membership is by invitation only. Ask an administrator of {org.name} for an invite link.</p>
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
      </CardBody>
      {v.can_manage_content ? (
        <div className="flex flex-col gap-2 border-t border-border px-5 py-4">
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
    </Card>
  );
}

function OrgHeader({ org }: { org: OrgDetail }) {
  const location = [org.city, org.country].filter(Boolean).join(", ");
  return (
    <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface">
      <div className="h-1.5" style={{ background: org.accent_color ?? "var(--accent)" }} aria-hidden />
      <div className="flex flex-col gap-5 p-6 sm:flex-row sm:items-center">
        <OrgLogo name={org.name} logoUrl={org.logo_url} accentColor={org.accent_color} size={72} className="rounded-xl" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <OrgVerificationBadge status={org.verification_status} />
            <Badge tone="outline">{titleCase(org.type)}</Badge>
            {org.is_demo ? <DemoBadge /> : null}
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{org.name}</h1>
          {org.tagline ? <p className="mt-1 text-muted">{org.tagline}</p> : null}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            {location ? <span className="inline-flex items-center gap-1.5"><MapPin className="h-4 w-4" aria-hidden />{location}</span> : null}
            {org.website_url ? (
              <a href={org.website_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 hover:text-fg">
                <Globe className="h-4 w-4" aria-hidden />
                {hostLabel(org.website_url)}
                <ExternalLink className="h-3 w-3" aria-hidden />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ) : null}
            <span className="inline-flex items-center gap-1.5"><Users className="h-4 w-4" aria-hidden />{compactNumber(org.member_count)} members</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function OrgPageSkeleton() {
  return (
    <Container className="py-8">
      <div role="status" aria-label="Loading organization">
        <Skeleton className="h-36 w-full rounded-[var(--radius-xl)]" />
        <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_320px]">
          <div className="space-y-3">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-40 w-full" />
          </div>
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    </Container>
  );
}

export default function OrgPage() {
  const { slug } = useParams<{ slug: string }>();
  const org = useQuery({ queryKey: qk.org(slug), queryFn: () => get<OrgDetail>(`/orgs/${slug}`) });

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

  return (
    <Container className="py-8">
      <OrgHeader org={o} />
      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-6">
          {o.description_html ? (
            <Card>
              <CardHeader title="About" />
              <CardBody><Prose html={o.description_html} /></CardBody>
            </Card>
          ) : null}

          {o.competitions.length ? (
            <Card>
              <CardHeader title="Hosted competitions" description={o.viewer.membership_status === "active" ? "Includes members-only competitions you can see as a member." : undefined} />
              <ul className="divide-y divide-border">
                {o.competitions.map((c) => (
                  <li key={c.slug} className="flex flex-col gap-1 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <Link href={`/competitions/${c.slug}`} className="font-medium text-fg hover:text-accent-strong">{c.title}</Link>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-subtle">
                        <StatusBadge status={c.status} />
                        {c.visibility === "university" ? <Badge tone="info" title="Visible only to members of this organization">Members only</Badge> : null}
                        <span>{compactNumber(c.participant_count)} participants</span>
                      </div>
                    </div>
                    {c.ends_at ? (
                      <span className="shrink-0 text-xs text-muted" title={formatDateTime(c.ends_at)}>
                        {new Date(c.ends_at).getTime() > Date.now() ? "Ends" : "Ended"} {relativeTime(c.ends_at)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          {o.sponsored_competitions.length ? (
            <Card>
              <CardHeader title="Sponsored competitions" />
              <ul className="divide-y divide-border">
                {o.sponsored_competitions.map((c) => (
                  <li key={c.slug} className="flex items-center justify-between gap-3 px-5 py-3">
                    <Link href={`/competitions/${c.slug}`} className="min-w-0 truncate font-medium text-fg hover:text-accent-strong">
                      <Handshake className="mr-2 inline h-4 w-4 text-subtle" aria-hidden />
                      {c.title}
                    </Link>
                    <Badge tone="outline">{titleCase(c.tier)}</Badge>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          {o.courses.length || o.projects.length ? (
            <div className="grid gap-6 md:grid-cols-2">
              {o.courses.length ? (
                <Card>
                  <CardHeader title="Courses" />
                  <ul className="divide-y divide-border">
                    {o.courses.map((c) => (
                      <li key={c.slug} className="flex items-center justify-between gap-3 px-5 py-3">
                        <Link href={`/learn/${c.slug}`} className="min-w-0 truncate text-sm font-medium text-fg hover:text-accent-strong">
                          <BookOpen className="mr-2 inline h-4 w-4 text-subtle" aria-hidden />
                          {c.title}
                        </Link>
                        <Badge>{titleCase(c.difficulty)}</Badge>
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : null}
              {o.projects.length ? (
                <Card>
                  <CardHeader title="Projects" />
                  <ul className="divide-y divide-border">
                    {o.projects.map((p) => (
                      <li key={p.slug} className="px-5 py-3">
                        <Link href={`/projects/${p.slug}`} className="text-sm font-medium text-fg hover:text-accent-strong">
                          <FolderGit2 className="mr-2 inline h-4 w-4 text-subtle" aria-hidden />
                          {p.title}
                        </Link>
                        {p.summary ? <p className="mt-0.5 line-clamp-2 text-xs text-muted">{p.summary}</p> : null}
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : null}
            </div>
          ) : null}

          {nothing && !o.description_html ? (
            <EmptyState icon={<Trophy className="h-5 w-5" />} title="Nothing published yet" description={`${o.name} hasn’t published competitions, courses or projects yet.`} />
          ) : null}
        </div>

        <aside className="space-y-6" aria-label="Organization details">
          <ViewerBox org={o} />
          {o.departments.length ? (
            <Card>
              <CardHeader title="Departments" description={`${o.departments.length} listed`} />
              <CardBody>
                <ul className="flex flex-wrap gap-1.5">
                  {o.departments.map((d) => <li key={d.id}><Badge tone="outline">{d.name}</Badge></li>)}
                </ul>
              </CardBody>
            </Card>
          ) : null}
          {o.domain_verification_available && o.email_domains.length ? (
            <Card>
              <CardHeader title="Institutional email" description="Addresses on these domains can verify membership." />
              <CardBody>
                <ul className="flex flex-wrap gap-1.5 font-mono text-xs">
                  {o.email_domains.map((d) => <li key={d} className="rounded bg-surface-2 px-2 py-1 text-muted">@{d}</li>)}
                </ul>
                <p className="mt-3 text-xs text-subtle">{VERIFICATION_METHOD_LABELS.domain} verification is confirmed by a one-time link sent to that inbox.</p>
              </CardBody>
            </Card>
          ) : null}
        </aside>
      </div>
    </Container>
  );
}
