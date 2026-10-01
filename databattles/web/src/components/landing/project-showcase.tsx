"use client";

import { ArrowRight, FolderGit2 } from "lucide-react";

import { ProjectCard } from "@/components/domain/cards";
import { Reveal } from "@/components/motion/reveal";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { EmptyState, SkeletonCards } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import type { ProjectCard as ProjectCardT } from "@/lib/types";
import { SectionHeading } from "./section-heading";

/**
 * Student projects in a staggered gallery (the middle column sits lower on desktop) and a swipeable
 * strip on phones — a portfolio wall rather than another uniform grid.
 */
export function ProjectShowcase({ items, loading, error = false }: { items?: ProjectCardT[]; loading: boolean; error?: boolean }) {
  const list = items ?? [];
  return (
    <section className="relative overflow-hidden py-20 sm:py-28" aria-label="Student projects">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10" style={{ background: "radial-gradient(60% 50% at 80% 40%, var(--ambient-b), transparent)" }} />
      <Container>
        <SectionHeading
          index="04"
          eyebrow="Build"
          title="Built by students."
          description="Projects with repositories, demos and the technologies behind them — linked to the competitions and courses they came from."
          action={
            <LinkButton href="/projects" variant="secondary" icon={<ArrowRight className="h-4 w-4" />}>
              Explore projects
            </LinkButton>
          }
        />
        <div className="mt-12">
          {loading ? (
            <SkeletonCards count={3} />
          ) : list.length ? (
            <div className="-mx-4 flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pb-4 [scrollbar-width:none] sm:mx-0 sm:grid sm:snap-none sm:grid-cols-2 sm:overflow-visible sm:px-0 sm:pb-0 lg:grid-cols-3">
              {list.slice(0, 6).map((p, i) => (
                <Reveal
                  key={p.id}
                  delay={(i % 3) * 80}
                  className={cn("flex w-[82%] min-w-0 shrink-0 snap-start sm:w-auto [&>a]:w-full", i % 3 === 1 && "lg:translate-y-10")}
                >
                  <ProjectCard p={p} />
                </Reveal>
              ))}
            </div>
          ) : error ? (
            <EmptyState
              icon={<FolderGit2 />}
              title="Featured projects are unavailable right now"
              description="The live data couldn't be loaded. You can still browse every project."
              action={<LinkButton href="/projects" variant="secondary">Browse projects</LinkButton>}
            />
          ) : (
            <EmptyState
              icon={<FolderGit2 />}
              title="No projects yet"
              description="Be the first to publish what you built."
              action={<LinkButton href="/projects/new" variant="secondary">Share a project</LinkButton>}
            />
          )}
        </div>
      </Container>
    </section>
  );
}
