"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { FolderGit2, GitFork, Plus, Star, User } from "lucide-react";
import { Suspense } from "react";

import { FilterBar, FilterSelect, ResultSummary, SearchBox, ToggleChip } from "@/components/catalog/filters";
import { ProjectCard } from "@/components/domain/cards";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Page, ProjectCard as ProjectCardT } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 24;
const DEFAULTS = { q: "", tag: "", tech: "", open_source: "", featured: "", owner: "", sort: "updated", page: "1" };

function ProjectsList() {
  const [f, setF, reset] = useUrlState(DEFAULTS);
  const me = useMe();
  const signedIn = Boolean(me.data);
  const mine = f.owner === "me" && signedIn;
  const page = Math.max(1, Number(f.page) || 1);
  const openSource = f.open_source === "true" ? true : f.open_source === "false" ? false : undefined;
  const featured = f.featured === "true";

  const query = {
    q: f.q || undefined,
    tag: f.tag || undefined,
    tech: f.tech || undefined,
    open_source: openSource,
    featured: featured || undefined,
    owner: mine ? "me" : undefined,
    sort: f.sort,
    page,
    page_size: PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: qk.projects(query),
    queryFn: ({ signal }) => get<Page<ProjectCardT>>("/projects", query, signal),
    placeholderData: keepPreviousData,
    enabled: f.owner !== "me" || me.isSuccess,
  });
  const active = Boolean(f.q || f.tag || f.tech || openSource !== undefined || featured || mine);

  return (
    <Container>
      <PageHeader
        eyebrow="Showcase"
        title="Projects"
        description="Portfolio projects, competition write-ups and open-source tools built by students. Verified maintainers own the linked GitHub repository."
        actions={<LinkButton href="/projects/new" icon={<Plus className="h-4 w-4" aria-hidden />}>New project</LinkButton>}
      />

      <FilterBar>
        <SearchBox className="sm:min-w-56 sm:flex-1" label="Search projects" placeholder="Search by title or summary" value={f.q} onCommit={(q) => setF({ q })} />
        <SearchBox className="sm:w-40" label="Filter by tag" placeholder="Tag" value={f.tag} onCommit={(tag) => setF({ tag: tag.toLowerCase() })} />
        <SearchBox className="sm:w-40" label="Filter by technology" placeholder="Technology" value={f.tech} onCommit={(tech) => setF({ tech: tech.toLowerCase() })} />
        <FilterSelect className="sm:w-40" label="Source" value={f.open_source} onChange={(open_source) => setF({ open_source })}>
          <option value="">Any</option>
          <option value="true">Open source</option>
          <option value="false">Closed source</option>
        </FilterSelect>
        <FilterSelect className="sm:w-40" label="Sort by" value={f.sort} onChange={(sort) => setF({ sort })}>
          <option value="updated">Recently updated</option>
          <option value="new">Newest</option>
          <option value="title">Title (A–Z)</option>
        </FilterSelect>
        <div className="flex flex-wrap gap-2">
          <ToggleChip pressed={featured} onChange={(on) => setF({ featured: on ? "true" : "" })} icon={<Star className="h-4 w-4" aria-hidden />}>
            Featured
          </ToggleChip>
          {signedIn ? (
            <ToggleChip pressed={mine} onChange={(on) => setF({ owner: on ? "me" : "" })} icon={<User className="h-4 w-4" aria-hidden />}>
              My projects
            </ToggleChip>
          ) : null}
        </div>
      </FilterBar>

      {mine ? <p className="-mt-3 mb-4 text-xs text-subtle">Includes drafts and private projects you own or are a member of.</p> : null}

      {list.isPending ? (
        <SkeletonCards count={9} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        mine && !f.q && !f.tag && !f.tech && openSource === undefined && !featured ? (
          <EmptyState
            icon={<FolderGit2 className="h-5 w-5" />}
            title="You don't have any projects yet"
            description="Showcase something you built — link the repository, add screenshots and invite collaborators."
            action={<LinkButton href="/projects/new">Create a project</LinkButton>}
          />
        ) : active ? (
          <NoResults onReset={reset} />
        ) : (
          <EmptyState
            icon={<GitFork className="h-5 w-5" />}
            title="No projects yet"
            description="Be the first to showcase a project."
            action={<LinkButton href="/projects/new">Create a project</LinkButton>}
          />
        )
      ) : (
        <div aria-busy={list.isFetching || undefined}>
          <ResultSummary total={list.data.total} noun="project" active={active} onClear={reset} />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {list.data.items.map((p) => <ProjectCard key={p.id} p={p} />)}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense fallback={<Container><div className="py-8"><SkeletonCards count={9} /></div></Container>}>
      <ProjectsList />
    </Suspense>
  );
}
