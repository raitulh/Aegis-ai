"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronRight, Eye, Rocket } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type KeyboardEvent } from "react";

import { useAction } from "@/components/discussions/use-action";
import { CourseDetailsForm } from "@/components/learning/course-details-form";
import { LessonsEditor } from "@/components/learning/lessons-editor";
import type { AuthoringCourse } from "@/components/learning/types";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Stat } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Container, PageHeader } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, PermissionDenied, Skeleton, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

const TABS = [
  { id: "lessons", label: "Lessons" },
  { id: "details", label: "Course details" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const PUBLISH_HINT: Record<string, string> = {
  course_empty: "Add a lesson in the Lessons tab, then publish again.",
  quiz_empty: "Open that quiz lesson in the Lessons tab and add at least one question.",
};

function EditorSkeleton() {
  return (
    <Container className="py-8">
      <div role="status" aria-label="Loading course editor">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="mt-6 h-8 w-1/2" />
        <div className="mt-8 grid grid-cols-2 gap-4 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
        </div>
        <div className="mt-8 space-y-3">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
        </div>
      </div>
    </Container>
  );
}

export default function CourseEditorPage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const qc = useQueryClient();
  const key = ["courses", slug, "authoring"] as const;
  const course = useQuery({
    queryKey: key,
    queryFn: () => get<AuthoringCourse>(`/learn/courses/${slug}/authoring`),
    enabled: Boolean(me.data),
  });
  const [tab, setTab] = useState<TabId>("lessons");
  const [publishError, setPublishError] = useState<{ message: string; code: string } | null>(null);

  const setCourse = (d: AuthoringCourse) => {
    qc.setQueryData(key, d);
    // The learner-facing view of this course is now stale (exact: don't refetch the authoring view itself).
    void qc.invalidateQueries({ queryKey: qk.course(slug), exact: true });
    void qc.invalidateQueries({ queryKey: ["courses", slug, "lessons"] });
  };
  const related = [qk.course(slug), ["courses", "list"], ["learn"]] as const;

  const publish = useAction(() => post<AuthoringCourse>(`/learn/courses/${slug}/publish`), {
    success: (d) => (d.version > 1 ? `Published version ${d.version}` : "Course published"),
    invalidate: related,
    onSuccess: (d) => {
      setCourse(d);
      setPublishError(null);
    },
    onError: (e) => setPublishError(e.status === 409 ? { message: e.message, code: e.code } : null),
  });
  const archive = useAction(() => post<AuthoringCourse>(`/learn/courses/${slug}/archive`), {
    success: "Course archived",
    invalidate: related,
    onSuccess: (d) => setCourse(d),
  });

  if (me.isPending || !me.data) return <Spinner />;
  if (course.isPending) return <EditorSkeleton />;
  if (course.isError) {
    const err = course.error;
    return (
      <Container className="py-12">
        {err instanceof ApiError && err.status === 403 ? (
          <PermissionDenied message="Only this course's author, managers of its organization and platform staff can edit it." />
        ) : err instanceof ApiError && err.status === 404 ? (
          <NotFoundState what="course" />
        ) : (
          <ErrorState error={err} onRetry={() => course.refetch()} />
        )}
      </Container>
    );
  }

  const c = course.data;
  const published = c.status === "published";
  const completionRate = c.stats.enrolled ? Math.round((100 * c.stats.completed) / c.stats.enrolled) : null;

  function onTabKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const idx = TABS.findIndex((t) => t.id === tab);
    const nextIdx =
      e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (idx + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    setTab(TABS[nextIdx].id);
    document.getElementById(`editor-tab-${TABS[nextIdx].id}`)?.focus();
  }

  const publishButton = <Button icon={<Rocket className="h-4 w-4" aria-hidden />} loading={publish.isPending}>{published ? "Publish update" : "Publish"}</Button>;

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 pt-6 text-sm text-muted">
        <Link href="/learn" className="hover:text-fg">Learn</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <Link href="/learn/authoring" className="hover:text-fg">Authoring</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <span className="max-w-[18rem] truncate text-fg" aria-current="page">{c.title}</span>
      </nav>

      <PageHeader
        className="pt-4"
        eyebrow="Course editor"
        title={c.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={c.status} />
            <Badge tone="outline">Version {c.version}</Badge>
            {c.visibility === "org" ? <Badge tone="info">Members only</Badge> : null}
            {c.org ? <span className="text-sm">{c.org.name}</span> : <span className="text-sm">Platform course</span>}
            {c.is_demo ? <DemoBadge /> : null}
          </span>
        }
        actions={
          <>
            <LinkButton href={`/learn/${slug}`} variant="secondary" icon={<Eye className="h-4 w-4" aria-hidden />}>
              {published ? "View" : "Preview"}
            </LinkButton>
            {published ? (
              <ConfirmDialog
                trigger={publishButton}
                tone="primary"
                title={`Publish version ${c.version + 1}?`}
                description="Your saved edits go live. Learners enrolled on an earlier version keep their progress and see a notice that the course was updated."
                confirmLabel="Publish update"
                onConfirm={() => publish.mutateAsync().then(() => undefined, () => undefined)}
              />
            ) : (
              <Button icon={<Rocket className="h-4 w-4" aria-hidden />} loading={publish.isPending} onClick={() => publish.mutate()}>
                {c.status === "archived" ? "Republish" : "Publish"}
              </Button>
            )}
            {c.status !== "archived" ? (
              <ConfirmDialog
                trigger={<Button variant="ghost" icon={<Archive className="h-4 w-4" aria-hidden />}>Archive</Button>}
                title="Archive this course?"
                description="It will be hidden from the catalog and search. Enrolled learners keep their progress, badges and certificates. You can publish it again later."
                confirmLabel="Archive course"
                onConfirm={() => archive.mutateAsync().then(() => undefined, () => undefined)}
              />
            ) : null}
          </>
        }
      />

      <div className="space-y-3">
        {publishError ? (
          <InlineNotice tone="warning" title="This course can't be published yet">
            {publishError.message} {PUBLISH_HINT[publishError.code] ?? ""}
          </InlineNotice>
        ) : null}
        {c.status === "draft" ? (
          <InlineNotice tone="info" title="Draft">
            Only authors can see this course. Publish it when the lessons are ready — every quiz needs at least one question.
          </InlineNotice>
        ) : c.status === "archived" ? (
          <InlineNotice tone="warning" title="Archived">
            This course is hidden from the catalog. Publish it again to restore it.
          </InlineNotice>
        ) : (
          <InlineNotice tone="success" title={`Published · version ${c.version}`}>
            Edits save immediately to the live course. Use “Publish update” to mark a new version so enrolled learners are told it changed.
          </InlineNotice>
        )}
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Enrolled" value={formatNumber(c.stats.enrolled)} />
        <Stat label="Completed" value={formatNumber(c.stats.completed)} hint={completionRate !== null ? `${completionRate}% completion rate` : undefined} />
        <Stat label="Lessons" value={formatNumber(c.lessons.length)} />
        <Stat label="Version" value={c.version} />
      </div>

      <div className="mt-8">
        <div role="tablist" aria-label="Editor sections" className="flex gap-1 overflow-x-auto border-b border-border">
          {TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                id={`editor-tab-${t.id}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`editor-panel-${t.id}`}
                tabIndex={active ? 0 : -1}
                onClick={() => setTab(t.id)}
                onKeyDown={onTabKey}
                className={cn(
                  "-mb-px shrink-0 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[var(--ring)]",
                  active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
                )}
              >
                {t.label}
                {t.id === "lessons" ? <span className="ml-1.5 rounded-full bg-surface-3 px-1.5 text-xs text-subtle">{c.lessons.length}</span> : null}
              </button>
            );
          })}
        </div>
        {/* Both panels stay mounted so switching tabs never loses unsaved edits. */}
        <div role="tabpanel" id="editor-panel-lessons" aria-labelledby="editor-tab-lessons" hidden={tab !== "lessons"} className="pt-6">
          <LessonsEditor course={c} onUpdate={setCourse} />
        </div>
        <div role="tabpanel" id="editor-panel-details" aria-labelledby="editor-tab-details" hidden={tab !== "details"} className="pt-6">
          <CourseDetailsForm course={c} onSaved={setCourse} />
        </div>
      </div>
    </Container>
  );
}
