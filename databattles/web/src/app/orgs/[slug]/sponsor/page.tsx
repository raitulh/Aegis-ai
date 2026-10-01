"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Eye, Handshake, Send, Settings, ShieldCheck, Sparkles, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { UserLink } from "@/components/domain/cards";
import { OrgLogo } from "@/components/orgs/org-ui";
import { AccentEdge, AccentGlow, Metric, MetricGrid } from "@/components/orgs/org-visuals";
import type { SponsorDashboard } from "@/components/orgs/types";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { RankBadge } from "@/components/ui/extras";
import { Container } from "@/components/ui/page";
import { EmptyState, QueryState, Skeleton, Spinner } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { compactNumber, formatNumber, titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";

export default function SponsorDashboardPage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const dash = useQuery({
    queryKey: ["orgs", slug, "sponsor-dashboard"],
    queryFn: () => get<SponsorDashboard>(`/orgs/${slug}/sponsor/dashboard`),
    enabled: Boolean(me.data),
  });

  if (me.isPending || !me.data) return <Spinner />;

  return (
    <Container className="pb-20">
      <Link
        href={`/orgs/${slug}`}
        className="mt-7 inline-flex items-center gap-1.5 rounded-sm text-sm text-muted transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden /> Organization page
      </Link>
      <QueryState
        query={dash}
        loading={
          <div className="mt-5 space-y-6" role="status" aria-label="Loading sponsor dashboard">
            <Skeleton className="h-32 w-full rounded-[var(--radius-2xl)]" />
            <div className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border sm:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="bg-surface p-5">
                  <Skeleton className="h-2.5 w-24" />
                  <Skeleton className="mt-4 h-7 w-16" />
                </div>
              ))}
            </div>
            <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
          </div>
        }
      >
        {(d) => (
          <div className="mt-5 space-y-8">
            <header className="relative isolate overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-card animate-rise">
              <AccentEdge color={d.org.accent_color} />
              <AccentGlow color={d.org.accent_color} strength={20} className="-right-24 -top-36 h-80 w-[36rem] max-w-full" />
              <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_60%)]" />
              <div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-8">
                <div className="flex min-w-0 items-center gap-4">
                  <OrgLogo name={d.org.name} logoUrl={d.org.logo_url} accentColor={d.org.accent_color} size={60} />
                  <div className="min-w-0">
                    <p className="inline-flex items-center gap-1.5 text-eyebrow text-accent-strong">
                      <Handshake className="h-3.5 w-3.5" aria-hidden /> Sponsor dashboard
                    </p>
                    <h1 className="mt-1.5 text-title text-fg [overflow-wrap:anywhere]">{d.org.name}</h1>
                    {d.org.is_demo ? <DemoBadge className="mt-2" /> : null}
                  </div>
                </div>
                <LinkButton href={`/orgs/${slug}/admin`} variant="secondary" icon={<Settings className="h-4 w-4" />} className="self-start sm:self-center">
                  Organization admin
                </LinkButton>
              </div>
            </header>

            <MetricGrid cols="grid-cols-1 sm:grid-cols-3">
              <Metric label="Sponsored events" value={formatNumber(d.totals.events)} icon={<Handshake />} />
              <Metric label="Participants" value={formatNumber(d.totals.participants)} icon={<Users />} hint="Across sponsored events" />
              <Metric label="Submissions" value={formatNumber(d.totals.submissions)} icon={<Send />} hint="Across sponsored events" />
            </MetricGrid>

            <section aria-labelledby="sponsored-heading">
              <div className="mb-3">
                <h2 id="sponsored-heading" className="text-lg font-semibold tracking-[-0.02em] text-fg">Sponsored competitions</h2>
                <p className="mt-0.5 text-sm text-muted">Engagement for each event you sponsor.</p>
              </div>
              {d.sponsored.length ? (
                <Table className="relative">
                  <THead>
                    <tr>
                      <TH>Competition</TH>
                      <TH>Tier</TH>
                      <TH>Status</TH>
                      <TH className="text-right">Participants</TH>
                      <TH className="text-right">Teams</TH>
                      <TH className="text-right">Submissions</TH>
                      <TH className="text-right"><span className="inline-flex items-center gap-1"><Eye className="h-3.5 w-3.5" aria-hidden />Views</span></TH>
                    </tr>
                  </THead>
                  <TBody>
                    {d.sponsored.map((c) => (
                      <TR key={c.slug}>
                        <TD className="min-w-56"><Link href={`/competitions/${c.slug}`} className="font-medium text-fg hover:text-accent-strong">{c.title}</Link></TD>
                        <TD><Badge tone="outline">{titleCase(c.tier)}</Badge></TD>
                        <TD><StatusBadge status={c.status} /></TD>
                        <TD className="tabular text-right">{formatNumber(c.participants)}</TD>
                        <TD className="tabular text-right">{formatNumber(c.teams)}</TD>
                        <TD className="tabular text-right">{formatNumber(c.submissions)}</TD>
                        <TD className="tabular text-right">{compactNumber(c.views)}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              ) : (
                <EmptyState
                  icon={<Handshake />}
                  title="No sponsored competitions yet"
                  description="Competition organizers add sponsors from their competition settings. Once you sponsor an event, its engagement appears here."
                />
              )}
            </section>

            <section aria-labelledby="talent-heading">
              <div className="mb-3">
                <h2 id="talent-heading" className="inline-flex items-center gap-2 text-lg font-semibold tracking-[-0.02em] text-fg">
                  <Sparkles className="h-4 w-4 text-accent-strong" aria-hidden />Talent
                </h2>
                <p className="mt-0.5 text-sm text-muted">Participants of your sponsored events who opted in.</p>
              </div>
              <p className="mb-4 flex items-start gap-2 rounded-[var(--radius-md)] border border-border bg-surface/70 px-3.5 py-2.5 text-xs leading-relaxed text-muted">
                <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden /> {d.talent_note}
              </p>
              {d.talent.length ? (
                <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {d.talent.map((t) => (
                    <li key={t.user.id} className="flex min-w-0 flex-col rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-4 shadow-card">
                      <div className="flex items-start justify-between gap-3">
                        <UserLink user={t.user} size={32} className="font-medium" />
                        <span className="inline-flex shrink-0 items-center gap-2 text-xs text-muted">
                          {t.best_rank ? <>Best rank <RankBadge rank={t.best_rank} size="sm" /></> : "Unranked"}
                        </span>
                      </div>
                      {t.headline ? <p className="mt-2.5 text-sm leading-relaxed text-muted">{t.headline}</p> : null}
                      {t.skills.length ? (
                        <div className="mt-2.5 flex flex-wrap gap-1">
                          {t.skills.map((s) => <span key={s} className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-muted">{s}</span>)}
                        </div>
                      ) : null}
                      <div aria-hidden className="min-h-3 grow" />
                      <div className="mt-3 flex items-center justify-between gap-3 border-t border-border pt-3 text-xs text-subtle">
                        <span className="tabular">{t.sponsored_events} sponsored event{t.sponsored_events === 1 ? "" : "s"}</span>
                        <Link href={`/u/${t.user.handle}`} className="font-medium text-accent-strong hover:underline">View public profile</Link>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  icon={<Sparkles />}
                  title="No opted-in participants with results yet"
                  description="Participants appear here only after they turn on “Open to opportunities” and finish a sponsored event with a result."
                />
              )}
            </section>
          </div>
        )}
      </QueryState>
    </Container>
  );
}
