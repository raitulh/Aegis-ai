"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Database, Plus, User } from "lucide-react";
import { Suspense } from "react";

import { FilterBar, FilterSelect, ResultSummary, SearchBox, ToggleChip } from "@/components/catalog/filters";
import { DatasetCard } from "@/components/domain/cards";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { DatasetCard as DatasetCardT, Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 24;
const DEFAULTS = { q: "", tag: "", license: "", sort: "updated", owner: "", page: "1" };

function DatasetsList() {
  const [f, setF, reset] = useUrlState(DEFAULTS);
  const me = useMe();
  const signedIn = Boolean(me.data);
  const page = Math.max(1, Number(f.page) || 1);
  const mine = f.owner === "me" && signedIn;

  const licenses = useQuery({ queryKey: ["datasets", "licenses"], queryFn: () => get<Record<string, string>>("/datasets/licenses"), staleTime: 10 * 60_000 });
  const filters = { q: f.q, tag: f.tag, license: f.license, sort: f.sort, owner: mine ? "me" : "", page };
  const list = useQuery({
    queryKey: qk.datasets(filters),
    queryFn: ({ signal }) =>
      get<Page<DatasetCardT>>("/datasets", { q: f.q || undefined, tag: f.tag || undefined, license: f.license || undefined, sort: f.sort, owner: mine ? "me" : undefined, page, page_size: PAGE_SIZE }, signal),
    placeholderData: keepPreviousData,
    enabled: f.owner !== "me" || me.isSuccess,
  });
  const active = Boolean(f.q || f.tag || f.license || mine);

  return (
    <Container>
      <PageHeader
        eyebrow="Dataset hub"
        title="Datasets"
        description="Versioned, licensed datasets for competitions, courses and your own projects. Every file has a checksum and a preview."
        actions={<LinkButton href="/datasets/new" icon={<Plus className="h-4 w-4" aria-hidden />}>Publish a dataset</LinkButton>}
      />

      <FilterBar>
        <SearchBox className="sm:flex-1 sm:min-w-56" label="Search datasets" placeholder="Search by title or subtitle" value={f.q} onCommit={(q) => setF({ q })} />
        <SearchBox className="sm:w-44" label="Filter by tag" placeholder="Tag (e.g. nlp)" value={f.tag} onCommit={(tag) => setF({ tag: tag.toLowerCase() })} />
        <FilterSelect className="sm:w-56" label="License" value={f.license} onChange={(license) => setF({ license })}>
          <option value="">Any license</option>
          {Object.entries(licenses.data ?? {}).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
        </FilterSelect>
        <FilterSelect className="sm:w-44" label="Sort by" value={f.sort} onChange={(sort) => setF({ sort })}>
          <option value="updated">Recently updated</option>
          <option value="downloads">Most downloaded</option>
          <option value="title">Title (A–Z)</option>
        </FilterSelect>
        {signedIn ? (
          <ToggleChip pressed={mine} onChange={(on) => setF({ owner: on ? "me" : "" })} icon={<User className="h-4 w-4" aria-hidden />}>
            My datasets
          </ToggleChip>
        ) : null}
      </FilterBar>

      {mine ? (
        <p className="-mt-3 mb-4 text-xs text-subtle">Includes drafts and private datasets you own or manage through an organization.</p>
      ) : null}

      {list.isPending ? (
        <SkeletonCards count={9} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        active ? (
          mine && !f.q && !f.tag && !f.license ? (
            <EmptyState
              icon={<Database className="h-5 w-5" />}
              title="You haven't published a dataset yet"
              description="Upload CSVs, JSON, Parquet or archives, add a data dictionary and publish an immutable version."
              action={<LinkButton href="/datasets/new">Publish a dataset</LinkButton>}
            />
          ) : (
            <NoResults onReset={reset} />
          )
        ) : (
          <EmptyState
            icon={<Database className="h-5 w-5" />}
            title="No datasets yet"
            description="Be the first to share a dataset with the community."
            action={<LinkButton href="/datasets/new">Publish a dataset</LinkButton>}
          />
        )
      ) : (
        <div aria-busy={list.isFetching || undefined}>
          <ResultSummary total={list.data.total} noun="dataset" active={active} onClear={reset} />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {list.data.items.map((d) => <DatasetCard key={d.id} d={d} />)}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function DatasetsPage() {
  return (
    <Suspense fallback={<Container><div className="py-8"><SkeletonCards count={9} /></div></Container>}>
      <DatasetsList />
    </Suspense>
  );
}
