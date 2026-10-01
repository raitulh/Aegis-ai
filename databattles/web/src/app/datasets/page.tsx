"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Database, Download, Eye, FileStack, Fingerprint, GitCommitVertical, LayoutGrid, Plus, Rows3, Trophy, User } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";

import { ActiveFilters } from "@/components/catalog/dataset-bits";
import { FilterBar, FilterSelect, ResultSummary, SearchBox, ToggleChip } from "@/components/catalog/filters";
import { DatasetCard } from "@/components/domain/cards";
import { DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/extras";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, Skeleton, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber, formatBytes, relativeTime } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { DatasetCard as DatasetCardT, Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 24;
const DEFAULTS = { q: "", tag: "", license: "", sort: "updated", owner: "", page: "1" };

type View = "grid" | "list";
const VIEW_KEY = "db-datasets-view";

/** Grid vs catalogue layout — a per-viewer convenience kept in this browser only. */
function useViewPreference(): [View, (v: View) => void] {
  const [view, setView] = useState<View>("grid");
  useEffect(() => {
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "grid" || saved === "list") setView(saved);
    } catch {
      /* storage unavailable: keep the default */
    }
  }, []);
  return [
    view,
    (v) => {
      setView(v);
      try {
        localStorage.setItem(VIEW_KEY, v);
      } catch {
        /* ignore */
      }
    },
  ];
}

/** Dense catalogue row: data presented like data (same fields as the card). */
function CatalogueRow({ d }: { d: DatasetCardT }) {
  const owner = d.owner_org?.name ?? d.owner?.display_name ?? null;
  return (
    <li>
      <Link
        href={`/datasets/${d.slug}`}
        className="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 px-4 py-3.5 transition-colors duration-200 hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] sm:px-5 lg:grid-cols-[auto_minmax(0,1fr)_8rem_7.5rem_5.5rem_10rem]"
      >
        <span className="row-span-2 flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-cyan-soft text-cyan shadow-[inset_0_1px_0_var(--hairline-highlight)] lg:row-span-1" aria-hidden>
          <Database className="h-4 w-4" />
        </span>
        <span className="min-w-0">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 sm:flex-nowrap">
            <span className="min-w-0 truncate font-medium text-fg transition-colors group-hover:text-accent-strong">{d.title}</span>
            {d.is_demo ? <DemoBadge className="shrink-0" /> : null}
          </span>
          <span className="block truncate text-xs text-subtle">
            {owner ? <span className="text-muted">{owner}</span> : null}
            {owner && d.subtitle ? " · " : null}
            {d.subtitle}
          </span>
        </span>
        <ArrowUpRight className="h-4 w-4 text-subtle transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-strong lg:hidden" aria-hidden />
        <span className="col-start-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted lg:contents">
          <span className="truncate font-mono text-[11.5px]" title={d.license_name}>{d.license.toUpperCase()}</span>
          <span className="tabular inline-flex items-center gap-1">
            <FileStack className="h-3.5 w-3.5 text-subtle" aria-hidden />
            {d.file_count} · {formatBytes(d.total_bytes)}
          </span>
          <span className="tabular inline-flex items-center gap-1" title={`${d.download_count} downloads`}>
            <Download className="h-3.5 w-3.5 text-subtle" aria-hidden />
            {compactNumber(d.download_count)}
            <span className="sr-only">downloads</span>
          </span>
          <span className="tabular text-subtle lg:text-right">{d.latest_version ? `v${d.latest_version}` : "Unpublished"} · {relativeTime(d.updated_at)}</span>
        </span>
      </Link>
    </li>
  );
}

function Catalogue({ items }: { items: DatasetCardT[] }) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card animate-rise">
      <div
        className="hidden grid-cols-[auto_minmax(0,1fr)_8rem_7.5rem_5.5rem_10rem] gap-x-4 whitespace-nowrap border-b border-border bg-bg-elevated/70 px-5 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle lg:grid"
        aria-hidden
      >
        <span className="w-9" />
        <span>Dataset</span>
        <span>License</span>
        <span>Files · size</span>
        <span>Downloads</span>
        <span className="text-right">Version · updated</span>
      </div>
      <ul className="divide-y divide-border">
        {items.map((d) => <CatalogueRow key={d.id} d={d} />)}
      </ul>
    </div>
  );
}

function ListSkeleton({ view }: { view: View }) {
  if (view === "grid") return <SkeletonCards count={9} media={false} />;
  return (
    <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface" role="status" aria-label="Loading">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-5 py-4" style={{ opacity: 1 - i * 0.08 }}>
          <Skeleton className="h-9 w-9 rounded-lg" />
          <div className="flex-1">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="mt-2 h-3 w-1/2" />
          </div>
          <Skeleton className="hidden h-3 w-40 lg:block" />
        </div>
      ))}
    </div>
  );
}

function HeaderFacts() {
  const facts = [
    { icon: Fingerprint, label: "SHA-256 per file" },
    { icon: GitCommitVertical, label: "Immutable versions" },
    { icon: Eye, label: "CSV / TSV previews" },
  ];
  return (
    <>
      {facts.map((f) => (
        <span key={f.label} className="inline-flex items-center gap-1.5">
          <f.icon className="h-3.5 w-3.5 text-cyan" aria-hidden />
          {f.label}
        </span>
      ))}
    </>
  );
}

function DatasetsList() {
  const [f, setF, reset] = useUrlState(DEFAULTS);
  const me = useMe();
  const signedIn = Boolean(me.data);
  const page = Math.max(1, Number(f.page) || 1);
  const mine = f.owner === "me" && signedIn;
  const [view, setView] = useViewPreference();

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

  const chips = [
    ...(f.q ? [{ key: "q", label: <>Search: “{f.q}”</>, onRemove: () => setF({ q: "" }) }] : []),
    ...(f.tag ? [{ key: "tag", label: <>#{f.tag}</>, onRemove: () => setF({ tag: "" }) }] : []),
    ...(f.license ? [{ key: "license", label: licenses.data?.[f.license] ?? f.license, onRemove: () => setF({ license: "" }) }] : []),
    ...(mine ? [{ key: "owner", label: "My datasets", onRemove: () => setF({ owner: "" }) }] : []),
  ];

  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Dataset hub"
        icon={<Database />}
        title="Datasets"
        description="Versioned, licensed datasets for competitions, courses and your own projects. Every file has a checksum and a preview."
        meta={<HeaderFacts />}
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

      <ActiveFilters chips={chips} />

      {mine ? (
        <p className="-mt-3 mb-4 text-xs text-subtle">Includes drafts and private datasets you own or manage through an organization.</p>
      ) : null}

      {list.isPending ? (
        <ListSkeleton view={view} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        active ? (
          mine && !f.q && !f.tag && !f.license ? (
            <EmptyState
              icon={<Database />}
              title="You haven't published a dataset yet"
              description="Upload CSVs, JSON, Parquet or archives, add a data dictionary and publish an immutable version."
              action={
                <>
                  <LinkButton href="/datasets/new" icon={<Plus className="h-4 w-4" aria-hidden />}>Publish a dataset</LinkButton>
                  <Button variant="ghost" onClick={() => setF({ owner: "" })}>Browse all datasets</Button>
                </>
              }
            />
          ) : (
            <NoResults onReset={reset} />
          )
        ) : (
          <EmptyState
            icon={<Database />}
            title="No datasets yet"
            description="Be the first to share a dataset with the community."
            action={
              <>
                <LinkButton href="/datasets/new" icon={<Plus className="h-4 w-4" aria-hidden />}>Publish a dataset</LinkButton>
                <LinkButton href="/competitions" variant="ghost" icon={<Trophy className="h-4 w-4" aria-hidden />}>Explore competitions</LinkButton>
              </>
            }
          />
        )
      ) : (
        <div aria-busy={list.isFetching || undefined} className="transition-opacity duration-200 aria-busy:opacity-70">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1 [&>div]:mb-0">
              <ResultSummary total={list.data.total} noun="dataset" active={active} onClear={reset} />
            </div>
            <SegmentedControl
              label="Layout"
              size="sm"
              className="max-sm:[&_button]:h-9 max-sm:[&_button]:px-3"
              value={view}
              onChange={setView}
              options={[
                { value: "grid", label: <span className="sr-only sm:not-sr-only">Grid</span>, icon: <LayoutGrid aria-hidden /> },
                { value: "list", label: <span className="sr-only sm:not-sr-only">Catalogue</span>, icon: <Rows3 aria-hidden /> },
              ]}
            />
          </div>
          {view === "grid" ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {list.data.items.map((d, i) => (
                <div key={d.id} className="flex min-w-0 animate-rise [&>a]:w-full" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                  <DatasetCard d={d} />
                </div>
              ))}
            </div>
          ) : (
            <Catalogue items={list.data.items} />
          )}
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function DatasetsPage() {
  return (
    <Suspense
      fallback={
        <Container>
          <div className="pb-8 pt-12">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="mt-4 h-9 w-56" />
            <Skeleton className="mt-4 h-4 w-full max-w-xl" />
          </div>
          <Skeleton className="mb-6 h-[4.25rem] w-full rounded-[var(--radius-lg)]" />
          <SkeletonCards count={9} media={false} />
        </Container>
      }
    >
      <DatasetsList />
    </Suspense>
  );
}
