"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, Award, BadgeCheck, Check, GraduationCap, ListChecks, PenSquare, RotateCcw, Route, Search, Swords } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState, useSyncExternalStore } from "react";

import { CourseCard } from "@/components/domain/cards";
import { Reveal } from "@/components/motion/reveal";
import { DifficultyBadge, formatMinutes } from "@/components/learning/ui";
import { MANAGER_ROLES, type LearningPath, type MyCourse } from "@/components/learning/types";
import { Button, LinkButton } from "@/components/ui/button";
import { Checkbox, Input, Select } from "@/components/ui/form";
import { Cover, ProgressBar, ProgressRing } from "@/components/ui/misc";
import { Container, PageHeader, Section } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, Skeleton, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";
import { hasRole, useDebounced, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CourseCard as CourseCardT, Me, Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const DEFAULTS = { q: "", category: "", difficulty: "", enrolled: "", page: "1" };
const PAGE_SIZE = 12;

const noopSubscribe = () => () => {};

/**
 * False for the server render and the hydration pass, true afterwards. The viewer is only known on the
 * client, so viewer-specific blocks wait for hydration instead of mismatching the server HTML.
 */
function useHydrated(): boolean {
  return useSyncExternalStore(noopSubscribe, () => true, () => false);
}

function canAuthorCourses(me: Me | null | undefined): boolean {
  if (!me) return false;
  if (hasRole(me, "platform_admin")) return true;
  return me.memberships.some((m) => MANAGER_ROLES.includes(m.role) && m.status === "active");
}

/** Column count follows the number of tiles so a gap-px grid never shows empty hairline cells. */
const RESUME_COLS: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 lg:grid-cols-3",
  4: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4",
};

function ContinueLearning() {
  const mine = useQuery({ queryKey: ["learn", "me"], queryFn: () => get<MyCourse[]>("/learn/me") });
  if (mine.isPending) {
    return (
      <Section eyebrow="Your courses" title="Continue learning">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="status" aria-label="Loading">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 w-full rounded-[var(--radius-lg)]" />)}
        </div>
      </Section>
    );
  }
  if (mine.isError) return null; // non-essential strip; the catalog below still works
  const active = mine.data.filter((c) => !c.completed_at).slice(0, 4);
  if (!active.length) return null;
  const compact = active.length > 2;
  return (
    <Section eyebrow="Your courses" title="Continue learning" description="Pick up where you left off.">
      <ul className={cn("grid gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card animate-rise", RESUME_COLS[active.length])}>
        {active.map((c) => (
          <li key={c.id} className="min-w-0">
            <Link
              href={c.resume_url}
              className="group relative flex h-full min-w-0 items-center gap-4 bg-surface p-4 transition-colors duration-200 hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] sm:p-5"
            >
              {/* Course cover chip for identity, with the learner's real progress dial on a glass disc. */}
              <Cover style={c.cover_style} className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[var(--radius-md)] border border-border">
                <span className="flex rounded-full bg-[var(--glass-strong)] p-[3px] shadow-card backdrop-blur-md">
                  <ProgressRing value={c.progress.percent} size={44} stroke={4} />
                </span>
              </Cover>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-eyebrow text-subtle">
                  {titleCase(c.category)}
                  {c.lesson_count ? ` · ${c.lesson_count} lesson${c.lesson_count === 1 ? "" : "s"}` : ""}
                </span>
                <span className="mt-1 block truncate font-medium tracking-[-0.01em] text-fg transition-colors group-hover:text-accent-strong">{c.title}</span>
                <span className="mt-1 block truncate text-xs text-subtle">
                  {titleCase(c.difficulty)} · <span className="tabular">{formatMinutes(c.estimated_minutes)}</span>
                </span>
              </span>
              <span className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-bg-elevated py-1.5 text-xs font-medium text-accent-strong transition-[border-color,background-color] duration-200",
                "group-hover:border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] group-hover:bg-accent-soft",
                compact ? "px-2" : "px-2 sm:pl-3 sm:pr-2.5",
              )}>
                <span className={compact ? "sr-only" : "max-sm:sr-only"}>Resume</span>
                <ArrowRight className="h-3.5 w-3.5 transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** A learning path drawn as a track: each course is a station, lit once the server marks it complete. */
function PathTrack({ path }: { path: LearningPath }) {
  const total = path.courses.length;
  const pct = total ? Math.round((100 * path.completed_count) / total) : 0;
  const done = total > 0 && path.completed_count >= total;
  const stations = path.courses.length + (path.badge ? 1 : 0);
  return (
    <article className="relative isolate overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_60%)]" />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-24 -top-24 -z-10 h-64 w-96 max-w-full"
        style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
      />
      <header className="flex flex-col gap-5 p-5 sm:flex-row sm:items-end sm:justify-between sm:p-6">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-eyebrow text-accent-strong">
            <Route className="h-3.5 w-3.5" aria-hidden />
            Learning path
            <span className="text-subtle">· {total} course{total === 1 ? "" : "s"}</span>
          </p>
          <h3 className="mt-2 text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">{path.title}</h3>
          {path.summary ? <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{path.summary}</p> : null}
        </div>
        <div className="w-full shrink-0 space-y-1.5 sm:w-60">
          <div className="flex justify-between gap-3 text-xs text-muted">
            <span className={done ? "font-medium text-success" : undefined}>
              {done ? "Path complete" : `${path.completed_count} of ${total} courses complete`}
            </span>
            <span className="tabular text-fg">{pct}%</span>
          </div>
          <ProgressBar value={pct} label={`${path.title} progress`} />
        </div>
      </header>
      <ol
        className={cn("grid grid-cols-1 border-t border-border p-5 sm:p-6", stations > 1 && "md:grid-flow-col md:auto-cols-fr")}
        aria-label={`Courses in ${path.title}`}
      >
        {path.courses.map((c, i) => {
          const last = i === stations - 1;
          return (
            <li key={c.slug} className="relative flex min-w-0 gap-4 pb-7 last:pb-0 md:block md:pb-0 md:pr-6">
              {!last ? (
                <span
                  aria-hidden
                  className={cn(
                    "absolute bottom-2 left-[15.5px] top-10 w-px md:bottom-auto md:left-10 md:right-2 md:top-[15.5px] md:h-px md:w-auto",
                    c.completed ? "bg-brand" : "bg-border-strong",
                  )}
                />
              ) : null}
              <span
                aria-hidden
                className={cn(
                  "tabular relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] font-medium",
                  c.completed
                    ? "border-[color-mix(in_oklab,var(--success)_45%,transparent)] bg-success-soft text-success"
                    : "border-border-strong bg-bg-elevated text-subtle",
                )}
              >
                {c.completed ? <Check className="h-3.5 w-3.5" strokeWidth={2.6} /> : String(i + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0 pt-1 md:mt-3 md:pt-0">
                <Link
                  href={`/learn/${c.slug}`}
                  className="line-clamp-2 font-medium tracking-[-0.01em] text-fg transition-colors hover:text-accent-strong"
                >
                  {c.title}
                  {c.completed ? <span className="sr-only"> (completed)</span> : null}
                </Link>
                <p className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-subtle">
                  <span className="tabular">{formatMinutes(c.estimated_minutes)}</span>
                  <DifficultyBadge difficulty={c.difficulty} />
                </p>
              </div>
            </li>
          );
        })}
        {path.badge ? (
          <li className="relative flex min-w-0 gap-4 md:block">
            <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-border-strong bg-bg-elevated">
              <Award className="h-4 w-4 text-accent-strong" style={path.badge.color ? { color: path.badge.color } : undefined} aria-hidden />
            </span>
            <div className="min-w-0 pt-1 md:mt-3 md:pt-0">
              <p className="text-eyebrow text-subtle">Path badge</p>
              <p className="mt-1 font-medium tracking-[-0.01em] text-fg">{path.badge.name}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted">Earned when you complete every course in this path.</p>
            </div>
          </li>
        ) : null}
      </ol>
    </article>
  );
}

function LearningPaths() {
  const paths = useQuery({ queryKey: ["learn", "paths"], queryFn: () => get<LearningPath[]>("/learn/paths") });
  if (paths.isPending) {
    return (
      <Section eyebrow="Tracks" title="Learning paths">
        <div role="status" aria-label="Loading" className="rounded-[var(--radius-xl)] border border-border bg-surface p-6">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="mt-3 h-5 w-1/3" />
          <div className="mt-8 grid gap-6 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex gap-3 md:block">
                <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
                <Skeleton className="mt-3 h-4 w-3/4" />
              </div>
            ))}
          </div>
        </div>
      </Section>
    );
  }
  if (paths.isError) {
    return (
      <Section eyebrow="Tracks" title="Learning paths">
        <ErrorState error={paths.error} onRetry={() => paths.refetch()} />
      </Section>
    );
  }
  const visible = paths.data.filter((p) => p.courses.length > 0);
  if (!visible.length) return null;
  return (
    <Section eyebrow="Tracks" title="Learning paths" description="Curated sequences of courses. Finish every course in a path to earn its badge.">
      <div className="space-y-4">
        {visible.map((p, i) => (
          <Reveal key={p.slug} delay={i * 80}>
            <PathTrack path={p} />
          </Reveal>
        ))}
      </div>
    </Section>
  );
}

function Catalog({ signedIn, viewerKnown }: { signedIn: boolean; viewerKnown: boolean }) {
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
  // Which `enrolled` scope the last settled (non-placeholder) page was fetched with. A deep link to "My courses"
  // first fetches unscoped (the viewer is not known yet), and keepPreviousData would then show that unscoped page
  // while the scoped one loads; until the scoped page has settled the list is presented as loading instead.
  const [settledEnrolled, setSettledEnrolled] = useState<boolean | undefined>(undefined);
  if (courses.isSuccess && !courses.isPlaceholderData && settledEnrolled !== filters.enrolled) {
    setSettledEnrolled(filters.enrolled);
  }
  const awaitingScope =
    state.enrolled === "1" && (!viewerKnown || (signedIn && courses.isPlaceholderData && settledEnrolled !== true));
  const clearAll = () => {
    setSearch("");
    reset();
  };

  return (
    <Section
      id="catalog"
      eyebrow="Catalogue"
      title="Course catalog"
      description="Search the catalogue, or filter by topic and level."
    >
      <div
        role="search"
        aria-label="Filter courses"
        className="mb-4 flex flex-col gap-2 rounded-[var(--radius-lg)] border border-border surface-glass p-2 shadow-card md:flex-row md:items-center"
      >
        <div className="relative min-w-0 flex-1">
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
        <div className="grid grid-cols-2 gap-2 md:flex">
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
            className="flex h-10 shrink-0 items-center rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 [&>input]:mt-0"
            checked={state.enrolled === "1"}
            onChange={(e) => set({ enrolled: e.target.checked ? "1" : "" })}
          />
        ) : null}
      </div>

      <div className="mb-5 flex min-h-8 flex-wrap items-center justify-between gap-2 text-sm text-muted" aria-live="polite">
        <span>
          {courses.data && !awaitingScope ? (
            <>
              <span className="tabular font-medium text-fg">{courses.data.total}</span> course{courses.data.total === 1 ? "" : "s"}
              {filtered ? " match your filters" : " in the catalogue"}
            </>
          ) : (
            <span className="text-subtle">Loading courses…</span>
          )}
        </span>
        {filtered && !awaitingScope && courses.data?.items.length ? (
          <Button variant="ghost" size="sm" icon={<RotateCcw className="h-3.5 w-3.5" aria-hidden />} onClick={clearAll}>
            Clear filters
          </Button>
        ) : null}
      </div>

      {courses.isPending || awaitingScope ? (
        <SkeletonCards count={6} />
      ) : courses.isError ? (
        <ErrorState error={courses.error} onRetry={() => courses.refetch()} />
      ) : courses.data.items.length === 0 ? (
        filtered ? (
          <NoResults onReset={clearAll} />
        ) : (
          <EmptyState icon={<GraduationCap />} title="No courses yet" description="Published courses will appear here. Check back soon." />
        )
      ) : (
        <div aria-busy={courses.isFetching || undefined} className={cn("transition-opacity duration-200", courses.isFetching && "opacity-70")}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
  const hydrated = useHydrated();
  const signedIn = hydrated && Boolean(me.data);
  const viewerKnown = hydrated && !me.isPending;
  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Learn"
        icon={<GraduationCap />}
        title={
          <>
            Courses &amp; <span className="text-gradient">learning paths</span>
          </>
        }
        description="Short, practical courses with server-graded quizzes and hands-on challenges in real competitions. Finish a course to earn badges and verifiable certificates."
        meta={
          <>
            <span className="inline-flex items-center gap-1.5"><ListChecks className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> Server-graded quizzes</span>
            <span className="inline-flex items-center gap-1.5"><Swords className="h-3.5 w-3.5 text-cyan" aria-hidden /> Hands-on challenges</span>
            <span className="inline-flex items-center gap-1.5"><BadgeCheck className="h-3.5 w-3.5 text-success" aria-hidden /> Verifiable certificates</span>
          </>
        }
        actions={
          hydrated && canAuthorCourses(me.data) ? (
            <LinkButton href="/learn/authoring" variant="secondary" icon={<PenSquare className="h-4 w-4" aria-hidden />}>
              Author courses
            </LinkButton>
          ) : null
        }
      />
      {signedIn ? <ContinueLearning /> : null}
      <LearningPaths />
      <Catalog signedIn={signedIn} viewerKnown={viewerKnown} />
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
