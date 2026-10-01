"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, ChevronRight, Eye, PenSquare, Pencil, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { friendlyError } from "@/components/discussions/use-action";
import { DIFFICULTIES, MANAGER_ROLES, type AuthoringCourse, type OrgMembership } from "@/components/learning/types";
import { DifficultyBadge } from "@/components/learning/ui";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Cover } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { compactNumber, titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import type { CourseCard } from "@/lib/types";

const AUTHORING_EXPLAINER =
  "Course authoring is available to organization managers (owners, admins and managers of a university, club or company on DataBattles) and to platform staff.";

function CreateCourseDialog({
  open,
  onOpenChange,
  isAdmin,
  managed,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  isAdmin: boolean;
  managed: OrgMembership[];
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const categories = useQuery({ queryKey: ["learn", "categories"], queryFn: () => get<string[]>("/learn/categories"), staleTime: 10 * 60_000 });
  const [form, setForm] = useState(() => ({
    title: "",
    category: "machine-learning",
    difficulty: "beginner",
    summary: "",
    org_id: isAdmin ? "" : managed[0]?.org.id ?? "",
  }));
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const titleError = form.title.trim().length > 0 && form.title.trim().length < 3 ? "Enter at least 3 characters." : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (form.title.trim().length < 3) {
      setError(new ApiError(422, "validation_error", "Some fields are invalid.", { fields: { title: "Enter at least 3 characters." } }, null));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await post<AuthoringCourse>("/learn/courses", {
        title: form.title.trim(),
        category: form.category,
        difficulty: form.difficulty,
        summary: form.summary.trim() || undefined,
        org_id: form.org_id || undefined,
      });
      await qc.invalidateQueries({ queryKey: ["learn", "authoring"] });
      qc.setQueryData(["courses", created.slug, "authoring"], created);
      router.push(`/learn/authoring/${created.slug}`);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "error", friendlyError(err), null, null));
    } finally {
      setBusy(false);
    }
  }

  const fields = error?.fields ?? {};
  const generic = error && !Object.keys(fields).length ? error : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setError(null);
      }}
      title="New course"
      description="Start with the basics — you can add a description, lessons, quizzes and challenges next. New courses start as private drafts."
      size="md"
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Title" required error={fields.title ?? titleError}>
          {(p) => <Input {...p} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={140} autoFocus />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Category" error={fields.category}>
            {(p) => (
              <Select {...p} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {(categories.data ?? [form.category]).map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Difficulty" error={fields.difficulty}>
            {(p) => (
              <Select {...p} value={form.difficulty} onChange={(e) => setForm({ ...form, difficulty: e.target.value })}>
                {DIFFICULTIES.map((d) => <option key={d} value={d}>{titleCase(d)}</option>)}
              </Select>
            )}
          </Field>
        </div>
        <Field label="Summary" hint="One or two sentences shown on the course card." error={fields.summary}>
          {(p) => <Textarea {...p} rows={2} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} maxLength={300} />}
        </Field>
        <Field
          label="Organization"
          hint={isAdmin ? "Platform courses have no organization." : "Courses are published on behalf of an organization you manage."}
          error={fields.org_id}
        >
          {(p) => (
            <Select {...p} value={form.org_id} onChange={(e) => setForm({ ...form, org_id: e.target.value })}>
              {isAdmin ? <option value="">No organization (platform course)</option> : null}
              {managed.map((m) => (
                <option key={m.org.id} value={m.org.id}>
                  {m.org.name} · {titleCase(m.role)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {generic ? (
          generic.status === 403 && generic.code !== "email_not_verified" ? (
            <InlineNotice tone="danger" title="You can't create courses here">
              {generic.message} {AUTHORING_EXPLAINER}
            </InlineNotice>
          ) : (
            <FormError message={friendlyError(generic)} />
          )
        ) : null}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="submit" loading={busy} disabled={!form.title.trim()}>Create draft</Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Shared column template so the header row and every course row line up on desktop. */
const ROW_COLS = "lg:grid-cols-[minmax(0,1fr)_8.5rem_5.5rem_6rem_minmax(0,11rem)_10.5rem]";

function CourseRow({ c }: { c: CourseCard }) {
  return (
    <li className={`grid grid-cols-1 gap-3 px-4 py-4 transition-colors duration-200 hover:bg-surface-2/60 sm:px-5 lg:items-center lg:gap-4 ${ROW_COLS}`}>
      <div className="flex min-w-0 items-center gap-3.5">
        <Cover style={c.cover_style} className="hidden h-11 w-16 shrink-0 rounded-[var(--radius-md)] border border-border sm:block" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/learn/authoring/${c.slug}`} className="min-w-0 truncate font-medium tracking-[-0.01em] text-fg transition-colors hover:text-accent-strong">
              {c.title}
            </Link>
            <span className="lg:hidden"><StatusBadge status={c.status} /></span>
            {c.visibility === "org" ? <Badge tone="info">Members only</Badge> : null}
            {c.is_demo ? <DemoBadge /> : null}
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <DifficultyBadge difficulty={c.difficulty} />
            <span>{titleCase(c.category)}</span>
            <span className="inline-flex items-center gap-1 lg:hidden"><BookOpen className="h-3.5 w-3.5" aria-hidden /> <span className="tabular">{c.lesson_count ?? 0}</span> lesson{c.lesson_count === 1 ? "" : "s"}</span>
            <span className="inline-flex items-center gap-1 lg:hidden"><Users className="h-3.5 w-3.5" aria-hidden /> <span className="tabular">{compactNumber(c.enrollment_count)}</span> learner{c.enrollment_count === 1 ? "" : "s"}</span>
            <span className="lg:hidden">{c.org ? c.org.name : "Platform course"}</span>
          </p>
        </div>
      </div>
      <div className="hidden lg:block"><StatusBadge status={c.status} /></div>
      <div className="tabular hidden text-sm text-fg lg:block">
        {c.lesson_count ?? 0}
        <span className="sr-only"> lesson{c.lesson_count === 1 ? "" : "s"}</span>
      </div>
      <div className="tabular hidden text-sm text-fg lg:block">
        {compactNumber(c.enrollment_count)}
        <span className="sr-only"> learner{c.enrollment_count === 1 ? "" : "s"}</span>
      </div>
      {/* Wraps to two lines so long organization names stay readable; the title covers anything longer. */}
      <div className="hidden min-w-0 text-sm leading-snug text-muted lg:block">
        <span className="line-clamp-2 break-words" title={c.org?.name}>{c.org ? c.org.name : "Platform course"}</span>
      </div>
      <div className="flex shrink-0 gap-2 lg:justify-end">
        {c.status === "published" ? (
          <LinkButton href={`/learn/${c.slug}`} variant="ghost" size="sm" icon={<Eye className="h-4 w-4" aria-hidden />} aria-label={`View ${c.title}`}>
            View
          </LinkButton>
        ) : null}
        <LinkButton href={`/learn/authoring/${c.slug}`} variant="secondary" size="sm" icon={<Pencil className="h-4 w-4" aria-hidden />} aria-label={`Edit ${c.title}`}>
          Edit
        </LinkButton>
      </div>
    </li>
  );
}

export default function AuthoringHomePage() {
  const me = useRequireAuth();
  const signedIn = Boolean(me.data);
  const courses = useQuery({ queryKey: ["learn", "authoring"], queryFn: () => get<CourseCard[]>("/learn/authoring"), enabled: signedIn });
  const memberships = useQuery({
    queryKey: ["orgs", "me", "memberships"],
    queryFn: () => get<OrgMembership[]>("/orgs/me/memberships"),
    enabled: signedIn,
  });
  const [open, setOpen] = useState(false);

  const isAdmin = Boolean(me.data?.platform_roles.includes("platform_admin"));
  const managed = useMemo(
    () => (memberships.data ?? []).filter((m) => MANAGER_ROLES.includes(m.role) && m.status === "active"),
    [memberships.data],
  );
  const canCreate = isAdmin || managed.length > 0;
  const permissionsKnown = isAdmin || memberships.isSuccess || memberships.isError;

  if (me.isPending || !me.data) return <Spinner />;

  const list = courses.data ?? [];
  const publishedCount = list.filter((c) => c.status === "published").length;
  const draftCount = list.filter((c) => c.status === "draft").length;
  const learners = list.reduce((sum, c) => sum + c.enrollment_count, 0);

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <ol className="flex min-w-0 items-center gap-1.5">
          <li><Link href="/learn" className="transition-colors hover:text-fg">Learn</Link></li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="text-muted" aria-current="page">Authoring</li>
        </ol>
      </nav>
      <PageHeader
        className="pt-4"
        eyebrow="Learn · Authoring"
        icon={<PenSquare />}
        title="Course authoring"
        description="Create and maintain courses: lessons, quizzes with server-side grading, and challenges linked to practice competitions."
        meta={
          courses.data ? (
            <>
              <span><span className="tabular font-medium text-fg">{list.length}</span> course{list.length === 1 ? "" : "s"}</span>
              <span><span className="tabular font-medium text-fg">{publishedCount}</span> published</span>
              <span><span className="tabular font-medium text-fg">{draftCount}</span> draft{draftCount === 1 ? "" : "s"}</span>
              <span><span className="tabular font-medium text-fg">{compactNumber(learners)}</span> learners enrolled</span>
            </>
          ) : null
        }
        actions={
          canCreate ? (
            <Button icon={<Plus className="h-4 w-4" aria-hidden />} onClick={() => setOpen(true)}>
              New course
            </Button>
          ) : null
        }
      />

      {permissionsKnown && !canCreate ? (
        <div className="mb-6">
          <InlineNotice tone="info" title="You can't create new courses">
            {AUTHORING_EXPLAINER} Ask an owner or admin of your organization to give you a manager role. You can still edit courses you&apos;ve
            authored before.
          </InlineNotice>
        </div>
      ) : null}
      {memberships.isError && !isAdmin ? (
        <div className="mb-6">
          <InlineNotice tone="warning">We couldn&apos;t load your organization roles, so creating courses may be unavailable. Try refreshing the page.</InlineNotice>
        </div>
      ) : null}

      <section aria-labelledby="my-courses-heading">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <p className="text-eyebrow text-subtle">Workspace</p>
            <h2 id="my-courses-heading" className="mt-1.5 text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">Your courses</h2>
          </div>
        </div>
        {courses.isPending ? (
          <SkeletonRows rows={4} />
        ) : courses.isError ? (
          <ErrorState error={courses.error} onRetry={() => courses.refetch()} />
        ) : courses.data.length === 0 ? (
          <EmptyState
            icon={<PenSquare />}
            title="No courses yet"
            description={canCreate ? "Create your first course — it stays a private draft until you publish it." : "Courses you can edit will appear here."}
            action={
              canCreate ? (
                <Button icon={<Plus className="h-4 w-4" aria-hidden />} onClick={() => setOpen(true)}>New course</Button>
              ) : (
                <LinkButton href="/learn" variant="secondary">Browse courses</LinkButton>
              )
            }
          />
        ) : (
          <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
            <div aria-hidden className={`hidden gap-4 border-b border-border bg-bg-elevated/60 px-5 py-2.5 text-eyebrow text-subtle lg:grid ${ROW_COLS}`}>
              <span>Course</span>
              <span>Status</span>
              <span>Lessons</span>
              <span>Learners</span>
              <span>Organization</span>
              <span className="text-right">Actions</span>
            </div>
            <ul className="divide-y divide-border">
              {courses.data.map((c) => <CourseRow key={c.id} c={c} />)}
            </ul>
          </div>
        )}
      </section>

      {canCreate && open ? <CreateCourseDialog open={open} onOpenChange={setOpen} isAdmin={isAdmin} managed={managed} /> : null}
    </Container>
  );
}
