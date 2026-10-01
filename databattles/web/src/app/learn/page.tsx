"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, PenSquare, Route, Search } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";

import { CourseCard } from "@/components/domain/cards";
import { DifficultyBadge, EarnableBadge, formatMinutes } from "@/components/learning/ui";
import { MANAGER_ROLES, type LearningPath, type MyCourse } from "@/components/learning/types";
import { LinkButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox, Input, Select } from "@/components/ui/form";
import { Cover, ProgressBar } from "@/components/ui/misc";
import { Container, PageHeader, Section } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, Skeleton, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { hasRole, useDebounced, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CourseCard as CourseCardT, Me, Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const DEFAULTS = { q: "", category: "", difficulty: "", enrolled: "", page: "1" };
const PAGE_SIZE = 12;

function canAuthorCourses(me: Me | null | undefined): boolean {
  if (!me) return false;
  if (hasRole(me, "platform_admin")) return true;
  return me.memberships.some((m) => MANAGER_ROLES.includes(m.role) && m.status === "active");
}

function ContinueLearning() {
  const mine = useQuery({ queryKey: ["learn", "me"], queryFn: () => get<MyCourse[]>("/learn/me") });
  if (mine.isPending) {
    return (
      <Section title="Continue learning">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="status" aria-label="Loading">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 w-full rounded-[var(--radius-lg)]" />)}
        </div>
      </Section>
    );
  }
  if (mine.isError) return null; // non-essential strip; the catalog below still works
  const active = mine.data.filter((c) => !c.completed_at).slice(0, 4);
  if (!active.length) return null;
  return (
    <Section title="Continue learning" description="Pick up where you left off.">
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {active.map((c) => (
          <li key={c.id}>
            <Link
              href={c.resume_url}
              className="group flex h-full gap-3 rounded-[var(--radius-lg)] border border-border bg-surface p-3 transition-colors hover:border-border-strong"
            >
              <Cover style={c.cover_style} className="h-16 w-16 shrink-0 rounded-[var(--radius-md)]" />
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="line-clamp-2 text-sm font-medium text-fg group-hover:text-accent-strong">{c.title}</span>
                <div className="mt-auto space-y-1 pt-2">
                  <ProgressBar value={c.progress.percent} label={`${c.title} progress`} />
                  <span className="flex items-center justify-between text-xs text-muted">
                    <span>{c.progress.percent}%</span>
                    <span className="inline-flex items-center gap-1 text-accent-strong">
                      Resume <ArrowRight className="h-3 w-3" aria-hidden />
                    </span>
                  </span>
                </div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function PathCard({ path }: { path: LearningPath }) {
  const total = path.courses.length;
  const pct = total ? Math.round((100 * path.completed_count) / total) : 0;
  const done = total > 0 && path.completed_count >= total;
  return (
    <Card className="flex flex-col p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-semibold text-fg">
            <Route className="h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
            {path.title}
          </h3>
          {path.summary ? <p className="mt-1 text-sm text-muted">{path.summary}</p> : null}
        </div>
      </div>
      <div className="mt-4 space-y-1.5">
        <div className="flex justify-between text-xs text-muted">
          <span>{done ? "Path complete" : `${path.completed_count} of ${total} courses complete`}</span>
          <span>{pct}%</span>
        </div>
        <ProgressBar value={pct} label={`${path.title} progress`} />
      </div>
      <ol className="mt-4 space-y-1" aria-label={`Courses in ${path.title}`}>
        {path.courses.map((c, i) => (
          <li key={c.slug}>
            <Link href={`/learn/${c.slug}`} className="flex items-center gap-3 rounded-[var(--radius-md)] px-2 py-2 text-sm hover:bg-surface-2">
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${c.completed ? "bg-success-soft text-success" : "bg-surface-3 text-muted"}`}
              >
                {c.completed ? <Check className="h-3.5 w-3.5" aria-hidden /> : i + 1}
              </span>
              <span className="min-w-0 flex-1 truncate text-fg">
                {c.title}
                {c.completed ? <span className="sr-only"> (completed)</span> : null}
              </span>
              <span className="hidden shrink-0 text-xs text-subtle sm:inline">{formatMinutes(c.estimated_minutes)}</span>
              <DifficultyBadge difficulty={c.difficulty} className="hidden shrink-0 sm:inline-flex" />
            </Link>
          </li>
        ))}
      </ol>
      {path.badge ? (
        <div className="mt-auto border-t border-border pt-4">
          <EarnableBadge name={path.badge.name} color={path.badge.color} description="Earned when you complete every course in this path." />
        </div>
      ) : null}
    </Card>
  );
}

function LearningPaths() {
  const paths = useQuery({ queryKey: ["learn", "paths"], queryFn: () => get<LearningPath[]>("/learn/paths") });
  if (paths.isPending) {
    return (
      <Section title="Learning paths">
        <div className="grid gap-4 md:grid-cols-2" role="status" aria-label="Loading">
          <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
          <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
        </div>
      </Section>
    );
  }
  if (paths.isError) {
    return (
      <Section title="Learning paths">
        <ErrorState error={paths.error} onRetry={() => paths.refetch()} />
      </Section>
    );
  }
  const visible = paths.data.filter((p) => p.courses.length > 0);
  if (!visible.length) return null;
  return (
    <Section title="Learning paths" description="Curated sequences of courses. Finish every course in a path to earn its badge.">
      <div className="grid gap-4 md:grid-cols-2">
        {visible.map((p) => <PathCard key={p.slug} path={p} />)}
      </div>
    </Section>
  );
}

function Catalog({ signedIn }: { signedIn: boolean }) {
  const [state, set, reset] = useUrlState(DEFAULTS);
  const page = Math.max(1, Number(state.page) || 1);
  const [search, setSearch] = useState(state.q);
  const debounced = useDebounced(search, 350);

  useEffect(() => {
    if (debounced !== state.q) set({ q: debounced });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  useEffect(() => {
    // URL changed from outside the input (reset, back/forward)
    if (state.q !== debounced) setSearch(state.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.q]);

  const categories = useQuery({ queryKey: ["learn", "categories"], queryFn: () => get<string[]>("/learn/categories"), staleTime: 10 * 60_000 });
  const filters = {
    q: state.q || undefined,
    category: state.category || undefined,
    difficulty: state.difficulty || undefined,
    enrolled: signedIn && state.enrolled === "1" ? true : undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const courses = useQuery({
    queryKey: qk.courses(filters),
    queryFn: () => get<Page<CourseCardT>>("/learn/courses", filters),
    placeholderData: keepPreviousData,
  });
  const filtered = Boolean(state.q || state.category || state.difficulty || state.enrolled);

  return (
    <Section title="Course catalog" description={courses.data ? `${courses.data.total} course${courses.data.total === 1 ? "" : "s"}` : undefined}>
      <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center">
        <div className="relative flex-1">
          <label htmlFor="course-search" className="sr-only">Search courses</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input
            id="course-search"
            type="search"
            className="pl-9"
            placeholder="Search courses…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            maxLength={80}
          />
        </div>
        <div className="grid grid-cols-2 gap-3 md:flex">
          <Select aria-label="Category" value={state.category} onChange={(e) => set({ category: e.target.value })} className="md:w-48">
            <option value="">All categories</option>
            {categories.data?.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
          </Select>
          <Select aria-label="Difficulty" value={state.difficulty} onChange={(e) => set({ difficulty: e.target.value })} className="md:w-40">
            <option value="">Any level</option>
            <option value="beginner">Beginner</option>
            <option value="intermediate">Intermediate</option>
            <option value="advanced">Advanced</option>
          </Select>
        </div>
        {signedIn ? (
          <Checkbox
            label="My courses"
            className="shrink-0 md:ml-1"
            checked={state.enrolled === "1"}
            onChange={(e) => set({ enrolled: e.target.checked ? "1" : "" })}
          />
        ) : null}
      </div>

      {courses.isPending ? (
        <SkeletonCards count={6} />
      ) : courses.isError ? (
        <ErrorState error={courses.error} onRetry={() => courses.refetch()} />
      ) : courses.data.items.length === 0 ? (
        filtered ? (
          <NoResults onReset={() => { setSearch(""); reset(); }} />
        ) : (
          <EmptyState title="No courses yet" description="Published courses will appear here. Check back soon." />
        )
      ) : (
        <div aria-busy={courses.isFetching || undefined}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {courses.data.items.map((c) => <CourseCard key={c.id} c={c} />)}
          </div>
          <Pagination
            page={courses.data.page}
            pageSize={courses.data.page_size}
            total={courses.data.total}
            onPage={(p) => {
              set({ page: String(p) });
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
          />
        </div>
      )}
    </Section>
  );
}

function LearnHome() {
  const me = useMe();
  const signedIn = Boolean(me.data);
  return (
    <Container className="pb-16">
      <PageHeader
        eyebrow="Learn"
        title="Courses & learning paths"
        description="Short, practical courses with server-graded quizzes and hands-on challenges in real competitions. Finish a course to earn badges and verifiable certificates."
        actions={
          canAuthorCourses(me.data) ? (
            <LinkButton href="/learn/authoring" variant="secondary" icon={<PenSquare className="h-4 w-4" aria-hidden />}>
              Author courses
            </LinkButton>
          ) : null
        }
      />
      {signedIn ? <ContinueLearning /> : null}
      <LearningPaths />
      <Catalog signedIn={signedIn} />
    </Container>
  );
}

export default function LearnPage() {
  return (
    <Suspense>
      <LearnHome />
    </Suspense>
  );
}
