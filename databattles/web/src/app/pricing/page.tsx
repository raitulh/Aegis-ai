"use client";

import { useQuery } from "@tanstack/react-query";
import { CreditCard, ShieldCheck } from "lucide-react";

import { EntitlementList, PlanPrice } from "@/components/orgs/org-ui";
import type { PlanInfo } from "@/components/orgs/types";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader, Section } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
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
    <Container>
      <PageHeader
        eyebrow="Plans"
        title="Simple plans for organizations"
        description="Free for students, always. Organizations pick a plan for private events, custom certificates, analytics and sponsor tools."
      />

      {provider.data ? (
        <div className="mb-8">
          <InlineNotice tone="info" title={<span className="inline-flex items-center gap-1.5"><CreditCard className="h-4 w-4" aria-hidden />{provider.data.checkout_available ? "Online checkout" : "Manual billing"}</span>}>
            {provider.data.message}
          </InlineNotice>
        </div>
      ) : null}

      {plans.isPending ? (
        <SkeletonCards count={3} />
      ) : plans.isError ? (
        <ErrorState error={plans.error} onRetry={() => plans.refetch()} />
      ) : plans.data.length === 0 ? (
        <EmptyState title="No plans published" description="The platform team hasn’t published any plans yet." />
      ) : (
        <div className={cn("grid gap-4", plans.data.length >= 3 ? "lg:grid-cols-3" : "md:grid-cols-2")}>
          {plans.data.map((p) => {
            return (
              <article
                key={p.key}
                aria-labelledby={`plan-${p.key}`}
                className="flex flex-col rounded-[var(--radius-xl)] border border-border bg-surface p-6"
              >
                <h2 id={`plan-${p.key}`} className="text-lg font-semibold text-fg">{p.name}</h2>
                <div className="mt-3"><PlanPrice cents={p.price_cents_monthly} /></div>
                {p.description ? <p className="mt-3 text-sm text-muted">{p.description}</p> : null}
                <EntitlementList entitlements={p.entitlements} className="mt-6 flex-1" />
                <div className="mt-6">
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
      )}

      <p className="mt-4 flex items-center gap-1.5 text-xs text-subtle">
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> Prices are per organization per month. No card details are collected by this app.
      </p>

      <Section title="Questions">
        <dl className="grid gap-4 md:grid-cols-2">
          {FAQ.map((f) => (
            <div key={f.q} className="rounded-[var(--radius-lg)] border border-border bg-surface p-5">
              <dt className="font-medium text-fg">{f.q}</dt>
              <dd className="mt-1.5 text-sm text-muted">{f.a}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </Container>
  );
}
