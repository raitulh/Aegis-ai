"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, BarChart3, Building2, Lock, Mail, University } from "lucide-react";
import Link from "next/link";

import { Reveal } from "@/components/motion/reveal";
import { DemoBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber, titleCase } from "@/lib/format";
import { useInView } from "@/lib/motion";
import { qk } from "@/lib/query";
import type { OrgCard, Page } from "@/lib/types";
import { SectionHeading } from "./section-heading";

const PARAMS = { page: 1, page_size: 6 };

const FEATURES = [
  { icon: Lock, title: "Public, members-only or private", text: "Host competitions for everyone, your members, or invited teams only." },
  { icon: Mail, title: "Institutional email verification", text: "Verify student membership by university email before members-only events." },
  { icon: BadgeCheck, title: "Verifiable certificates", text: "Issue certificates with checksummed IDs and public verification pages." },
  { icon: BarChart3, title: "Privacy-respecting analytics", text: "See participation and outcomes without exposing individual students." },
];

/** Universities & clubs on the platform (live from /orgs) next to what organizers get. */
export function OrganizationSection({ universities }: { universities?: number }) {
  const [ref, near] = useInView<HTMLElement>({ rootMargin: "400px 0px" });
  const orgs = useQuery({ queryKey: qk.orgs(PARAMS), queryFn: () => get<Page<OrgCard>>("/orgs", PARAMS), enabled: near, staleTime: 60_000 });
  const items = orgs.data?.items ?? [];

  return (
    <section ref={ref} className="relative py-20 sm:py-28" aria-label="Universities and organizers">
      <Container>
        <SectionHeading
          index="08"
          eyebrow="Connect"
          title="For universities and clubs."
          description="Host competitions, verify student membership, issue verifiable certificates and see privacy-respecting analytics — sponsors meet talent only when students opt in."
          action={
            <div className="flex flex-wrap gap-2">
              <LinkButton href="/organize" icon={<Building2 className="h-4 w-4" />}>Organizer tools</LinkButton>
              <LinkButton href="/pricing" variant="ghost">Plans</LinkButton>
            </div>
          }
        />

        <div className="mt-12 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
          <Reveal className="relative overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface p-5 shadow-card sm:p-6">
            <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]" />
            <div className="relative flex items-center justify-between">
              <p className="text-eyebrow text-subtle">On DataBattles</p>
              {universities !== undefined ? (
                <p className="tabular text-xs text-muted">
                  <span className="font-semibold text-fg">{compactNumber(universities)}</span> {universities === 1 ? "university" : "universities"}
                </p>
              ) : null}
            </div>
            {orgs.isPending ? (
              <div className="relative mt-5 grid gap-2.5">
                {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 w-full rounded-[var(--radius-lg)]" />)}
              </div>
            ) : items.length ? (
              <ul className="relative mt-5 grid gap-2.5">
                {items.map((o) => (
                  <li key={o.id}>
                    <Link href={`/orgs/${o.slug}`} className="group lift flex items-center gap-3 rounded-[var(--radius-lg)] border border-border bg-bg-elevated/70 p-3">
                      {o.logo_url ? (
                        <img src={o.logo_url} alt="" className="h-10 w-10 rounded-lg object-cover" />
                      ) : (
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-bold text-white" style={{ background: o.accent_color ?? "var(--accent)" }}>
                          {o.name[0]}
                        </span>
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{o.name}</span>
                          {o.verification_status === "verified" ? <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-success" aria-label="Verified" /> : null}
                        </span>
                        <span className="block truncate text-xs text-subtle">
                          {titleCase(o.type)}{o.city ? ` · ${o.city}` : ""} · {compactNumber(o.member_count)} members
                        </span>
                      </span>
                      {o.is_demo ? <DemoBadge className="hidden sm:inline-flex" /> : null}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="relative mt-6 text-sm text-muted">No organizations listed yet.</p>
            )}
            <Link href="/orgs" className="relative mt-5 inline-flex items-center gap-1.5 text-sm font-medium text-accent-strong hover:underline">
              <University className="h-4 w-4" aria-hidden /> Find your university <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </Reveal>

          <Reveal delay={80} as="ul" className="grid grid-cols-1 gap-3">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-3.5 rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-4 shadow-card">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-accent-soft text-accent-strong">
                  <f.icon className="h-4 w-4" aria-hidden />
                </span>
                <span>
                  <span className="block text-sm font-medium text-fg">{f.title}</span>
                  <span className="mt-0.5 block text-sm leading-relaxed text-muted">{f.text}</span>
                </span>
              </li>
            ))}
          </Reveal>
        </div>
      </Container>
    </section>
  );
}
