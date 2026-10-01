"use client";

import { useQuery } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { useMemo, useState } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Input, Select, Switch, TagInput, Textarea } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { Cover } from "@/components/ui/misc";
import { get, patch } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { useUnsavedChangesWarning } from "@/lib/hooks";
import { COVER_STYLES, DIFFICULTIES, type AuthoringCourse, type Badge } from "./types";

interface DetailsForm {
  title: string;
  summary: string;
  description_md: string;
  category: string;
  difficulty: string;
  estimated_minutes: string;
  prerequisites: string;
  tags: string[];
  visibility: string;
  issues_certificate: boolean;
  cover_style: string;
  badge_slug: string;
}

function toForm(c: AuthoringCourse): DetailsForm {
  return {
    title: c.title,
    summary: c.summary ?? "",
    description_md: c.description_md ?? "",
    category: c.category,
    difficulty: c.difficulty,
    estimated_minutes: String(c.estimated_minutes),
    prerequisites: c.prerequisites.join("\n"),
    tags: [...c.tags],
    visibility: c.visibility,
    issues_certificate: c.issues_certificate,
    cover_style: c.cover_style,
    badge_slug: c.badge_slug ?? "",
  };
}

export function CourseDetailsForm({ course, onSaved }: { course: AuthoringCourse; onSaved: (c: AuthoringCourse) => void }) {
  const [form, setForm] = useState<DetailsForm>(() => toForm(course));
  const [baseline, setBaseline] = useState(() => JSON.stringify(toForm(course)));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const dirty = JSON.stringify(form) !== baseline;
  useUnsavedChangesWarning(dirty);
  const set = (p: Partial<DetailsForm>) => setForm((f) => ({ ...f, ...p }));

  const categories = useQuery({ queryKey: ["learn", "categories"], queryFn: () => get<string[]>("/learn/categories"), staleTime: 10 * 60_000 });
  const badges = useQuery({ queryKey: ["badges", "catalog"], queryFn: () => get<Badge[]>("/badges"), staleTime: 5 * 60_000 });
  const badgeGroups = useMemo(() => {
    const groups = new Map<string, Badge[]>();
    for (const b of badges.data ?? []) groups.set(b.category, [...(groups.get(b.category) ?? []), b]);
    return [...groups.entries()];
  }, [badges.data]);

  const save = useAction(
    (f: DetailsForm) =>
      patch<AuthoringCourse>(`/learn/courses/${course.slug}`, {
        title: f.title.trim(),
        summary: f.summary.trim(),
        description_md: f.description_md,
        category: f.category,
        difficulty: f.difficulty,
        estimated_minutes: Number(f.estimated_minutes),
        prerequisites: f.prerequisites.split("\n").map((p) => p.trim()).filter(Boolean).slice(0, 10),
        tags: f.tags,
        visibility: f.visibility,
        issues_certificate: f.issues_certificate,
        cover_style: f.cover_style,
        badge_slug: f.badge_slug || null,
      }),
    {
      success: "Course details saved",
      onSuccess: (d) => {
        onSaved(d);
        const fresh = toForm(d);
        setForm(fresh);
        setBaseline(JSON.stringify(fresh));
        setErrors({});
      },
      onError: (e) => setErrors(e.fields ?? {}),
    },
  );

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (form.title.trim().length < 3) local.title = "Enter at least 3 characters.";
    const mins = Number(form.estimated_minutes);
    if (!Number.isInteger(mins) || mins < 5 || mins > 10_000) local.estimated_minutes = "Use a whole number of minutes between 5 and 10,000.";
    if (form.prerequisites.split("\n").filter((p) => p.trim()).length > 10) local.prerequisites = "List at most 10 prerequisites.";
    setErrors(local);
    if (Object.keys(local).length) return;
    save.mutate(form);
  }

  const hasOrg = Boolean(course.org_id);
  const categoryOptions = categories.data ?? [form.category];

  return (
    <form onSubmit={submit} noValidate className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-5">
        <Field label="Title" required error={errors.title}>
          {(p) => <Input {...p} value={form.title} maxLength={140} onChange={(e) => set({ title: e.target.value })} />}
        </Field>
        <Field label="Summary" hint={`${form.summary.length}/300 · shown on course cards and search results`} error={errors.summary}>
          {(p) => <Textarea {...p} rows={2} maxLength={300} value={form.summary} onChange={(e) => set({ summary: e.target.value })} />}
        </Field>
        <Field label="Description" hint="What learners will build and learn. Markdown supported." error={errors.description_md}>
          {(p) => <MarkdownEditor {...p} value={form.description_md} onChange={(v) => set({ description_md: v })} rows={12} maxLength={50_000} />}
        </Field>
        <Field label="Prerequisites" hint="One per line (up to 10), e.g. “Basic Python”." error={errors.prerequisites}>
          {(p) => <Textarea {...p} rows={4} value={form.prerequisites} onChange={(e) => set({ prerequisites: e.target.value })} />}
        </Field>
        <Field label="Tags" hint="Press Enter after each tag." error={errors.tags}>
          {(p) => <TagInput id={p.id} value={form.tags} onChange={(tags) => set({ tags })} max={20} placeholder="pandas, eda…" />}
        </Field>
      </div>

      <div className="space-y-5">
        <Card>
          <CardHeader title="Settings" />
          <CardBody className="space-y-4">
            <Field label="Category" error={errors.category}>
              {(p) => (
                <Select {...p} value={form.category} onChange={(e) => set({ category: e.target.value })}>
                  {categoryOptions.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Difficulty" error={errors.difficulty}>
              {(p) => (
                <Select {...p} value={form.difficulty} onChange={(e) => set({ difficulty: e.target.value })}>
                  {DIFFICULTIES.map((d) => <option key={d} value={d}>{titleCase(d)}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Estimated time (minutes)" error={errors.estimated_minutes}>
              {(p) => (
                <Input {...p} type="number" min={5} max={10000} inputMode="numeric" value={form.estimated_minutes} onChange={(e) => set({ estimated_minutes: e.target.value })} />
              )}
            </Field>
            <Field
              label="Visibility"
              hint={hasOrg ? "Members-only courses are visible to members of the course's organization." : "Members-only visibility needs an organization."}
              error={errors.visibility}
            >
              {(p) => (
                <Select {...p} value={form.visibility} onChange={(e) => set({ visibility: e.target.value })}>
                  <option value="public">Public</option>
                  <option value="org" disabled={!hasOrg}>Organization members only</option>
                </Select>
              )}
            </Field>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Credentials" description="What learners earn when they finish every lesson." />
          <CardBody className="space-y-4">
            <Switch
              checked={form.issues_certificate}
              onChange={(v) => set({ issues_certificate: v })}
              label="Issue a verifiable certificate"
              description="Each certificate gets a public verification page."
            />
            <Field label="Completion badge" hint={badges.isError ? "Badges couldn't be loaded." : "Optional badge awarded on completion."} error={errors.badge_slug}>
              {(p) => (
                <Select {...p} value={form.badge_slug} onChange={(e) => set({ badge_slug: e.target.value })} disabled={badges.isPending && !form.badge_slug}>
                  <option value="">No badge</option>
                  {form.badge_slug && !badges.data?.some((b) => b.slug === form.badge_slug) ? <option value={form.badge_slug}>{form.badge_slug}</option> : null}
                  {badgeGroups.map(([cat, list]) => (
                    <optgroup key={cat} label={titleCase(cat)}>
                      {list.map((b) => <option key={b.slug} value={b.slug}>{b.name}</option>)}
                    </optgroup>
                  ))}
                </Select>
              )}
            </Field>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Cover" />
          <CardBody className="space-y-3">
            <Cover style={form.cover_style} className="h-20 rounded-[var(--radius-md)]" />
            <Field label="Cover style" error={errors.cover_style}>
              {(p) => (
                <Select {...p} value={form.cover_style} onChange={(e) => set({ cover_style: e.target.value })}>
                  {COVER_STYLES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                  {!COVER_STYLES.includes(form.cover_style as (typeof COVER_STYLES)[number]) ? <option value={form.cover_style}>{titleCase(form.cover_style)}</option> : null}
                </Select>
              )}
            </Field>
          </CardBody>
        </Card>
      </div>

      <div className="sticky bottom-0 z-10 -mx-4 flex items-center justify-end gap-2 border-t border-border bg-bg/90 px-4 py-3 backdrop-blur lg:col-span-2 lg:mx-0 lg:rounded-[var(--radius-lg)] lg:border">
        {dirty ? <span className="mr-auto text-sm text-warning">You have unsaved changes</span> : <span className="mr-auto text-sm text-subtle">All changes saved</span>}
        {dirty ? (
          <Button variant="ghost" onClick={() => { setForm(JSON.parse(baseline) as DetailsForm); setErrors({}); }}>
            Discard
          </Button>
        ) : null}
        <Button type="submit" loading={save.isPending} disabled={!dirty} icon={<Save className="h-4 w-4" aria-hidden />}>
          Save details
        </Button>
      </div>
    </form>
  );
}
