"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRight, CircleDot, FolderGit2, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { FilterBar, FilterSelect, ResultSummary, SearchBox, ToggleChip } from "@/components/catalog/filters";
import { CatalogRowsSkeleton, IssueItem, osKeys } from "@/components/catalog/repo";
import type { IssueRow } from "@/components/catalog/types";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults } from "@/components/ui/states";
import { get } from "@/lib/api";
import { hasRole, useMe } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 25;
const DEFAULTS = { q: "", beginner: "", language: "", promoted: "", repo_id: "", page: "1" };

function IssuesFeed() {
  const [f, setF, reset] = useUrlState(DEFAULTS);
  const me = useMe();
  const isMod = hasRole(me.data, "moderator");
  const page = Math.max(1, Number(f.page) || 1);
  const query = {
    q: f.q || undefined,
    beginner: f.beginner === "true" || undefined,
    language: f.language || undefined,
    promoted: f.promoted === "true" ? true : f.promoted === "false" ? false : undefined,
    repo_id: f.repo_id || undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: osKeys.issues(query),
    queryFn: ({ signal }) => get<Page<IssueRow>>("/opensource/issues", query, signal),
    placeholderData: keepPreviousData,
  });
  const active = Boolean(f.q || f.beginner || f.language || f.promoted || f.repo_id);
  const repoName = f.repo_id ? list.data?.items.find((i) => i.repo.id === f.repo_id)?.repo.full_name : undefined;
  const anyDemo = (list.data?.items ?? []).some((i) => i.repo.is_demo);

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 pt-6 text-sm text-subtle">
        <Link href="/open-source" className="rounded-sm transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">Open source</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <span className="text-muted" aria-current="page">Issues</span>
      </nav>
      <PageHeader
        className="pt-4"
        eyebrow="Contribute"
        icon={<CircleDot />}
        title="Open issues"
        description="Open issues from registered repositories, refreshed by webhooks and periodic syncs. Promoted issues are picked by project maintainers."
        actions={<LinkButton href="/open-source/repos" variant="secondary">Browse repositories</LinkButton>}
      />

      <FilterBar>
        <SearchBox className="sm:min-w-56 sm:flex-1" label="Search issues" placeholder="Search issue titles" value={f.q} onCommit={(q) => setF({ q })} />
        <SearchBox className="sm:w-44" label="Filter by language" placeholder="Language (e.g. Python)" value={f.language} onCommit={(language) => setF({ language })} />
        <FilterSelect className="sm:w-44" label="Promotion" value={f.promoted} onChange={(promoted) => setF({ promoted })}>
          <option value="">All issues</option>
          <option value="true">Promoted only</option>
          <option value="false">Not promoted</option>
        </FilterSelect>
        <ToggleChip pressed={f.beginner === "true"} onChange={(on) => setF({ beginner: on ? "true" : "" })} icon={<Sparkles className="h-4 w-4" aria-hidden />}>
          Beginner friendly
        </ToggleChip>
      </FilterBar>

      {f.repo_id ? (
        <div className="-mt-2 mb-5 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-eyebrow text-subtle">Repository</span>
          <span className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[color-mix(in_oklab,var(--accent)_40%,transparent)] bg-accent-soft pl-3 pr-1 font-mono text-xs text-accent-strong">
            <FolderGit2 className="h-3.5 w-3.5" aria-hidden />
            <span className="max-w-[16rem] truncate">{repoName ?? "selected repository"}</span>
            <button
              type="button"
              onClick={() => setF({ repo_id: "" })}
              className="flex h-6 w-6 items-center justify-center rounded-full text-accent-strong/80 transition-colors hover:bg-surface-3 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
              aria-label="Clear repository filter"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        </div>
      ) : null}

      {list.isPending ? (
        <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
          <CatalogRowsSkeleton rows={6} label="Loading issues" />
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        active ? (
          <NoResults onReset={reset} />
        ) : (
          <EmptyState
            icon={<CircleDot className="h-5 w-5" />}
            title="No open issues yet"
            description="Issues appear here after a registered repository is synced."
            action={<LinkButton href="/open-source" variant="secondary">Register a repository</LinkButton>}
          />
        )
      ) : (
        <div aria-busy={list.isFetching || undefined} className={list.isPlaceholderData ? "opacity-70 transition-opacity" : "transition-opacity"}>
          <ResultSummary total={list.data.total} noun="issue" active={active} onClear={reset} />
          <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
            <ul className="divide-y divide-border" aria-label="Issues">
              {list.data.items.map((i) => <IssueItem key={i.id} issue={i} canPromote={isMod} />)}
            </ul>
          </div>
          {anyDemo ? <p className="mt-2 text-xs text-subtle">Issues marked “Demo data” come from synthetic seed repositories and have no GitHub page.</p> : null}
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function OpenSourceIssuesPage() {
  return (
    <Suspense
      fallback={
        <Container className="py-16">
          <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
            <CatalogRowsSkeleton rows={6} label="Loading issues" />
          </div>
        </Container>
      }
    >
      <IssuesFeed />
    </Suspense>
  );
}
