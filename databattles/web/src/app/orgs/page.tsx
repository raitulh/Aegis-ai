"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Search, University } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { OrgCard } from "@/components/domain/cards";
import { ORG_TYPES } from "@/components/orgs/org-ui";
import { LinkButton } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
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

  return (
    <Container>
      <PageHeader
        eyebrow="Community"
        title="Universities, clubs & sponsors"
        description="Organizations host competitions, run courses and verify their members. Verified organizations were reviewed by the platform team."
        actions={
          <>
            {me ? <LinkButton href="/orgs/mine" variant="secondary">My organizations</LinkButton> : null}
            <LinkButton href={me ? "/orgs/new" : "/login?next=/orgs/new"} icon={<Plus className="h-4 w-4" />}>Create organization</LinkButton>
          </>
        }
      />

      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center" role="search">
        <div className="relative flex-1">
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

      {orgs.isPending ? (
        <SkeletonCards count={9} />
      ) : orgs.isError ? (
        <ErrorState error={orgs.error} onRetry={() => orgs.refetch()} />
      ) : orgs.data.items.length === 0 ? (
        filtered ? (
          <NoResults onReset={() => { setQ(""); reset(); }} />
        ) : (
          <EmptyState
            icon={<University className="h-5 w-5" />}
            title="No organizations yet"
            description="Create the first one for your university, club or community."
            action={<LinkButton href={me ? "/orgs/new" : "/login?next=/orgs/new"}>Create organization</LinkButton>}
          />
        )
      ) : (
        <>
          <p className="mb-3 text-sm text-muted" aria-live="polite">{orgs.data.total} organization{orgs.data.total === 1 ? "" : "s"}</p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {orgs.data.items.map((o) => <OrgCard key={o.id} o={o} />)}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={orgs.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </Container>
  );
}

export default function OrgsPage() {
  return (
    <Suspense fallback={<Container><SkeletonCards count={9} className="mt-8" /></Container>}>
      <OrgList />
    </Suspense>
  );
}
