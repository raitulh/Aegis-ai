"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, BookOpen, Clock, Layers } from "lucide-react";
import Link from "next/link";

import { Reveal } from "@/components/motion/reveal";
import { DemoBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber, titleCase } from "@/lib/format";
import { useInView } from "@/lib/motion";
import { qk } from "@/lib/query";
import type { CourseCard, Page } from "@/lib/types";
import { SectionHeading } from "./section-heading";

const PARAMS = { page: 1, page_size: 4 };

/** Published courses drawn as a learning track: each course is a station on one line. */
export function LearningSection() {
  const [ref, near] = useInView<HTMLElement>({ rootMargin: "400px 0px" });
  const courses = useQuery({
    queryKey: qk.courses(PARAMS),
    queryFn: () => get<Page<CourseCard>>("/learn/courses", PARAMS),
    enabled: near,
    staleTime: 60_000,
  });
  const items = courses.data?.items ?? [];

  return (
    <section ref={ref} className="relative py-20 sm:py-28" aria-label="Learning">
      <Container className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:gap-16">
        <div className="lg:sticky lg:top-28 lg:self-start">
          <SectionHeading
            index="05"
            eyebrow="Learn"
            title="Learn by doing, graded on the server."
            description="Short courses with quizzes and hands-on challenges. Your progress is recorded — and completions can issue certificates and badges."
          />
          <Reveal delay={80} className="mt-8 flex flex-wrap gap-3">
            <LinkButton href="/learn" icon={<BookOpen className="h-4 w-4" />}>Explore courses</LinkButton>
            <LinkButton href="/learn/authoring" variant="ghost">Teach a course</LinkButton>
          </Reveal>
        </div>

        <div className="relative">
          <div aria-hidden className="absolute bottom-6 left-[19px] top-6 w-px bg-gradient-to-b from-accent via-border-strong to-transparent" />
          {courses.isPending ? (
            <ol className="space-y-4" role="status" aria-label="Loading courses">
              {Array.from({ length: 3 }).map((_, i) => (
                <li key={i} className="flex gap-5">
                  <Skeleton className="h-10 w-10 shrink-0 rounded-full" />
                  <div className="flex-1 rounded-[var(--radius-lg)] border border-border bg-surface p-5">
                    <Skeleton className="h-3 w-24" />
                    <Skeleton className="mt-3 h-4 w-2/3" />
                    <Skeleton className="mt-2 h-3 w-1/2" />
                  </div>
                </li>
              ))}
            </ol>
          ) : courses.isError || !items.length ? (
            <EmptyState
              icon={<BookOpen />}
              title={courses.isError ? "Courses are unavailable right now" : "No courses published yet"}
              description="Check back soon, or browse the full catalogue."
              action={<LinkButton href="/learn" variant="secondary">Open the catalogue</LinkButton>}
            />
          ) : (
            <ol className="space-y-4">
              {items.map((c, i) => (
                <Reveal as="li" key={c.id} delay={i * 80} className="relative flex gap-5">
                  <span className="tabular relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-border-strong bg-surface font-mono text-xs text-accent-strong shadow-[0_0_0_6px_var(--bg)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <Link
                    href={`/learn/${c.slug}`}
                    className="group lift flex min-w-0 flex-1 flex-col rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card"
                  >
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">
                      <span>{titleCase(c.category)}</span>
                      <span>{titleCase(c.difficulty)}</span>
                      <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3" aria-hidden />{Math.round((c.estimated_minutes / 60) * 10) / 10}h</span>
                      {c.lesson_count ? <span className="inline-flex items-center gap-1"><Layers className="h-3 w-3" aria-hidden />{c.lesson_count} lessons</span> : null}
                    </span>
                    <span className="mt-2 flex items-center gap-2">
                      <span className="truncate text-[16px] font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong">{c.title}</span>
                      {c.is_demo ? <DemoBadge /> : null}
                    </span>
                    <span className="mt-1 line-clamp-2 text-sm text-muted">{c.summary}</span>
                    <span className="mt-4 flex items-center gap-3 text-xs text-subtle">
                      {c.issues_certificate ? <span className="inline-flex items-center gap-1 text-success"><BadgeCheck className="h-3.5 w-3.5" aria-hidden />Certificate</span> : null}
                      <span className="tabular">{compactNumber(c.enrollment_count)} learners</span>
                      {c.progress.enrolled ? <span className="tabular text-accent-strong">{c.progress.percent}% done</span> : null}
                      <ArrowRight className="ml-auto h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:text-accent-strong" aria-hidden />
                    </span>
                  </Link>
                </Reveal>
              ))}
            </ol>
          )}
        </div>
      </Container>
    </section>
  );
}
