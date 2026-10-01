"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BadgeCheck, FolderGit2, GitFork, Plus, Star, Trophy, User } from "lucide-react";
import { Suspense } from "react";

import { ActiveFilters } from "@/components/catalog/dataset-bits";
import { FilterBar, FilterSelect, ResultSummary, SearchBox, ToggleChip } from "@/components/catalog/filters";
import { ProjectCard } from "@/components/domain/cards";
import { Button, LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, Skeleton, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Page, ProjectCard as ProjectCardT } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 24;
const DEFAULTS = { q: "", tag: "", tech: "", open_source: "", featured: "", owner: "", sort: "updated", page: "1" };

function HeaderFacts() {
  const facts = [
    { icon: Trophy, label: "Competition write-ups" },
    { icon: GitFork, label: "Open-source tools" },
    { icon: BadgeCheck, label: "GitHub-verified maintainers" },
  ];
  return (
    <>
      {facts.map((f) => (
        <span key={f.label} className="inline-flex items-center gap-1.5">
          <f.icon className="h-3.5 w-3.5 text-accent-strong" aria-hidden />
          {f.label}
        </span>
      ))}
    </>
  );
}

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

  const chips = [
    ...(f.q ? [{ key: "q", label: <>Search: “{f.q}”</>, onRemove: () => setF({ q: "" }) }] : []),
    ...(f.tag ? [{ key: "tag", label: <>#{f.tag}</>, onRemove: () => setF({ tag: "" }) }] : []),
    ...(f.tech ? [{ key: "tech", label: <span className="font-mono">{f.tech}</span>, onRemove: () => setF({ tech: "" }) }] : []),
    ...(openSource !== undefined ? [{ key: "open_source", label: openSource ? "Open source" : "Closed source", onRemove: () => setF({ open_source: "" }) }] : []),
    ...(featured ? [{ key: "featured", label: "Featured", onRemove: () => setF({ featured: "" }) }] : []),
    ...(mine ? [{ key: "owner", label: "My projects", onRemove: () => setF({ owner: "" }) }] : []),
  ];

  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Showcase"
        icon={<FolderGit2 />}
        title="Projects"
        description="Portfolio projects, competition write-ups and open-source tools built by students. Verified maintainers own the linked GitHub repository."
        meta={<HeaderFacts />}
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
        <FilterSelect className="sm:w-44" label="Sort by" value={f.sort} onChange={(sort) => setF({ sort })}>
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

      <ActiveFilters chips={chips} />

      {mine ? <p className="-mt-3 mb-4 text-xs text-subtle">Includes drafts and private projects you own or are a member of.</p> : null}

      {list.isPending ? (
        <SkeletonCards count={9} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        mine && !f.q && !f.tag && !f.tech && openSource === undefined && !featured ? (
          <EmptyState
            icon={<FolderGit2 />}
            title="You don't have any projects yet"
            description="Showcase something you built — link the repository, add screenshots and invite collaborators."
            action={
              <>
                <LinkButton href="/projects/new" icon={<Plus className="h-4 w-4" aria-hidden />}>Create a project</LinkButton>
                <Button variant="ghost" onClick={() => setF({ owner: "" })}>Browse all projects</Button>
              </>
            }
          />
        ) : active ? (
          <NoResults onReset={reset} />
        ) : (
          <EmptyState
            icon={<GitFork />}
            title="No projects yet"
            description="Be the first to showcase a project."
            action={
              <>
                <LinkButton href="/projects/new" icon={<Plus className="h-4 w-4" aria-hidden />}>Create a project</LinkButton>
                <LinkButton href="/competitions" variant="ghost" icon={<Trophy className="h-4 w-4" aria-hidden />}>Find a competition to build for</LinkButton>
              </>
            }
          />
        )
      ) : (
        <div aria-busy={list.isFetching || undefined} className="transition-opacity duration-200 aria-busy:opacity-70">
          <ResultSummary total={list.data.total} noun="project" active={active} onClear={reset} />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {list.data.items.map((p, i) => (
              <div key={p.id} className="flex min-w-0 animate-rise [&>a]:w-full" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                <ProjectCard p={p} />
              </div>
            ))}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => setF({ page: String(p) }, { resetPage: false })} />
        </div>
      )}
    </Container>
  );
}

export default function ProjectsPage() {
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
          <SkeletonCards count={9} />
        </Container>
      }
    >
      <ProjectsList />
    </Suspense>
  );
}
