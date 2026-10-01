"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, BookOpen, Building, ChevronRight, Clock, GraduationCap, LogOut, Pencil, Play, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { UserLink } from "@/components/domain/cards";
import { useAction } from "@/components/discussions/use-action";
import type { CourseDetail } from "@/components/learning/types";
import { CourseOutline, DifficultyBadge, EarnableBadge, formatMinutes } from "@/components/learning/ui";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
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
    <div role="status" aria-label="Loading course">
      <Skeleton className="h-40 w-full rounded-none" />
      <Container className="py-8">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="mt-3 h-4 w-1/2" />
        <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_320px]">
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
          </div>
          <Skeleton className="h-56 w-full" />
        </div>
      </Container>
    </div>
  );
}

function EnrollmentPanel({ course }: { course: CourseDetail }) {
  const me = useMe();
  const qc = useQueryClient();
  const slug = course.slug;
  const enrolled = course.progress.enrolled;
  const completed = course.progress.completed;
  const doneLessons = course.lessons.filter((l) => l.status === "completed").length;
  const firstLesson = course.lessons[0]?.slug ?? null;
  const resume = course.resume_lesson_slug ?? firstLesson;
  const published = course.status === "published";

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

  return (
    <Card className="p-5 lg:sticky lg:top-20">
      {enrolled ? (
        <div className="flex items-center gap-4">
          <ProgressRing value={course.progress.percent} size={60} stroke={5} />
          <div>
            <p className="font-semibold text-fg">{completed ? "Course completed" : "Your progress"}</p>
            <p className="text-sm text-muted">
              {doneLessons} of {course.lessons.length} lessons done
            </p>
          </div>
        </div>
      ) : (
        <div>
          <p className="font-semibold text-fg">Free course</p>
          <p className="mt-1 text-sm text-muted">Enroll to track progress, take quizzes and earn this course&apos;s credentials.</p>
        </div>
      )}

      <div className="mt-5 flex flex-col gap-2">
        {!published ? (
          <InlineNotice tone="info" title={course.status === "archived" ? "Archived course" : "Draft preview"}>
            {course.status === "archived"
              ? "This course is archived and no longer accepts enrollments."
              : "Only authors can see this course. Publish it to open enrollment."}
          </InlineNotice>
        ) : enrolled ? (
          resume ? (
            <LinkButton href={`/learn/${slug}/${resume}`} icon={<Play className="h-4 w-4" aria-hidden />}>
              {completed ? "Review course" : course.progress.percent > 0 ? "Continue learning" : "Start the course"}
            </LinkButton>
          ) : null
        ) : me.data ? (
          <Button onClick={() => enroll.mutate()} loading={enroll.isPending} icon={<GraduationCap className="h-4 w-4" aria-hidden />}>
            Enroll for free
          </Button>
        ) : me.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          <LinkButton href={`/login?next=${encodeURIComponent(`/learn/${slug}`)}`}>Sign in to enroll</LinkButton>
        )}

        {!enrolled && firstLesson && published ? (
          <LinkButton href={`/learn/${slug}/${firstLesson}`} variant="ghost">
            Preview the first lesson
          </LinkButton>
        ) : null}

        {course.certificate_public_id ? (
          <LinkButton href={`/verify/${course.certificate_public_id}`} variant="secondary" icon={<Award className="h-4 w-4" aria-hidden />}>
            View your certificate
          </LinkButton>
        ) : null}

        {course.can_author ? (
          <LinkButton href={`/learn/authoring/${slug}`} variant="outline" icon={<Pencil className="h-4 w-4" aria-hidden />}>
            Edit course
          </LinkButton>
        ) : null}
      </div>

      {course.issues_certificate || course.badge ? (
        <div className="mt-5 space-y-3 border-t border-border pt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-subtle">On completion</p>
          {course.issues_certificate ? (
            <EarnableBadge
              name="Verifiable certificate"
              description={
                course.certificate_public_id
                  ? "Issued — anyone can verify it from its public page."
                  : "Issued automatically when you finish every lesson, with a public verification page."
              }
            />
          ) : null}
          {course.badge ? <EarnableBadge name={course.badge.name} color={course.badge.color} description={course.badge.description} /> : null}
        </div>
      ) : null}

      {enrolled ? (
        <div className="mt-5 border-t border-border pt-4">
          {completed ? (
            <p className="text-xs text-subtle">Completed courses stay on your record, so you can&apos;t leave this course.</p>
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
    </Card>
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

  const c = course.data;
  const totalMinutes = c.estimated_minutes;
  return (
    <>
      <Cover style={c.cover_style} className="h-36 border-b border-border sm:h-44">
        <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/40 to-transparent" aria-hidden />
      </Cover>
      <Container className="relative -mt-16 pb-16">
        <nav aria-label="Breadcrumb" className="mb-3 flex items-center gap-1 text-sm text-muted">
          <Link href="/learn" className="hover:text-fg">Learn</Link>
          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          <span className="truncate text-fg" aria-current="page">{c.title}</span>
        </nav>
        <header className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <DifficultyBadge difficulty={c.difficulty} />
            <Badge tone="outline">{titleCase(c.category)}</Badge>
            {c.status !== "published" ? <StatusBadge status={c.status} /> : null}
            {c.visibility === "org" ? <Badge tone="info">Members only</Badge> : null}
            {c.is_demo ? <DemoBadge /> : null}
          </div>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{c.title}</h1>
          {c.summary ? <p className="mt-2 text-base text-muted">{c.summary}</p> : null}
          <ul className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-muted">
            <li className="inline-flex items-center gap-1.5">
              <Clock className="h-4 w-4" aria-hidden /> <span className="sr-only">Estimated time:</span>
              {formatMinutes(totalMinutes)}
            </li>
            <li className="inline-flex items-center gap-1.5">
              <BookOpen className="h-4 w-4" aria-hidden /> {c.lessons.length} lesson{c.lessons.length === 1 ? "" : "s"}
            </li>
            <li className="inline-flex items-center gap-1.5">
              <Users className="h-4 w-4" aria-hidden /> {compactNumber(c.enrollment_count)} enrolled
            </li>
            {c.org ? (
              <li className="inline-flex items-center gap-1.5">
                <Building className="h-4 w-4" aria-hidden />
                <Link href={`/orgs/${c.org.slug}`} className="hover:text-fg">{c.org.name}</Link>
              </li>
            ) : null}
            {c.author ? (
              <li className="inline-flex items-center gap-1.5">
                <span>by</span> <UserLink user={c.author} size={20} />
              </li>
            ) : null}
          </ul>
          {c.published_at ? (
            <p className="mt-2 text-xs text-subtle">
              Published {formatDate(c.published_at)}
              {c.version > 1 ? ` · version ${c.version}` : ""}
            </p>
          ) : null}
        </header>

        {c.outdated_enrollment ? (
          <div className="mt-6 max-w-3xl">
            <InlineNotice tone="info" title="This course has been updated since you enrolled">
              Your completed lessons are kept. Revisit changed lessons to stay current.
            </InlineNotice>
          </div>
        ) : null}

        <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0">
            {c.description_html ? (
              <Section title="About this course" className="pt-0">
                <Prose html={c.description_html} />
              </Section>
            ) : null}

            {c.prerequisites.length ? (
              <Section title="Prerequisites">
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
                  {c.prerequisites.map((p) => <li key={p}>{p}</li>)}
                </ul>
              </Section>
            ) : null}

            <Section
              title="Lessons"
              description={c.progress.enrolled ? `${c.progress.percent}% complete` : "Enroll to track your progress through each lesson."}
            >
              <CourseOutline courseSlug={c.slug} lessons={c.lessons} showProgress={c.progress.enrolled} />
            </Section>
          </div>
          <aside aria-label="Enrollment" className="order-first lg:order-none">
            <EnrollmentPanel course={c} />
          </aside>
        </div>
      </Container>
    </>
  );
}
