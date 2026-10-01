"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CircleDot, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { FilterBar, FilterSelect, ResultSummary, SearchBox, ToggleChip } from "@/components/catalog/filters";
import { IssueItem, osKeys } from "@/components/catalog/repo";
import type { IssueRow } from "@/components/catalog/types";
import { LinkButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonRows } from "@/components/ui/states";
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
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <Link href="/open-source" className="hover:text-fg">Open source</Link>
        <span aria-hidden> / </span>
        <span className="text-muted">Issues</span>
      </nav>
      <PageHeader
        className="pt-4"
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
        <div className="-mt-3 mb-4 flex items-center gap-2 text-sm">
          <span className="text-muted">Repository:</span>
          <span className="inline-flex items-center gap-1 rounded-md bg-surface-3 px-2 py-0.5 font-mono text-xs text-fg">
            {repoName ?? "selected repository"}
            <button type="button" onClick={() => setF({ repo_id: "" })} className="text-subtle hover:text-fg" aria-label="Clear repository filter">
              <X className="h-3 w-3" />
            </button>
          </span>
        </div>
      ) : null}

      {list.isPending ? (
        <SkeletonRows rows={8} />
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
        <div aria-busy={list.isFetching || undefined}>
          <ResultSummary total={list.data.total} noun="issue" active={active} onClear={reset} />
          <Card>
            <ul className="divide-y divide-border">
              {list.data.items.map((i) => <IssueItem key={i.id} issue={i} canPromote={isMod} />)}
            </ul>
          </Card>
          {anyDemo ? <p className="mt-2 text-xs text-subtle">Issues marked “Demo data” come from synthetic seed repositories and have no GitHub page.</p> : null}
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function OpenSourceIssuesPage() {
  return (
    <Suspense fallback={<Container className="py-8"><SkeletonRows rows={8} /></Container>}>
      <IssuesFeed />
    </Suspense>
  );
}
