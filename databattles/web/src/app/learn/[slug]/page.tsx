"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, BookOpen, Building, Check, ChevronRight, Clock, GraduationCap, LogOut, Pencil, Play, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { UserLink } from "@/components/domain/cards";
import { useAction } from "@/components/discussions/use-action";
import type { CourseDetail } from "@/components/learning/types";
import { CourseOutline, DifficultyBadge, EarnableBadge, formatMinutes, kindSummary } from "@/components/learning/ui";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { MetaItem } from "@/components/ui/extras";
import { Prose } from "@/components/ui/markdown";
import { Cover, ProgressRing } from "@/components/ui/misc";
import { Container, Section } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, Skeleton } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { compactNumber, formatDate, titleCase } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

function CourseSkeleton() {
  return (
    <Container className="pb-16">
      <div role="status" aria-label="Loading course">
        <Skeleton className="mt-6 h-4 w-48" />
        <div className="mt-5 grid gap-8 rounded-[var(--radius-2xl)] border border-border bg-surface p-5 sm:p-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div>
            <Skeleton className="h-5 w-40 rounded-full" />
            <Skeleton className="mt-5 h-9 w-2/3" />
            <Skeleton className="mt-3 h-4 w-1/2" />
            <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i}>
                  <Skeleton className="h-2.5 w-16" />
                  <Skeleton className="mt-2 h-5 w-20" />
                </div>
              ))}
            </div>
            <Skeleton className="mt-8 h-11 w-44 rounded-[var(--radius-md)]" />
          </div>
          <Skeleton className="aspect-[2/1] w-full rounded-[var(--radius-xl)] lg:aspect-[4/3]" />
        </div>
        <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
          </div>
          <Skeleton className="h-56 w-full" />
        </div>
      </div>
    </Container>
  );
}

const heroGlow = { background: "radial-gradient(closest-side, var(--ambient-a), transparent)" };

function CourseView({ course }: { course: CourseDetail }) {
  const c = course;
  const me = useMe();
  const qc = useQueryClient();
  const slug = c.slug;
  const enrolled = c.progress.enrolled;
  const completed = c.progress.completed;
  const doneLessons = c.lessons.filter((l) => l.status === "completed").length;
  const firstLesson = c.lessons[0]?.slug ?? null;
  const resume = c.resume_lesson_slug ?? firstLesson;
  const published = c.status === "published";

  const enroll = useAction(() => post<CourseDetail>(`/learn/courses/${slug}/enroll`), {
    success: "You're enrolled — enjoy the course!",
    invalidate: [["courses", "list"], ["learn"], ["courses", slug, "lessons"]],
    onSuccess: (data) => {
      qc.setQueryData(qk.course(slug), data);
    },
  });
  const leave = useAction(() => post<{ message: string }>(`/learn/courses/${slug}/unenroll`), {
    success: (d) => d.message,
    invalidate: [qk.course(slug), ["courses", "list"], ["learn"], ["courses", slug, "lessons"]],
  });

  const credential =
    c.issues_certificate && c.badge ? "Certificate + badge" : c.issues_certificate ? "Certificate" : c.badge ? "Badge" : "—";
  const kinds = kindSummary(c.lessons);

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <ol className="flex min-w-0 items-center gap-1.5">
          <li><Link href="/learn" className="transition-colors hover:text-fg">Learn</Link></li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="min-w-0 truncate text-muted" aria-current="page">{c.title}</li>
        </ol>
      </nav>

      {/* ------------------------------------------------------------------ hero */}
      <header className="relative isolate mt-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-card animate-rise">
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_top_left,black,transparent_55%)]" />
        <div aria-hidden className="pointer-events-none absolute -left-24 -top-28 -z-10 h-72 w-[30rem] max-w-full" style={heroGlow} />

        <div className="grid grid-cols-1 gap-8 p-4 sm:p-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)] lg:gap-10">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-eyebrow text-accent-strong">
              <GraduationCap className="h-3.5 w-3.5" aria-hidden />
              Course
              <span className="text-subtle">· {titleCase(c.category)}</span>
            </p>
            <h1 className="mt-3 text-title text-fg">{c.title}</h1>
            {c.summary ? <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-muted">{c.summary}</p> : null}

            <div className="mt-4 flex flex-wrap items-center gap-1.5">
              <DifficultyBadge difficulty={c.difficulty} />
              {c.status !== "published" ? <StatusBadge status={c.status} /> : null}
              {c.visibility === "org" ? <Badge tone="info">Members only</Badge> : null}
              {c.is_demo ? <DemoBadge /> : null}
            </div>

            {c.author || c.org ? (
              <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted">
                {c.author ? (
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <span className="text-subtle">by</span> <UserLink user={c.author} size={20} />
                  </span>
                ) : null}
                {c.org ? (
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <Building className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
                    <Link href={`/orgs/${c.org.slug}`} className="truncate transition-colors hover:text-fg">{c.org.name}</Link>
                  </span>
                ) : null}
              </div>
            ) : null}

            <dl className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-4">
              <MetaItem icon={<Clock className="shrink-0" aria-hidden />} label="Duration" className="bg-surface px-3 py-3 sm:px-4">
                <span className="tabular">{formatMinutes(c.estimated_minutes)}</span>
              </MetaItem>
              <MetaItem icon={<BookOpen className="shrink-0" aria-hidden />} label="Lessons" className="bg-surface px-3 py-3 sm:px-4">
                <span className="tabular">{c.lessons.length}</span> lesson{c.lessons.length === 1 ? "" : "s"}
              </MetaItem>
              <MetaItem icon={<Users className="shrink-0" aria-hidden />} label="Learners" className="bg-surface px-3 py-3 sm:px-4">
                <span className="tabular">{compactNumber(c.enrollment_count)}</span> enrolled
              </MetaItem>
              <MetaItem icon={<Award className="shrink-0" aria-hidden />} label="Credential" className="bg-surface px-3 py-3 sm:px-4">
                {credential}
              </MetaItem>
            </dl>

            <div className="mt-6 flex flex-col gap-3">
              {!published ? (
                <InlineNotice tone="info" title={c.status === "archived" ? "Archived course" : "Draft preview"}>
                  {c.status === "archived"
                    ? "This course is archived and no longer accepts enrollments."
                    : "Only authors can see this course. Publish it to open enrollment."}
                </InlineNotice>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                {!published ? null : enrolled ? (
                  resume ? (
                    <LinkButton href={`/learn/${slug}/${resume}`} size="lg" icon={<Play className="h-4 w-4" aria-hidden />}>
                      {completed ? "Review course" : c.progress.percent > 0 ? "Continue learning" : "Start the course"}
                    </LinkButton>
                  ) : null
                ) : me.data ? (
                  <Button size="lg" onClick={() => enroll.mutate()} loading={enroll.isPending} icon={<GraduationCap className="h-4 w-4" aria-hidden />}>
                    Enroll for free
                  </Button>
                ) : me.isPending ? (
                  <Skeleton className="h-11 w-40 rounded-[var(--radius-md)]" />
                ) : (
                  <LinkButton href={`/login?next=${encodeURIComponent(`/learn/${slug}`)}`} size="lg">Sign in to enroll</LinkButton>
                )}

                {!enrolled && firstLesson && published ? (
                  <LinkButton href={`/learn/${slug}/${firstLesson}`} variant="ghost">
                    Preview the first lesson
                  </LinkButton>
                ) : null}

                {c.certificate_public_id ? (
                  <LinkButton href={`/verify/${course.certificate_public_id}`} variant="secondary" icon={<Award className="h-4 w-4" aria-hidden />}>
                    View your certificate
                  </LinkButton>
                ) : null}

                {c.can_author ? (
                  <LinkButton href={`/learn/authoring/${slug}`} variant="outline" icon={<Pencil className="h-4 w-4" aria-hidden />}>
                    Edit course
                  </LinkButton>
                ) : null}
              </div>
            </div>
          </div>

          {/* Poster: generated cover art with the learner's real progress (or the free-course note) on glass. */}
          <div className="relative min-w-0 lg:self-stretch">
            <Cover style={c.cover_style} className="aspect-[16/9] h-full w-full rounded-[var(--radius-xl)] border border-border shadow-elevated sm:aspect-[2/1] lg:aspect-auto lg:min-h-[17rem]">
              <div aria-hidden className="absolute inset-0 bg-[radial-gradient(80%_60%_at_50%_100%,rgb(0_0_0/0.45),transparent)]" />
              {kinds ? (
                <span className="absolute left-3 top-3 max-w-[calc(100%-1.5rem)] truncate rounded-md bg-black/45 px-2 py-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-white/85 ring-1 ring-inset ring-white/15 backdrop-blur-md">
                  {kinds}
                </span>
              ) : null}
              <div className="absolute inset-x-3 bottom-3 flex items-center gap-3 rounded-[var(--radius-lg)] border border-border bg-[var(--glass-strong)] p-3 shadow-card backdrop-blur-xl">
                {enrolled ? (
                  <span className="shrink-0">
                    <ProgressRing value={c.progress.percent} size={52} stroke={4} />
                  </span>
                ) : (
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border bg-accent-soft text-accent-strong">
                    <GraduationCap className="h-5 w-5" aria-hidden />
                  </span>
                )}
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 text-sm font-semibold text-fg">
                    {completed ? <Check className="h-4 w-4 text-success" aria-hidden /> : null}
                    {completed ? "Course completed" : enrolled ? "Your progress" : "Free course"}
                  </p>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted">
                    {enrolled ? (
                      <>
                        <span className="tabular">{doneLessons}</span> of <span className="tabular">{c.lessons.length}</span> lesson{c.lessons.length === 1 ? "" : "s"} done
                      </>
                    ) : (
                      "Enroll to track progress, take quizzes and earn this course's credentials."
                    )}
                  </p>
                </div>
              </div>
            </Cover>
          </div>
        </div>
      </header>

      {c.outdated_enrollment ? (
        <div className="mt-6">
          <InlineNotice tone="info" title="This course has been updated since you enrolled">
            Your completed lessons are kept. Revisit changed lessons to stay current.
          </InlineNotice>
        </div>
      ) : null}

      {/* ------------------------------------------------------------------ body */}
      <div className="mt-6 grid grid-cols-1 gap-x-12 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0">
          {c.description_html ? (
            <Section eyebrow="Overview" title="About this course">
              <Prose html={c.description_html} className="max-w-[68ch]" />
            </Section>
          ) : null}

          {c.prerequisites.length ? (
            <Section eyebrow="Before you start" title="Prerequisites">
              <ul className="max-w-[68ch] divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
                {c.prerequisites.map((p) => (
                  <li key={p} className="flex items-start gap-3 px-4 py-3 text-sm text-fg">
                    <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent-strong" />
                    {p}
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          <Section
            id="syllabus"
            eyebrow={`Syllabus · ${c.lessons.length} lesson${c.lessons.length === 1 ? "" : "s"} · ${formatMinutes(c.estimated_minutes)}`}
            title="Lessons"
            description={c.progress.enrolled ? `${c.progress.percent}% complete` : "Enroll to track your progress through each lesson."}
          >
            <CourseOutline courseSlug={c.slug} lessons={c.lessons} showProgress={c.progress.enrolled} />
          </Section>
        </div>

        <aside aria-label="Course details" className="min-w-0 lg:pt-7">
          <div className="space-y-4 lg:sticky lg:top-24">
            {c.issues_certificate || c.badge ? (
              <section aria-labelledby="earn-heading" className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card">
                <h2 id="earn-heading" className="text-eyebrow text-subtle">On completion</h2>
                <div className="mt-4 space-y-4">
                  {c.issues_certificate ? (
                    <EarnableBadge
                      name="Verifiable certificate"
                      earned={Boolean(c.certificate_public_id)}
                      description={
                        c.certificate_public_id
                          ? "Issued — anyone can verify it from its public page."
                          : "Issued automatically when you finish every lesson, with a public verification page."
                      }
                    />
                  ) : null}
                  {c.badge ? <EarnableBadge name={c.badge.name} color={c.badge.color} description={c.badge.description} /> : null}
                </div>
              </section>
            ) : null}

            <section aria-labelledby="facts-heading" className="rounded-[var(--radius-lg)] border border-border bg-surface p-5">
              <h2 id="facts-heading" className="text-eyebrow text-subtle">Course facts</h2>
              {/* Level, category and duration are already in the hero; this card keeps the record details. */}
              <dl className="mt-3 divide-y divide-border text-sm">
                {c.published_at ? (
                  <div className="flex items-center justify-between gap-3 py-2.5">
                    <dt className="text-muted">Published</dt>
                    <dd className="tabular text-fg">{formatDate(c.published_at)}</dd>
                  </div>
                ) : null}
                <div className="flex items-center justify-between gap-3 py-2.5">
                  <dt className="text-muted">Version</dt>
                  <dd className="tabular font-mono text-[13px] text-fg">v{c.version}</dd>
                </div>
              </dl>
              {c.tags.length ? (
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border pt-4">
                  {c.tags.map((t) => (
                    <span key={t} className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-muted">{t}</span>
                  ))}
                </div>
              ) : null}
            </section>

            {enrolled ? (
              <div className="rounded-[var(--radius-lg)] border border-dashed border-border-strong px-5 py-4">
                {completed ? (
                  <p className="text-xs leading-relaxed text-subtle">Completed courses stay on your record, so you can&apos;t leave this course.</p>
                ) : (
                  <ConfirmDialog
                    trigger={
                      <Button variant="ghost" size="sm" icon={<LogOut className="h-4 w-4" aria-hidden />} className="w-full">
                        Leave course
                      </Button>
                    }
                    title="Leave this course?"
                    description="Your progress in this course will be removed. You can enroll again at any time."
                    confirmLabel="Leave course"
                    onConfirm={() => leave.mutateAsync().then(() => undefined, () => undefined)}
                  />
                )}
              </div>
            ) : null}
          </div>
        </aside>
      </div>
    </Container>
  );
}

export default function CoursePage() {
  const { slug } = useParams<{ slug: string }>();
  const course = useQuery({ queryKey: qk.course(slug), queryFn: () => get<CourseDetail>(`/learn/courses/${slug}`) });

  if (course.isPending) return <CourseSkeleton />;
  if (course.isError) {
    return (
      <Container className="py-12">
        {course.error instanceof ApiError && course.error.status === 404 ? (
          <NotFoundState what="course" />
        ) : (
          <ErrorState error={course.error} onRetry={() => course.refetch()} />
        )}
      </Container>
    );
  }

  return <CourseView course={course.data} />;
}
