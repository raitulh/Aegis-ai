"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, Medal, RefreshCw, Trophy } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { BadgeIcon } from "@/components/profile/badge-icon";
import { MiniSwitch } from "@/components/profile/mini-switch";
import type { MyBadgeAward, MyCertificate, PublicProfile } from "@/components/profile/types";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { errorMessage, get, post } from "@/lib/api";
import { formatDate, formatNumber, formatScore } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Message } from "@/lib/types";

const CERTS_KEY = ["me", "certificates"] as const;
const BADGES_KEY = ["me", "badges"] as const;
type Kind = "certificate" | "badge" | "result";

function useVisibility(handle: string) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Set<string>>(new Set());
  const toggle = async (kind: Kind, itemId: string, hidden: boolean) => {
    const key = `${kind}:${itemId}`;
    setPending((s) => new Set(s).add(key));
    try {
      const m = await post<Message>("/me/achievements/visibility", { kind, item_id: itemId, hidden });
      toast.success(m.message);
      await Promise.all([
        qc.invalidateQueries({ queryKey: kind === "certificate" ? CERTS_KEY : kind === "badge" ? BADGES_KEY : qk.profile(handle) }),
        qc.invalidateQueries({ queryKey: qk.profile(handle) }),
      ]);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setPending((s) => {
        const n = new Set(s);
        n.delete(key);
        return n;
      });
    }
  };
  return { toggle, isPending: (kind: Kind, id: string) => pending.has(`${kind}:${id}`) };
}

function Row({ icon, title, meta, badges, control }: { icon: ReactNode; title: ReactNode; meta?: ReactNode; badges?: ReactNode; control: ReactNode }) {
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      {icon}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        {meta ? <p className="mt-0.5 text-xs text-muted">{meta}</p> : null}
        {badges ? <div className="mt-1 flex flex-wrap gap-1.5">{badges}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-xs text-muted">{control}</div>
    </li>
  );
}

function VisibilityControl({ shown, label, onChange, disabled }: { shown: boolean; label: string; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <>
      <span className="hidden sm:inline" aria-hidden>{shown ? "Shown" : "Hidden"}</span>
      <MiniSwitch checked={shown} label={`Show ${label} on my profile`} onChange={onChange} disabled={disabled} />
    </>
  );
}

export default function AchievementsSettingsPage() {
  const me = useRequireAuth();
  const handle = me.data?.handle ?? "";
  const qc = useQueryClient();
  const enabled = Boolean(me.data);
  const certs = useQuery({ queryKey: CERTS_KEY, queryFn: () => get<MyCertificate[]>("/me/certificates"), enabled });
  const badges = useQuery({ queryKey: BADGES_KEY, queryFn: () => get<MyBadgeAward[]>("/me/badges"), enabled });
  const profile = useQuery({ queryKey: qk.profile(handle), queryFn: () => get<PublicProfile>(`/users/${encodeURIComponent(handle)}`), enabled });
  const vis = useVisibility(handle);
  const [checking, setChecking] = useState(false);
  const privacy = me.data?.privacy ?? {};

  const recheck = async () => {
    setChecking(true);
    try {
      const awarded = await post<string[]>("/me/badges/check");
      if (awarded.length) toast.success(`You earned ${awarded.length} new ${awarded.length === 1 ? "badge" : "badges"}!`);
      else toast.info("No new badges yet — you're up to date.");
      await Promise.all([
        qc.invalidateQueries({ queryKey: BADGES_KEY }),
        qc.invalidateQueries({ queryKey: qk.profile(handle) }),
        qc.invalidateQueries({ queryKey: qk.dashboard }),
      ]);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted">
        Choose which achievements appear on your{" "}
        <Link href={`/u/${handle}`} className="text-accent-strong hover:underline">public profile</Link>. Hidden items stay publicly verifiable by their ID
        and remain on competition leaderboards.
      </p>

      <Card>
        <CardHeader title="Certificates" description="Verifiable certificates issued to you." />
        <CardBody className="space-y-4">
          {privacy.show_certificates === false ? (
            <InlineNotice tone="warning" action={<LinkButton href="/settings/privacy" size="sm" variant="secondary">Privacy settings</LinkButton>}>
              Your certificates section is hidden entirely by your privacy settings.
            </InlineNotice>
          ) : null}
          {certs.isPending ? (
            <SkeletonRows rows={3} />
          ) : certs.isError ? (
            <ErrorState error={certs.error} onRetry={() => certs.refetch()} />
          ) : !certs.data.length ? (
            <EmptyState icon={<Award className="h-5 w-5" />} title="No certificates yet" description="Finish a course or place in a competition that issues certificates." action={<LinkButton href="/learn" variant="secondary">Browse courses</LinkButton>} />
          ) : (
            <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
              {certs.data.map((c) => {
                const revoked = c.status !== "valid";
                return (
                  <Row
                    key={c.public_id}
                    icon={<Award className="h-5 w-5 shrink-0 text-accent-strong" aria-hidden />}
                    title={<Link href={`/verify/${c.public_id}`} className="hover:text-accent-strong">{c.result_label} · {c.event_title}</Link>}
                    meta={<>{c.issuer_name} · {formatDate(c.issued_at)} · <span className="font-mono">{c.public_id}</span></>}
                    badges={
                      revoked || c.is_demo ? (
                        <>
                          {revoked ? <StatusBadge status={c.status} /> : null}
                          {c.is_demo ? <DemoBadge /> : null}
                        </>
                      ) : undefined
                    }
                    control={
                      revoked ? (
                        <span className="text-xs text-subtle">Never shown</span>
                      ) : (
                        <VisibilityControl
                          shown={!c.hidden_on_profile}
                          label={c.event_title}
                          disabled={vis.isPending("certificate", c.public_id)}
                          onChange={(v) => vis.toggle("certificate", c.public_id, !v)}
                        />
                      )
                    }
                  />
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Badges"
          description="Automatic badges are awarded when criteria are met; organizers can also award badges manually."
          action={<Button variant="secondary" size="sm" icon={<RefreshCw className="h-4 w-4" />} loading={checking} onClick={recheck}>Re-check badges</Button>}
        />
        <CardBody className="space-y-4">
          {privacy.show_badges === false ? (
            <InlineNotice tone="warning" action={<LinkButton href="/settings/privacy" size="sm" variant="secondary">Privacy settings</LinkButton>}>
              Your badges section is hidden entirely by your privacy settings.
            </InlineNotice>
          ) : null}
          {badges.isPending ? (
            <SkeletonRows rows={3} />
          ) : badges.isError ? (
            <ErrorState error={badges.error} onRetry={() => badges.refetch()} />
          ) : !badges.data.length ? (
            <EmptyState icon={<Medal className="h-5 w-5" />} title="No badges yet" description="Make your first submission, finish a course or get a pull request merged to start earning badges." action={<LinkButton href="/competitions" variant="secondary">Find a competition</LinkButton>} />
          ) : (
            <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
              {badges.data.map((a) => (
                <Row
                  key={a.id}
                  icon={<BadgeIcon icon={a.badge.icon} color={a.badge.color} size={36} />}
                  title={<Link href={`/badges/${a.public_id}`} className="hover:text-accent-strong">{a.badge.name}</Link>}
                  meta={<>{a.badge.description} · {formatDate(a.awarded_at)}</>}
                  badges={
                    <>
                      <Badge tone="outline">{a.manual ? "Awarded by organizer" : "Automatic"}</Badge>
                      {a.badge.rarity_label ? <Badge>{a.badge.rarity_label}</Badge> : null}
                    </>
                  }
                  control={
                    <VisibilityControl
                      shown={!a.hidden_on_profile}
                      label={a.badge.name}
                      disabled={vis.isPending("badge", a.id)}
                      onChange={(v) => vis.toggle("badge", a.id, !v)}
                    />
                  }
                />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Competition results" description="Final results from finalized leaderboards." />
        <CardBody>
          {profile.isPending ? (
            <SkeletonRows rows={3} />
          ) : profile.isError ? (
            <ErrorState error={profile.error} onRetry={() => profile.refetch()} />
          ) : !profile.data.results.length ? (
            <EmptyState icon={<Trophy className="h-5 w-5" />} title="No final results yet" description="Results appear after a competition you joined is finalized." action={<LinkButton href="/competitions" variant="secondary">Browse competitions</LinkButton>} />
          ) : (
            <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
              {profile.data.results.map((r) => (
                <Row
                  key={r.id}
                  icon={<Trophy className="h-5 w-5 shrink-0 text-subtle" aria-hidden />}
                  title={<Link href={`/competitions/${r.competition.slug}`} className="hover:text-accent-strong">{r.competition.title}</Link>}
                  meta={
                    <>
                      {r.rank ? `Rank ${r.rank} of ${formatNumber(r.total_ranked)}` : "Unranked"}
                      {r.score !== null ? <> · score <span className="font-mono">{formatScore(r.score)}</span></> : null}
                    </>
                  }
                  badges={r.label ? <Badge tone="accent">{r.label}</Badge> : undefined}
                  control={
                    <VisibilityControl
                      shown={!r.hidden}
                      label={`result in ${r.competition.title}`}
                      disabled={vis.isPending("result", r.id)}
                      onChange={(v) => vis.toggle("result", r.id, !v)}
                    />
                  }
                />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
