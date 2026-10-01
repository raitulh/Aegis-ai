"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, EyeOff, Medal, RefreshCw, Trophy } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { BadgeIcon } from "@/components/profile/badge-icon";
import { MiniSwitch } from "@/components/profile/mini-switch";
import type { MyBadgeAward, MyCertificate, PublicProfile } from "@/components/profile/types";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { RankBadge } from "@/components/ui/extras";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { errorMessage, get, post } from "@/lib/api";
import { formatDate, formatNumber, formatScore } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Message } from "@/lib/types";
import { HitArea, SettingsPageHeading, SettingsSection } from "../_components/settings-ui";

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
    <li className="flex items-center gap-3.5 px-5 py-3.5 transition-colors duration-150 hover:bg-surface-2/40 sm:px-6">
      {icon}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        {meta ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{meta}</p> : null}
        {badges ? <div className="mt-1.5 flex flex-wrap gap-1.5">{badges}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-1 text-xs text-muted">{control}</div>
    </li>
  );
}

/** Icon chip for list rows (decorative). */
function RowIcon({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "accent" }) {
  return (
    <span
      aria-hidden
      className={
        tone === "accent"
          ? "flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-[color-mix(in_oklab,var(--accent)_30%,transparent)] bg-accent-soft text-accent-strong [&_svg]:h-4 [&_svg]:w-4"
          : "flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-border bg-surface-2 text-muted [&_svg]:h-4 [&_svg]:w-4"
      }
    >
      {children}
    </span>
  );
}

/**
 * Counts derived from the lists this page already loads ("—" until they arrive). When the privacy settings hide a
 * whole section (`sectionHidden`), nothing from it is public, so the tile says so instead of a per-item count.
 */
function Summary({ items }: { items: { label: string; icon: ReactNode; total?: number; shown?: number; sectionHidden?: boolean }[] }) {
  return (
    <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card sm:grid-cols-3">
      {items.map((it) => (
        <div key={it.label} className="flex items-center justify-between gap-3 bg-surface px-5 py-4 sm:block">
          <dt className="flex items-center gap-1.5 text-eyebrow text-subtle [&_svg]:h-3.5 [&_svg]:w-3.5">{it.icon}{it.label}</dt>
          <dd className="text-right sm:mt-2 sm:text-left">
            <span className="tabular block text-2xl font-semibold leading-none tracking-[-0.03em] text-fg">{it.total ?? "—"}</span>
            {it.total !== undefined && it.sectionHidden ? (
              <span className="mt-1.5 flex items-center justify-end gap-1 whitespace-nowrap text-xs text-warning sm:justify-start">
                <EyeOff className="h-3 w-3 shrink-0" aria-hidden />
                Hidden by privacy<span className="sr-only"> settings, so none are shown on your profile</span>
              </span>
            ) : (
              <span className="tabular mt-1.5 block text-xs text-subtle">
                {it.total !== undefined && it.shown !== undefined ? `${it.shown} shown on profile` : "\u00a0"}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function VisibilityControl({ shown, label, onChange, disabled }: { shown: boolean; label: string; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <>
      <span className={shown ? "hidden w-12 text-right text-fg/80 sm:inline" : "hidden w-12 text-right text-subtle sm:inline"} aria-hidden>{shown ? "Shown" : "Hidden"}</span>
      <HitArea>
        <MiniSwitch checked={shown} label={`Show ${label} on my profile`} onChange={onChange} disabled={disabled} />
      </HitArea>
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
    <div className="space-y-10">
      <SettingsPageHeading
        icon={<Medal />}
        title="Achievements"
        description={
          <>
            Choose which achievements appear on your{" "}
            <Link href={`/u/${handle}`} className="text-accent-strong hover:underline">public profile</Link>. Hidden items stay publicly verifiable by their ID
            and remain on competition leaderboards.
          </>
        }
      />

      <Summary
        items={[
          {
            label: "Certificates",
            icon: <Award aria-hidden />,
            total: certs.data?.length,
            shown: certs.data?.filter((c) => c.status === "valid" && !c.hidden_on_profile).length,
            sectionHidden: privacy.show_certificates === false,
          },
          {
            label: "Badges",
            icon: <Medal aria-hidden />,
            total: badges.data?.length,
            shown: badges.data?.filter((a) => !a.hidden_on_profile).length,
            sectionHidden: privacy.show_badges === false,
          },
          {
            label: "Results",
            icon: <Trophy aria-hidden />,
            total: profile.data?.results.length,
            shown: profile.data?.results.filter((r) => !r.hidden).length,
          },
        ]}
      />

      <SettingsSection title="Certificates" description="Verifiable certificates issued to you." flush>
        {privacy.show_certificates === false ? (
          <div className="p-4 sm:p-5">
            <InlineNotice tone="warning" action={<LinkButton href="/settings/privacy" size="sm" variant="secondary">Privacy settings</LinkButton>}>
              Your certificates section is hidden entirely by your privacy settings.
            </InlineNotice>
          </div>
        ) : null}
        {certs.isPending ? (
          <div className="p-4 sm:p-5"><SkeletonRows rows={3} /></div>
        ) : certs.isError ? (
          <div className="p-4 sm:p-5"><ErrorState error={certs.error} onRetry={() => certs.refetch()} /></div>
        ) : !certs.data.length ? (
          <div className="p-4 sm:p-5"><EmptyState icon={<Award />} title="No certificates yet" description="Finish a course or place in a competition that issues certificates." action={<LinkButton href="/learn" variant="secondary">Browse courses</LinkButton>} /></div>
        ) : (
          <ul className="divide-y divide-border">
            {certs.data.map((c) => {
              const revoked = c.status !== "valid";
              return (
                <Row
                  key={c.public_id}
                  icon={<RowIcon tone={revoked ? "neutral" : "accent"}><Award /></RowIcon>}
                  title={<Link href={`/verify/${c.public_id}`} className="hover:text-accent-strong">{c.result_label} · {c.event_title}</Link>}
                  meta={<>{c.issuer_name} · <span className="tabular">{formatDate(c.issued_at)}</span> · <span className="font-mono">{c.public_id}</span></>}
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
      </SettingsSection>

      <SettingsSection
        title="Badges"
        description="Automatic badges are awarded when criteria are met; organizers can also award badges manually."
        action={<Button variant="secondary" size="sm" icon={<RefreshCw className="h-4 w-4" />} loading={checking} onClick={recheck}>Re-check badges</Button>}
        flush
      >
        {privacy.show_badges === false ? (
          <div className="p-4 sm:p-5">
            <InlineNotice tone="warning" action={<LinkButton href="/settings/privacy" size="sm" variant="secondary">Privacy settings</LinkButton>}>
              Your badges section is hidden entirely by your privacy settings.
            </InlineNotice>
          </div>
        ) : null}
        {badges.isPending ? (
          <div className="p-4 sm:p-5"><SkeletonRows rows={3} /></div>
        ) : badges.isError ? (
          <div className="p-4 sm:p-5"><ErrorState error={badges.error} onRetry={() => badges.refetch()} /></div>
        ) : !badges.data.length ? (
          <div className="p-4 sm:p-5"><EmptyState icon={<Medal />} title="No badges yet" description="Make your first submission, finish a course or get a pull request merged to start earning badges." action={<LinkButton href="/competitions" variant="secondary">Find a competition</LinkButton>} /></div>
        ) : (
          <ul className="divide-y divide-border">
            {badges.data.map((a) => (
              <Row
                key={a.id}
                icon={<BadgeIcon icon={a.badge.icon} color={a.badge.color} size={36} />}
                title={<Link href={`/badges/${a.public_id}`} className="hover:text-accent-strong">{a.badge.name}</Link>}
                meta={<>{a.badge.description} · <span className="tabular">{formatDate(a.awarded_at)}</span></>}
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
      </SettingsSection>

      <SettingsSection title="Competition results" description="Final results from finalized leaderboards." flush>
        {profile.isPending ? (
          <div className="p-4 sm:p-5"><SkeletonRows rows={3} /></div>
        ) : profile.isError ? (
          <div className="p-4 sm:p-5"><ErrorState error={profile.error} onRetry={() => profile.refetch()} /></div>
        ) : !profile.data.results.length ? (
          <div className="p-4 sm:p-5"><EmptyState icon={<Trophy />} title="No final results yet" description="Results appear after a competition you joined is finalized." action={<LinkButton href="/competitions" variant="secondary">Browse competitions</LinkButton>} /></div>
        ) : (
          <ul className="divide-y divide-border">
            {profile.data.results.map((r) => (
              <Row
                key={r.id}
                icon={r.rank ? <RankBadge rank={r.rank} size="lg" /> : <RowIcon><Trophy /></RowIcon>}
                title={<Link href={`/competitions/${r.competition.slug}`} className="hover:text-accent-strong">{r.competition.title}</Link>}
                meta={
                  <>
                    <span className="tabular">{r.rank ? `Rank ${r.rank} of ${formatNumber(r.total_ranked)}` : "Unranked"}</span>
                    {r.score !== null ? <> · score <span className="tabular font-mono">{formatScore(r.score)}</span></> : null}
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
      </SettingsSection>
    </div>
  );
}
