"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, CreditCard, Plus, RotateCcw, Search, University } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";

import { OrgCard } from "@/components/domain/cards";
import { Reveal } from "@/components/motion/reveal";
import { ORG_TYPES } from "@/components/orgs/org-ui";
import { Button, LinkButton } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useDebounced, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { OrgCard as OrgCardT, Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 24;

function OrgList() {
  const me = useMe().data;
  const [state, setState, reset] = useUrlState({ q: "", type: "", verified: "", page: "1" });
  const [q, setQ] = useState(state.q ?? "");
  const debounced = useDebounced(q, 300);
  useEffect(() => {
    if (debounced !== (state.q ?? "")) setState({ q: debounced });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const page = Math.max(1, Number(state.page) || 1);
  const filters = { q: state.q || undefined, type: state.type || undefined, verified: state.verified || undefined, page, page_size: PAGE_SIZE };
  const orgs = useQuery({ queryKey: qk.orgs(filters), queryFn: () => get<Page<OrgCardT>>("/orgs", filters) });
  const filtered = Boolean(state.q || state.type || state.verified);
  const typeLabel = ORG_TYPES.find((t) => t.value === state.type)?.label;

  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Community"
        icon={<University />}
        title="Universities, clubs & sponsors"
        description="Organizations host competitions, run courses and verify their members. Verified organizations were reviewed by the platform team."
        meta={
          <span className="inline-flex items-center gap-1.5">
            <BadgeCheck className="h-3.5 w-3.5 text-success" aria-hidden />
            Verification is granted by the platform team, never self-declared.
          </span>
        }
        actions={
          <>
            {me ? <LinkButton href="/orgs/mine" variant="secondary">My organizations</LinkButton> : null}
            <LinkButton href={me ? "/orgs/new" : "/login?next=/orgs/new"} icon={<Plus className="h-4 w-4" />}>Create organization</LinkButton>
          </>
        }
      />

      <section aria-label="Organizations">
        <div
          className="sticky top-14 z-20 -mx-4 mb-6 border-y border-border surface-glass px-4 py-3 sm:mx-0 sm:rounded-[var(--radius-lg)] sm:border sm:px-3 lg:top-[4.75rem]"
          role="search"
          aria-label="Filter organizations"
        >
          <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
              <Input
                aria-label="Search organizations"
                placeholder="Search by name, tagline or city"
                className="pl-9"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                maxLength={80}
              />
            </div>
            <div className="grid grid-cols-2 gap-2.5 sm:flex sm:shrink-0">
              <Select aria-label="Organization type" className="sm:w-44" value={state.type ?? ""} onChange={(e) => setState({ type: e.target.value })}>
                <option value="">All types</option>
                {ORG_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </Select>
              <Select aria-label="Verification" className="sm:w-44" value={state.verified ?? ""} onChange={(e) => setState({ verified: e.target.value })}>
                <option value="">Any verification</option>
                <option value="true">Verified only</option>
                <option value="false">Not verified</option>
              </Select>
            </div>
          </div>
        </div>

        {orgs.isPending ? (
          <SkeletonCards count={9} media={false} />
        ) : orgs.isError ? (
          <ErrorState error={orgs.error} onRetry={() => orgs.refetch()} />
        ) : orgs.data.items.length === 0 ? (
          filtered ? (
            <NoResults onReset={() => { setQ(""); reset(); }} />
          ) : (
            <EmptyState
              icon={<University />}
              title="No organizations yet"
              description="Create the first one for your university, club or community."
              action={<LinkButton href={me ? "/orgs/new" : "/login?next=/orgs/new"}>Create organization</LinkButton>}
            />
          )
        ) : (
          <>
            <div className="mb-4 flex min-h-8 flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <p className="text-sm text-muted" aria-live="polite">
                <span className="tabular font-medium text-fg">{formatNumber(orgs.data.total)}</span> organization{orgs.data.total === 1 ? "" : "s"}
                {typeLabel ? <span className="text-subtle"> · {typeLabel}</span> : null}
                {state.verified === "true" ? <span className="text-subtle"> · verified only</span> : state.verified === "false" ? <span className="text-subtle"> · not verified</span> : null}
                {state.q ? <span className="text-subtle"> · matching “{state.q}”</span> : null}
              </p>
              {filtered ? (
                <Button variant="ghost" size="sm" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => { setQ(""); reset(); }}>
                  Clear filters
                </Button>
              ) : null}
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {orgs.data.items.map((o) => (
                <div key={o.id} className="flex min-w-0 [&>*]:flex-1">
                  <OrgCard o={o} />
                </div>
              ))}
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={orgs.data.total} onPage={(p) => setState({ page: String(p) })} />
          </>
        )}
      </section>

      <Reveal className="mt-14">
        <div className="relative isolate overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-6 shadow-card sm:p-8">
          <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_right,black,transparent_65%)]" />
          <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 -z-10 h-64 w-96 max-w-full" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
          <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <p className="text-eyebrow text-accent-strong">For organizers</p>
              <h2 className="mt-2 text-xl font-semibold tracking-[-0.02em] text-fg">Run a university, club or community?</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted">
                Create an organization to host competitions, verify members and issue certificates. It starts on the free plan.
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <LinkButton href={me ? "/orgs/new" : "/login?next=/orgs/new"} icon={<Plus className="h-4 w-4" />}>Create organization</LinkButton>
              <LinkButton href="/pricing" variant="secondary" icon={<CreditCard className="h-4 w-4" />}>Compare plans</LinkButton>
            </div>
          </div>
          {me ? (
            <Link href="/orgs/mine" className="mt-6 inline-flex items-center gap-1.5 text-sm font-medium text-accent-strong hover:underline">
              Manage your memberships <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : null}
        </div>
      </Reveal>
    </Container>
  );
}

export default function OrgsPage() {
  return (
    <Suspense fallback={<Container><SkeletonCards count={9} media={false} className="mt-8" /></Container>}>
      <OrgList />
    </Suspense>
  );
}
