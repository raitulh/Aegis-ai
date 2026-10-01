"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Award, BookOpenCheck, Check, ChevronDown, ChevronRight, Clock, GraduationCap, ListTree } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useCallback, useState } from "react";

import { useAction } from "@/components/discussions/use-action";
import { SuccessMark } from "@/components/learning/celebrate";
import { ChallengePanel } from "@/components/learning/challenge";
import { QuizPanel } from "@/components/learning/quiz";
import type { CompleteResult, CourseDetail, LessonDetail, ProgressResult } from "@/components/learning/types";
import { CourseOutline, KIND_LABEL, LessonKindIcon, LessonStatusBadge, formatMinutes } from "@/components/learning/ui";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Prose } from "@/components/ui/markdown";
import { ProgressBar } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, Skeleton } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

const pad = (n: number) => String(n).padStart(2, "0");

function LessonSkeleton() {
  return (
    <Container className="pb-16">
      <Skeleton className="mt-6 h-4 w-56" />
      <div className="mt-5 grid gap-10 lg:grid-cols-[17.5rem_minmax(0,1fr)]" role="status" aria-label="Loading lesson">
        <div className="hidden rounded-[var(--radius-xl)] border border-border bg-surface p-4 lg:block">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="mt-2 h-4 w-40" />
          <Skeleton className="mt-4 h-1.5 w-full rounded-full" />
          <div className="mt-5 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3">
                <Skeleton className="h-6 w-6 shrink-0 rounded-full" />
                <Skeleton className="h-3.5 flex-1" />
              </div>
            ))}
          </div>
        </div>
        <div className="max-w-3xl">
          <Skeleton className="h-3 w-40" />
          <Skeleton className="mt-4 h-9 w-2/3" />
          <Skeleton className="mt-4 h-5 w-48 rounded-full" />
          <div className="mt-10 space-y-3">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className={cn("h-4", i % 3 === 2 ? "w-4/5" : "w-full")} />)}
          </div>
        </div>
      </div>
    </Container>
  );
}

function OutlineSidebar({ course, currentSlug }: { course: CourseDetail | undefined; currentSlug: string }) {
  if (!course) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading course outline">
        <Skeleton className="h-4 w-40" />
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-6 w-6 shrink-0 rounded-full" />
            <Skeleton className="h-3.5 flex-1" />
          </div>
        ))}
      </div>
    );
  }
  const done = course.lessons.filter((l) => l.status === "completed").length;
  return (
    <div className="space-y-4">
      <div className="px-2">
        <p className="text-eyebrow text-subtle">Course</p>
        <Link href={`/learn/${course.slug}`} className="mt-1 block font-semibold leading-snug tracking-[-0.01em] text-fg transition-colors hover:text-accent-strong">
          {course.title}
        </Link>
        {course.progress.enrolled ? (
          <div className="mt-3 space-y-1.5">
            <ProgressBar value={course.progress.percent} label="Course progress" />
            <p className="flex justify-between text-xs text-muted">
              <span>{course.progress.percent}% complete</span>
              <span className="tabular text-subtle">
                {done}/{course.lessons.length}
              </span>
            </p>
          </div>
        ) : null}
      </div>
      <nav aria-label="Course lessons" className="border-t border-border pt-2">
        <CourseOutline courseSlug={course.slug} lessons={course.lessons} currentSlug={currentSlug} showProgress={course.progress.enrolled} dense />
      </nav>
    </div>
  );
}

function EnrollGate({ courseSlug, signedIn, published }: { courseSlug: string; signedIn: boolean; published: boolean }) {
  const qc = useQueryClient();
  const pathname = usePathname();
  const enroll = useAction(() => post<CourseDetail>(`/learn/courses/${courseSlug}/enroll`), {
    success: "You're enrolled — your progress is now tracked.",
    invalidate: [["courses", courseSlug, "lessons"], ["courses", "list"], ["learn"]],
    onSuccess: (d) => {
      qc.setQueryData(qk.course(courseSlug), d);
    },
  });
  if (!published) {
    return <InlineNotice tone="info" title="Draft preview">Progress can&apos;t be recorded until the course is published.</InlineNotice>;
  }
  if (!signedIn) {
    return (
      <InlineNotice
        tone="info"
        title="Sign in to track your progress"
        action={<LinkButton size="sm" href={`/login?next=${encodeURIComponent(pathname)}`}>Sign in</LinkButton>}
      >
        You can read along, but quizzes and challenges need an account and an enrollment.
      </InlineNotice>
    );
  }
  return (
    <InlineNotice
      tone="info"
      title="Enroll to track your progress"
      action={
        <Button size="sm" onClick={() => enroll.mutate()} loading={enroll.isPending} icon={<GraduationCap className="h-4 w-4" aria-hidden />}>
          Enroll for free
        </Button>
      }
    >
      Completing lessons, submitting quizzes and checking challenges require an enrollment.
    </InlineNotice>
  );
}

function ArticleCompletion({
  courseSlug,
  lesson,
  onProgress,
}: {
  courseSlug: string;
  lesson: LessonDetail;
  onProgress: (r: ProgressResult) => Promise<void>;
}) {
  // True only after this visitor marks the lesson complete here, so the check animates for a real completion.
  const [justCompleted, setJustCompleted] = useState(false);
  const complete = useAction(() => post<CompleteResult>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}/complete`), {
    success: "Lesson marked complete",
    onSuccess: (r) => {
      setJustCompleted(true);
      return onProgress(r);
    },
  });
  const done = lesson.progress.status === "completed";
  return (
    <div
      className={cn(
        "rounded-[var(--radius-xl)] border p-5 transition-colors duration-500",
        done ? "border-[color-mix(in_oklab,var(--success)_30%,transparent)] bg-success-soft" : "border-border bg-surface surface-sheen shadow-card",
      )}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3.5">
          {done ? (
            <SuccessMark animate={justCompleted} />
          ) : (
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-border bg-bg-elevated text-accent-strong">
              <BookOpenCheck className="h-[18px] w-[18px]" aria-hidden />
            </span>
          )}
          <div className="min-w-0 self-center">
            {done ? (
              <>
                <p className="font-medium text-fg">Lesson complete</p>
                <p className="mt-0.5 text-sm text-muted">You&apos;ve completed this lesson.</p>
              </>
            ) : (
              <p className="text-sm leading-relaxed text-fg">Finished reading? Mark the lesson complete to update your progress.</p>
            )}
          </div>
        </div>
        <div className="shrink-0 pl-[3.375rem] sm:pl-0">
          {done ? (
            lesson.next ? (
              <LinkButton href={`/learn/${courseSlug}/${lesson.next.slug}`} icon={<ArrowRight className="h-4 w-4" aria-hidden />}>
                Next lesson
              </LinkButton>
            ) : (
              <Badge tone="success" icon={<Check className="h-3 w-3" aria-hidden />}>Completed</Badge>
            )
          ) : (
            <Button onClick={() => complete.mutate()} loading={complete.isPending} icon={<Check className="h-4 w-4" aria-hidden />}>
              Mark complete
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function LessonPage() {
  const { slug, lesson: lessonSlug } = useParams<{ slug: string; lesson: string }>();
  const me = useMe();
  const qc = useQueryClient();
  const lesson = useQuery({
    queryKey: qk.lesson(slug, lessonSlug),
    queryFn: () => get<LessonDetail>(`/learn/courses/${slug}/lessons/${lessonSlug}`),
  });
  const course = useQuery({ queryKey: qk.course(slug), queryFn: () => get<CourseDetail>(`/learn/courses/${slug}`) });
  const [celebrate, setCelebrate] = useState(false);

  const onProgress = useCallback(
    async (r: ProgressResult) => {
      const wasCompleted = qc.getQueryData<CourseDetail>(qk.course(slug))?.progress.completed ?? false;
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["courses", slug] }),
        qc.invalidateQueries({ queryKey: ["courses", "list"] }),
        qc.invalidateQueries({ queryKey: ["learn"] }),
      ]);
      if (r.course_completed && !wasCompleted) setCelebrate(true);
    },
    [qc, slug],
  );

  if (lesson.isPending) return <LessonSkeleton />;
  if (lesson.isError) {
    return (
      <Container className="py-12">
        {lesson.error instanceof ApiError && lesson.error.status === 404 ? (
          <NotFoundState what="lesson" />
        ) : (
          <ErrorState error={lesson.error} onRetry={() => lesson.refetch()} />
        )}
      </Container>
    );
  }

  const l = lesson.data;
  const c = course.data;
  const signedIn = Boolean(me.data);
  const published = c ? c.status === "published" : true;
  const canSubmit = signedIn && l.enrolled;
  const gate = !l.enrolled && !me.isPending ? <EnrollGate courseSlug={slug} signedIn={signedIn} published={published} /> : null;
  const certificateId = c?.certificate_public_id ?? null;

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <ol className="flex min-w-0 flex-wrap items-center gap-1.5">
          <li><Link href="/learn" className="transition-colors hover:text-fg">Learn</Link></li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="min-w-0">
            <Link href={`/learn/${slug}`} className="block max-w-[16rem] truncate transition-colors hover:text-fg">{l.course.title}</Link>
          </li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="text-muted" aria-current="page">Lesson {l.position} of {l.total}</li>
        </ol>
      </nav>

      <div className="mt-5 grid grid-cols-1 gap-8 lg:grid-cols-[17.5rem_minmax(0,1fr)] lg:gap-12">
        <aside className="hidden lg:block" aria-label="Course outline">
          <div className="sticky top-24 max-h-[calc(100dvh-7.5rem)] overflow-y-auto rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-3 pt-4 shadow-card">
            <OutlineSidebar course={c} currentSlug={l.slug} />
          </div>
        </aside>

        <div className="min-w-0">
          <details className="group mb-6 overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card lg:hidden">
            <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 px-4 py-3 text-sm font-medium text-fg [&::-webkit-details-marker]:hidden">
              <ListTree className="h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block">Course outline</span>
                <span className="block text-xs font-normal text-subtle">
                  Lesson {l.position} of {l.total}
                  {c?.progress.enrolled ? ` · ${c.progress.percent}% complete` : ""}
                </span>
              </span>
              <ChevronDown className="h-4 w-4 shrink-0 text-subtle transition-transform duration-300 group-open:rotate-180" aria-hidden />
            </summary>
            <div className="border-t border-border p-3">
              <OutlineSidebar course={c} currentSlug={l.slug} />
            </div>
          </details>

          {c?.progress.completed ? (
            <div className="mb-6 max-w-3xl">
              <InlineNotice
                tone="success"
                title="You've completed this course"
                action={
                  certificateId ? (
                    <LinkButton size="sm" variant="secondary" href={`/verify/${certificateId}`} icon={<Award className="h-4 w-4" aria-hidden />}>
                      View certificate
                    </LinkButton>
                  ) : null
                }
              >
                Feel free to revisit any lesson.
              </InlineNotice>
            </div>
          ) : null}

          <article aria-labelledby="lesson-title" className="max-w-3xl">
            <header className="mb-8 border-b border-border pb-6 animate-rise">
              <p className="flex items-center gap-2 text-eyebrow text-accent-strong">
                <LessonKindIcon kind={l.kind} className="h-3.5 w-3.5" />
                <span className="tabular">Lesson {pad(l.position)}</span>
                <span className="tabular text-subtle">/ {pad(l.total)}</span>
              </p>
              <h1 id="lesson-title" className="mt-3 text-title text-fg">{l.title}</h1>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Badge tone="outline" icon={<LessonKindIcon kind={l.kind} className="h-3 w-3" />}>{KIND_LABEL[l.kind]}</Badge>
                <span className="inline-flex items-center gap-1 text-xs text-muted">
                  <Clock className="h-3.5 w-3.5" aria-hidden /> <span className="tabular">{formatMinutes(l.estimated_minutes)}</span>
                </span>
                {l.enrolled ? <LessonStatusBadge status={l.progress.status} /> : null}
                {c?.is_demo ? <DemoBadge /> : null}
              </div>
            </header>

            {l.kind === "article" && gate ? <div className="mb-6">{gate}</div> : null}

            {l.body_html ? <Prose html={l.body_html} className="text-[16px] leading-[1.8]" /> : null}

            <div className="mt-10">
              {l.kind === "article" ? (
                l.enrolled ? <ArticleCompletion courseSlug={slug} lesson={l} onProgress={onProgress} /> : null
              ) : l.kind === "quiz" ? (
                <section aria-labelledby="quiz-heading">
                  <h2 id="quiz-heading" className="mb-4 flex items-center gap-2 text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">
                    <LessonKindIcon kind="quiz" className="h-[18px] w-[18px] text-accent-strong" />
                    Quiz
                  </h2>
                  <QuizPanel key={l.id} courseSlug={slug} lesson={l} canSubmit={canSubmit} gate={gate} onProgress={onProgress} />
                </section>
              ) : (
                <ChallengePanel key={l.id} courseSlug={slug} lesson={l} canSubmit={canSubmit} gate={gate} onProgress={onProgress} />
              )}
            </div>
          </article>

          <nav aria-label="Lesson navigation" className="mt-12 grid max-w-3xl grid-cols-1 gap-3 border-t border-border pt-6 sm:grid-cols-2">
            {l.prev ? (
              <Link
                href={`/learn/${slug}/${l.prev.slug}`}
                className="group lift flex min-w-0 flex-col rounded-[var(--radius-lg)] border border-border bg-surface p-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
              >
                <span className="inline-flex items-center gap-1.5 text-eyebrow text-subtle">
                  <ArrowLeft className="h-3.5 w-3.5 transition-transform duration-300 group-hover:-translate-x-0.5" aria-hidden /> Previous
                </span>
                <span className="mt-1.5 truncate font-medium text-fg transition-colors group-hover:text-accent-strong">{l.prev.title}</span>
              </Link>
            ) : (
              <span className="hidden sm:block" />
            )}
            {l.next ? (
              <Link
                href={`/learn/${slug}/${l.next.slug}`}
                className="group lift flex min-w-0 flex-col items-end rounded-[var(--radius-lg)] border border-border bg-surface p-4 text-right focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
              >
                <span className="inline-flex items-center gap-1.5 text-eyebrow text-subtle">
                  Next <ArrowRight className="h-3.5 w-3.5 transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden />
                </span>
                <span className="mt-1.5 max-w-full truncate font-medium text-fg transition-colors group-hover:text-accent-strong">{l.next.title}</span>
              </Link>
            ) : (
              <Link
                href={`/learn/${slug}`}
                className="group lift flex min-w-0 flex-col items-end rounded-[var(--radius-lg)] border border-border bg-surface p-4 text-right focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
              >
                <span className="text-eyebrow text-subtle">Last lesson</span>
                <span className="mt-1.5 font-medium text-fg transition-colors group-hover:text-accent-strong">Back to course overview</span>
              </Link>
            )}
          </nav>
        </div>
      </div>

      <Dialog
        open={celebrate}
        onOpenChange={setCelebrate}
        title="Course complete!"
        description={`Congratulations — you finished “${l.course.title}”.`}
        footer={
          <>
            <LinkButton href={`/learn/${slug}`} variant="secondary" onClick={() => setCelebrate(false)}>
              Course overview
            </LinkButton>
            {certificateId ? (
              <LinkButton href={`/verify/${certificateId}`} icon={<Award className="h-4 w-4" aria-hidden />} onClick={() => setCelebrate(false)}>
                View your certificate
              </LinkButton>
            ) : (
              <LinkButton href="/learn" onClick={() => setCelebrate(false)}>Find another course</LinkButton>
            )}
          </>
        }
      >
        <div className="relative flex flex-col items-center gap-4 overflow-hidden rounded-[var(--radius-lg)] border border-border bg-bg-elevated px-5 py-7 text-center">
          <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]" />
          <SuccessMark animate={celebrate} size="lg" className="relative" />
          <p className="relative max-w-sm text-sm leading-relaxed text-muted">
            {certificateId
              ? "Your verifiable certificate has been issued. Share its public page with anyone."
              : c?.issues_certificate
                ? "Your certificate is being issued — you'll find it on the course page shortly."
                : "Every lesson is done. Your achievement is recorded on your profile."}
            {c?.badge ? ` You also earned the “${c.badge.name}” badge.` : ""}
          </p>
        </div>
      </Dialog>
    </Container>
  );
}
