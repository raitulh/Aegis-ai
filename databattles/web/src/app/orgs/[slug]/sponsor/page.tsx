"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Eye, Handshake, Send, ShieldCheck, Sparkles, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { UserLink } from "@/components/domain/cards";
import { OrgLogo } from "@/components/orgs/org-ui";
import type { SponsorDashboard } from "@/components/orgs/types";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
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
    <Container className="py-8">
      <Link href={`/orgs/${slug}`} className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Organization page
      </Link>
      <QueryState
        query={dash}
        loading={
          <div className="mt-6 space-y-6" role="status" aria-label="Loading sponsor dashboard">
            <Skeleton className="h-14 w-80" />
            <div className="grid gap-3 sm:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full" />)}</div>
            <Skeleton className="h-64 w-full" />
          </div>
        }
      >
        {(d) => (
          <div className="mt-4 space-y-6">
            <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-3">
                <OrgLogo name={d.org.name} logoUrl={d.org.logo_url} accentColor={d.org.accent_color} size={52} />
                <div>
                  <p className="text-xs font-medium uppercase tracking-wider text-accent-strong">Sponsor dashboard</p>
                  <h1 className="text-2xl font-semibold tracking-tight text-fg">{d.org.name}</h1>
                  {d.org.is_demo ? <DemoBadge className="mt-1" /> : null}
                </div>
              </div>
              <LinkButton href={`/orgs/${slug}/admin`} variant="secondary">Organization admin</LinkButton>
            </header>

            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="Sponsored events" value={formatNumber(d.totals.events)} icon={<Handshake className="h-4 w-4" />} />
              <Stat label="Participants" value={formatNumber(d.totals.participants)} icon={<Users className="h-4 w-4" />} />
              <Stat label="Submissions" value={formatNumber(d.totals.submissions)} icon={<Send className="h-4 w-4" />} />
            </div>

            <Card>
              <CardHeader title="Sponsored competitions" />
              {d.sponsored.length ? (
                <div className="p-4">
                  <Table>
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
                          <TD><Link href={`/competitions/${c.slug}`} className="font-medium text-fg hover:text-accent-strong">{c.title}</Link></TD>
                          <TD><Badge tone="outline">{titleCase(c.tier)}</Badge></TD>
                          <TD><StatusBadge status={c.status} /></TD>
                          <TD className="text-right tabular-nums">{formatNumber(c.participants)}</TD>
                          <TD className="text-right tabular-nums">{formatNumber(c.teams)}</TD>
                          <TD className="text-right tabular-nums">{formatNumber(c.submissions)}</TD>
                          <TD className="text-right tabular-nums">{compactNumber(c.views)}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </div>
              ) : (
                <CardBody>
                  <EmptyState
                    icon={<Handshake className="h-5 w-5" />}
                    title="No sponsored competitions yet"
                    description="Competition organizers add sponsors from their competition settings. Once you sponsor an event, its engagement appears here."
                  />
                </CardBody>
              )}
            </Card>

            <Card>
              <CardHeader title={<span className="inline-flex items-center gap-2"><Sparkles className="h-4 w-4 text-accent-strong" aria-hidden />Talent</span>} description="Participants of your sponsored events who opted in." />
              <CardBody className="space-y-4">
                <p className="flex items-start gap-2 rounded-[var(--radius-md)] bg-surface-2 px-3 py-2 text-xs text-muted">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden /> {d.talent_note}
                </p>
                {d.talent.length ? (
                  <ul className="grid gap-3 md:grid-cols-2">
                    {d.talent.map((t) => (
                      <li key={t.user.id} className="rounded-[var(--radius-md)] border border-border p-4">
                        <div className="flex items-start justify-between gap-3">
                          <UserLink user={t.user} size={32} />
                          <span className="shrink-0 text-xs text-muted">
                            {t.best_rank ? <>Best rank <span className="font-semibold text-fg">#{t.best_rank}</span></> : "Unranked"}
                          </span>
                        </div>
                        {t.headline ? <p className="mt-2 text-sm text-muted">{t.headline}</p> : null}
                        {t.skills.length ? (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {t.skills.map((s) => <span key={s} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">{s}</span>)}
                          </div>
                        ) : null}
                        <div className="mt-3 flex items-center justify-between text-xs text-subtle">
                          <span>{t.sponsored_events} sponsored event{t.sponsored_events === 1 ? "" : "s"}</span>
                          <Link href={`/u/${t.user.handle}`} className="font-medium text-accent-strong hover:underline">View public profile</Link>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="py-6 text-center text-sm text-subtle">No opted-in participants with results yet.</p>
                )}
              </CardBody>
            </Card>
          </div>
        )}
      </QueryState>
    </Container>
  );
}
