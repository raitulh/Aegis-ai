"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, ChevronRight, Eye, Pencil, Plus, Users } from "lucide-react";
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

function CourseRow({ c }: { c: CourseCard }) {
  return (
    <li className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
      <Cover style={c.cover_style} className="hidden h-14 w-20 shrink-0 rounded-[var(--radius-md)] sm:block" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`/learn/authoring/${c.slug}`} className="font-medium text-fg hover:text-accent-strong">
            {c.title}
          </Link>
          <StatusBadge status={c.status} />
          {c.visibility === "org" ? <Badge tone="info">Members only</Badge> : null}
          {c.is_demo ? <DemoBadge /> : null}
        </div>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <DifficultyBadge difficulty={c.difficulty} />
          <span>{titleCase(c.category)}</span>
          <span className="inline-flex items-center gap-1"><BookOpen className="h-3.5 w-3.5" aria-hidden /> {c.lesson_count ?? 0} lessons</span>
          <span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" aria-hidden /> {compactNumber(c.enrollment_count)} learners</span>
          {c.org ? <span>{c.org.name}</span> : <span>Platform course</span>}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
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

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-muted">
        <Link href="/learn" className="hover:text-fg">Learn</Link>
        <ChevronRight className="mx-1 inline h-3.5 w-3.5" aria-hidden />
        <span className="text-fg" aria-current="page">Authoring</span>
      </nav>
      <PageHeader
        className="pt-4"
        title="Course authoring"
        description="Create and maintain courses: lessons, quizzes with server-side grading, and challenges linked to practice competitions."
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
        <h2 id="my-courses-heading" className="mb-3 text-lg font-semibold text-fg">Your courses</h2>
        {courses.isPending ? (
          <SkeletonRows rows={4} />
        ) : courses.isError ? (
          <ErrorState error={courses.error} onRetry={() => courses.refetch()} />
        ) : courses.data.length === 0 ? (
          <EmptyState
            title="No courses yet"
            description={canCreate ? "Create your first course — it stays a private draft until you publish it." : "Courses you can edit will appear here."}
            action={canCreate ? <Button icon={<Plus className="h-4 w-4" aria-hidden />} onClick={() => setOpen(true)}>New course</Button> : undefined}
          />
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
            {courses.data.map((c) => <CourseRow key={c.id} c={c} />)}
          </ul>
        )}
      </section>

      {canCreate && open ? <CreateCourseDialog open={open} onOpenChange={setOpen} isAdmin={isAdmin} managed={managed} /> : null}
    </Container>
  );
}
