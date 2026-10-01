"use client";

import { useQuery } from "@tanstack/react-query";
import { CreditCard, GraduationCap, MoveHorizontal, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Reveal } from "@/components/motion/reveal";
import { EntitlementList, EntitlementValue, PlanPrice, entitlementKeys, entitlementLabel } from "@/components/orgs/org-ui";
import type { PlanInfo } from "@/components/orgs/types";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader, Section } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonCards } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";
import { useMe } from "@/lib/hooks";

interface Provider {
  provider: string;
  checkout_available: boolean;
  message: string;
}

const FAQ = [
  {
    q: "Do students pay anything?",
    a: "No. Plans apply to organizations (universities, clubs, sponsors). Learners can join competitions, take courses and earn verifiable credentials for free.",
  },
  {
    q: "How do I upgrade?",
    a: "Create your organization first — it starts on the free plan. An owner or admin can see the current plan under the organization’s admin area. Plan changes are processed by the platform team.",
  },
  {
    q: "Do you store card details?",
    a: "No. This deployment has no payment processor connected and never asks for card details. Invoicing is handled outside the app.",
  },
  {
    q: "What happens if we downgrade?",
    a: "Existing competitions, results and certificates are kept. Plan limits (such as the number of active competitions) apply to new activity.",
  },
];

export default function PricingPage() {
  const me = useMe().data;
  const plans = useQuery({ queryKey: ["billing", "plans"], queryFn: () => get<PlanInfo[]>("/billing/plans"), staleTime: 5 * 60_000 });
  const provider = useQuery({ queryKey: ["billing", "provider"], queryFn: () => get<Provider>("/billing/provider"), staleTime: 5 * 60_000 });
  const managesOrg = Boolean(me?.memberships.some((m) => (m.role === "owner" || m.role === "admin") && m.status === "active"));

  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Plans"
        icon={<CreditCard />}
        title={<>Simple plans for <span className="text-gradient">organizations</span></>}
        description="Free for students, always. Organizations pick a plan for private events, custom certificates, analytics and sponsor tools."
        meta={
          <span className="inline-flex items-center gap-1.5">
            <GraduationCap className="h-3.5 w-3.5 text-accent-strong" aria-hidden />
            Learners never pay — plans apply to organizations only.
          </span>
        }
      />

      {provider.data ? (
        <div
          role="status"
          className="mb-8 flex flex-col gap-2 rounded-[var(--radius-lg)] border border-border bg-surface/70 px-4 py-3 text-sm sm:flex-row sm:items-center sm:gap-4"
        >
          <span className="inline-flex shrink-0 items-center gap-2 font-medium text-fg">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-info-soft text-info">
              <CreditCard className="h-3.5 w-3.5" aria-hidden />
            </span>
            {provider.data.checkout_available ? "Online checkout" : "Manual billing"}
          </span>
          <span aria-hidden className="hidden h-4 w-px bg-border-strong sm:block" />
          <span className="text-muted">{provider.data.message}</span>
        </div>
      ) : null}

      {plans.isPending ? (
        <SkeletonCards count={3} media={false} />
      ) : plans.isError ? (
        <ErrorState error={plans.error} onRetry={() => plans.refetch()} />
      ) : plans.data.length === 0 ? (
        <EmptyState icon={<CreditCard />} title="No plans published" description="The platform team hasn’t published any plans yet." />
      ) : (
        <>
          <div className={cn("grid grid-cols-1 gap-4", plans.data.length >= 3 ? "lg:grid-cols-3" : "md:grid-cols-2")}>
            {plans.data.map((p) => {
              const free = p.price_cents_monthly === 0;
              return (
                <article
                  key={p.key}
                  aria-labelledby={`plan-${p.key}`}
                  className={cn(
                    "relative isolate flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-xl)] bg-surface surface-sheen p-6 shadow-card sm:p-7",
                    free ? "border-gradient shadow-elevated" : "border border-border",
                  )}
                >
                  {free ? (
                    <div
                      aria-hidden
                      className="pointer-events-none absolute -top-24 left-1/2 -z-10 h-56 w-[28rem] max-w-full -translate-x-1/2"
                      style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
                    />
                  ) : null}
                  <div className="flex min-h-[22px] flex-wrap items-center justify-between gap-x-3 gap-y-2">
                    <h2 id={`plan-${p.key}`} className="text-eyebrow text-accent-strong">{p.name}</h2>
                    {free ? <Badge tone="accent" icon={<Sparkles className="h-3 w-3" aria-hidden />}>Every organization starts here</Badge> : null}
                  </div>
                  <div className="mt-5"><PlanPrice cents={p.price_cents_monthly} size="lg" /></div>
                  {p.description ? <p className="mt-3 min-h-[2.75rem] text-sm leading-relaxed text-muted">{p.description}</p> : null}
                  <div className="mt-6 border-t border-border pt-5">
                    <p className="mb-3 text-eyebrow text-subtle">Includes</p>
                    <EntitlementList entitlements={p.entitlements} className="flex-1" />
                  </div>
                  <div aria-hidden className="min-h-6 grow" />
                  <div>
                    {p.price_cents_monthly === 0 ? (
                      <LinkButton href={me ? "/orgs/new" : "/signup"} className="w-full">
                        {me ? "Create an organization" : "Get started free"}
                      </LinkButton>
                    ) : managesOrg ? (
                      <LinkButton href="/orgs/mine" variant="secondary" className="w-full">See your organization’s plan</LinkButton>
                    ) : (
                      <LinkButton href={me ? "/orgs/new" : "/signup"} variant="secondary" className="w-full">
                        Start free, upgrade later
                      </LinkButton>
                    )}
                  </div>
                </article>
              );
            })}
          </div>

          <p className="mt-4 flex items-center gap-1.5 text-xs text-subtle">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden /> Prices are per organization per month. No card details are collected by this app.
          </p>

          {plans.data.length > 1 ? (
            <Reveal>
              <Section eyebrow="Side by side" title="Compare plans" description="Every entitlement each plan includes, as published by the platform team." className="pt-14">
                <CompareScroller planCount={plans.data.length}>
                  <Table className="relative">
                    <caption className="sr-only">Plan comparison</caption>
                    <THead>
                      <tr>
                        <TH className="min-w-40 sm:min-w-48">Entitlement</TH>
                        {plans.data.map((p) => (
                          <TH key={p.key} className="min-w-32 text-center">
                            <span className="block text-[11px] text-fg">{p.name}</span>
                            <span className="mt-0.5 block normal-case tracking-normal text-subtle">
                              <PlanPriceInline cents={p.price_cents_monthly} />
                            </span>
                          </TH>
                        ))}
                      </tr>
                    </THead>
                    <TBody>
                      {entitlementKeys(plans.data).map((k) => (
                        <TR key={k}>
                          <TH scope="row" className="whitespace-normal text-left font-sans text-sm font-normal normal-case tracking-normal text-fg">
                            {entitlementLabel(k)}
                          </TH>
                          {plans.data.map((p) => (
                            <TD key={p.key} className="text-center">
                              <EntitlementValue value={p.entitlements[k]} />
                            </TD>
                          ))}
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </CompareScroller>
              </Section>
            </Reveal>
          ) : null}
        </>
      )}

      <Section eyebrow="FAQ" title="Questions" className="pt-14">
        <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border md:grid-cols-2">
          {FAQ.map((f) => (
            <div key={f.q} className="bg-surface p-5 sm:p-6">
              <dt className="font-medium tracking-[-0.01em] text-fg">{f.q}</dt>
              <dd className="mt-2 text-sm leading-relaxed text-muted">{f.a}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </Container>
  );
}

/** Compact price for table headers ("Free" or "$49.00/mo"), from the same cents value the cards show. */
/**
 * On narrow screens the comparison table scrolls sideways inside its container. When it actually overflows, show a
 * hint above it and fade the right edge until the last column is in view, so it's clear more plans sit off-screen.
 */
function CompareScroller({ planCount, children }: { planCount: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [moreRight, setMoreRight] = useState(false);
  useEffect(() => {
    const el = ref.current?.firstElementChild;
    if (!(el instanceof HTMLElement)) return;
    const update = () => {
      setOverflows(el.scrollWidth > el.clientWidth + 1);
      setMoreRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
    };
    // ResizeObserver reports once on observe, which sets the initial state.
    const ro = new ResizeObserver(update);
    ro.observe(el);
    el.addEventListener("scroll", update, { passive: true });
    return () => {
      ro.disconnect();
      el.removeEventListener("scroll", update);
    };
  }, []);
  return (
    <>
      {overflows ? (
        <p className="mb-2.5 flex items-center gap-1.5 text-xs text-subtle">
          <MoveHorizontal className="h-3.5 w-3.5 shrink-0" aria-hidden /> Scroll sideways to compare all {planCount} plans
        </p>
      ) : null}
      <div ref={ref} className="relative">
        {children}
        <div
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-y-px right-px w-14 rounded-r-[var(--radius-lg)] bg-gradient-to-l from-surface to-transparent transition-opacity duration-200",
            overflows && moreRight ? "opacity-100" : "opacity-0",
          )}
        />
      </div>
    </>
  );
}

function PlanPriceInline({ cents }: { cents: number }) {
  if (!cents) return <span>Free</span>;
  return <span className="tabular">{formatMoney(cents)}/mo</span>;
}
