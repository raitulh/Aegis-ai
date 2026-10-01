"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BookMarked, ChevronRight } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { FilterBar, FilterSelect, ResultSummary, SearchBox } from "@/components/catalog/filters";
import { RegisterRepoDialog, RepoCardView, osKeys } from "@/components/catalog/repo";
import type { RepoCard } from "@/components/catalog/types";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 24;
const DEFAULTS = { q: "", language: "", sort: "stars", page: "1" };

function ReposList() {
  const [f, setF, reset] = useUrlState(DEFAULTS);
  const me = useMe();
  const signedIn = Boolean(me.data);
  const page = Math.max(1, Number(f.page) || 1);
  const query = { q: f.q || undefined, language: f.language || undefined, sort: f.sort, page, page_size: PAGE_SIZE };
  const list = useQuery({
    queryKey: osKeys.repos(query),
    queryFn: ({ signal }) => get<Page<RepoCard>>("/opensource/repos", query, signal),
    placeholderData: keepPreviousData,
  });
  const active = Boolean(f.q || f.language);

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 pt-6 text-sm text-subtle">
        <Link href="/open-source" className="rounded-sm transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">Open source</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <span className="text-muted" aria-current="page">Repositories</span>
      </nav>
      <PageHeader
        className="pt-4"
        eyebrow="Contribute"
        icon={<BookMarked />}
        title="Repositories"
        description="Public GitHub repositories registered with the hub. Stats refresh on webhooks and periodic syncs; the registrant, project maintainers and moderators can request a sync."
        actions={signedIn ? <RegisterRepoDialog /> : <LinkButton href="/login?next=%2Fopen-source%2Frepos" variant="secondary">Sign in to register</LinkButton>}
      />

      <FilterBar>
        <SearchBox className="sm:min-w-56 sm:flex-1" label="Search repositories" placeholder="Search by name or description" value={f.q} onCommit={(q) => setF({ q })} />
        <SearchBox className="sm:w-44" label="Filter by language" placeholder="Language" value={f.language} onCommit={(language) => setF({ language })} />
        <FilterSelect className="sm:w-44" label="Sort by" value={f.sort} onChange={(sort) => setF({ sort })}>
          <option value="stars">Most stars</option>
          <option value="updated">Recently pushed</option>
          <option value="name">Name (A–Z)</option>
        </FilterSelect>
      </FilterBar>

      {list.isPending ? (
        <SkeletonCards count={6} media={false} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        active ? (
          <NoResults onReset={reset} />
        ) : (
          <EmptyState
            icon={<BookMarked className="h-5 w-5" />}
            title="No repositories registered yet"
            description="Register a public GitHub repository to surface its issues and attribute merged pull requests."
            action={signedIn ? <RegisterRepoDialog /> : <LinkButton href="/login?next=%2Fopen-source%2Frepos">Sign in</LinkButton>}
          />
        )
      ) : (
        <div aria-busy={list.isFetching || undefined} className={list.isPlaceholderData ? "opacity-70 transition-opacity" : "transition-opacity"}>
          <ResultSummary total={list.data.total} noun="repo" active={active} onClear={reset} />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {list.data.items.map((r) => <RepoCardView key={r.id} repo={r} canSync={signedIn} />)}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function OpenSourceReposPage() {
  return (
    <Suspense fallback={<Container className="py-16"><SkeletonCards count={6} media={false} /></Container>}>
      <ReposList />
    </Suspense>
  );
}
