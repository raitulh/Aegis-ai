"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Award, Check, ChevronRight, Clock, GraduationCap, ListTree, PartyPopper } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useCallback, useState } from "react";

import { useAction } from "@/components/discussions/use-action";
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
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

function LessonSkeleton() {
  return (
    <Container className="py-8">
      <div className="grid gap-8 lg:grid-cols-[280px_minmax(0,1fr)]" role="status" aria-label="Loading lesson">
        <div className="hidden space-y-2 lg:block">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-11 w-full" />)}
        </div>
        <div>
          <Skeleton className="h-4 w-40" />
          <Skeleton className="mt-4 h-8 w-2/3" />
          <div className="mt-8 space-y-3">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-4 w-full" />)}
          </div>
        </div>
      </div>
    </Container>
  );
}

function OutlineSidebar({ course, currentSlug }: { course: CourseDetail | undefined; currentSlug: string }) {
  if (!course) return <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-11 w-full" />)}</div>;
  return (
    <div className="space-y-3">
      <div>
        <Link href={`/learn/${course.slug}`} className="text-sm font-semibold text-fg hover:text-accent-strong">
          {course.title}
        </Link>
        {course.progress.enrolled ? (
          <div className="mt-2 space-y-1">
            <ProgressBar value={course.progress.percent} label="Course progress" />
            <p className="text-xs text-muted">{course.progress.percent}% complete</p>
          </div>
        ) : null}
      </div>
      <nav aria-label="Course lessons">
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
  const complete = useAction(() => post<CompleteResult>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}/complete`), {
    success: "Lesson marked complete",
    onSuccess: (r) => onProgress(r),
  });
  const done = lesson.progress.status === "completed";
  return (
    <div className="flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted">
        {done ? "You've completed this lesson." : "Finished reading? Mark the lesson complete to update your progress."}
      </p>
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
    <Container className="py-6 pb-16">
      <nav aria-label="Breadcrumb" className="mb-4 flex flex-wrap items-center gap-1 text-sm text-muted">
        <Link href="/learn" className="hover:text-fg">Learn</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <Link href={`/learn/${slug}`} className="max-w-[16rem] truncate hover:text-fg">{l.course.title}</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <span className="text-fg" aria-current="page">Lesson {l.position} of {l.total}</span>
      </nav>

      <div className="grid gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <div className="sticky top-20 max-h-[calc(100dvh-6rem)] overflow-y-auto pb-4">
            <OutlineSidebar course={c} currentSlug={l.slug} />
          </div>
        </aside>

        <div className="min-w-0">
          <details className="mb-6 rounded-[var(--radius-lg)] border border-border bg-surface lg:hidden">
            <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm font-medium text-fg">
              <ListTree className="h-4 w-4" aria-hidden /> Course outline
              {c?.progress.enrolled ? <span className="ml-auto text-xs text-muted">{c.progress.percent}% complete</span> : null}
            </summary>
            <div className="border-t border-border p-3">
              <OutlineSidebar course={c} currentSlug={l.slug} />
            </div>
          </details>

          {c?.progress.completed ? (
            <div className="mb-6">
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

          <article aria-labelledby="lesson-title">
            <header className="mb-6">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="outline" icon={<LessonKindIcon kind={l.kind} className="h-3 w-3" />}>{KIND_LABEL[l.kind]}</Badge>
                <span className="inline-flex items-center gap-1 text-xs text-muted">
                  <Clock className="h-3.5 w-3.5" aria-hidden /> {formatMinutes(l.estimated_minutes)}
                </span>
                {l.enrolled ? <LessonStatusBadge status={l.progress.status} /> : null}
                {c?.is_demo ? <DemoBadge /> : null}
              </div>
              <h1 id="lesson-title" className="mt-3 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{l.title}</h1>
            </header>

            {l.kind === "article" && gate ? <div className="mb-6">{gate}</div> : null}

            {l.body_html ? <Prose html={l.body_html} className="max-w-3xl" /> : null}

            <div className="mt-8 max-w-3xl">
              {l.kind === "article" ? (
                l.enrolled ? <ArticleCompletion courseSlug={slug} lesson={l} onProgress={onProgress} /> : null
              ) : l.kind === "quiz" ? (
                <section aria-labelledby="quiz-heading">
                  <h2 id="quiz-heading" className="mb-3 text-lg font-semibold text-fg">Quiz</h2>
                  <QuizPanel key={l.id} courseSlug={slug} lesson={l} canSubmit={canSubmit} gate={gate} onProgress={onProgress} />
                </section>
              ) : (
                <ChallengePanel key={l.id} courseSlug={slug} lesson={l} canSubmit={canSubmit} gate={gate} onProgress={onProgress} />
              )}
            </div>
          </article>

          <nav aria-label="Lesson navigation" className="mt-10 grid max-w-3xl gap-3 border-t border-border pt-6 sm:grid-cols-2">
            {l.prev ? (
              <Link
                href={`/learn/${slug}/${l.prev.slug}`}
                className="group flex flex-col rounded-[var(--radius-lg)] border border-border p-4 hover:border-border-strong hover:bg-surface-2"
              >
                <span className="inline-flex items-center gap-1 text-xs text-subtle"><ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Previous</span>
                <span className="mt-1 truncate font-medium text-fg group-hover:text-accent-strong">{l.prev.title}</span>
              </Link>
            ) : (
              <span className="hidden sm:block" />
            )}
            {l.next ? (
              <Link
                href={`/learn/${slug}/${l.next.slug}`}
                className="group flex flex-col items-end rounded-[var(--radius-lg)] border border-border p-4 text-right hover:border-border-strong hover:bg-surface-2"
              >
                <span className="inline-flex items-center gap-1 text-xs text-subtle">Next <ArrowRight className="h-3.5 w-3.5" aria-hidden /></span>
                <span className="mt-1 max-w-full truncate font-medium text-fg group-hover:text-accent-strong">{l.next.title}</span>
              </Link>
            ) : (
              <Link
                href={`/learn/${slug}`}
                className="group flex flex-col items-end rounded-[var(--radius-lg)] border border-border p-4 text-right hover:border-border-strong hover:bg-surface-2"
              >
                <span className="text-xs text-subtle">Last lesson</span>
                <span className="mt-1 font-medium text-fg group-hover:text-accent-strong">Back to course overview</span>
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
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-accent-soft">
            <PartyPopper className="h-8 w-8 text-accent-strong" aria-hidden />
          </span>
          <p className="text-sm text-muted">
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
