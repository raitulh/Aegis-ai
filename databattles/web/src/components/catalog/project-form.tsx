"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, FormError, Input, Select, Switch, TagInput, Textarea } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { ApiError } from "@/lib/api";
import { useUnsavedChangesWarning } from "@/lib/hooks";
import type { OrgMini } from "@/lib/types";
import { CoverPicker } from "./cover-picker";
import { unplacedFieldErrors } from "./errors";
import { CompetitionSlugInput, DatasetSlugsInput } from "./slug-picker";
import type { OrgMembership, ProjectDetail } from "./types";

export interface ProjectFormValues {
  title: string;
  summary: string;
  description_md: string;
  tags: string[];
  technologies: string[];
  repo_url: string;
  demo_url: string;
  paper_url: string;
  video_url: string;
  docs_url: string;
  visibility: "public" | "unlisted" | "private";
  status: "draft" | "active" | "archived";
  is_open_source: boolean;
  cover_style: string;
  org_id: string;
  competition_slug: string;
  dataset_slugs: string[];
}

export const EMPTY_PROJECT: ProjectFormValues = {
  title: "",
  summary: "",
  description_md: "",
  tags: [],
  technologies: [],
  repo_url: "",
  demo_url: "",
  paper_url: "",
  video_url: "",
  docs_url: "",
  visibility: "public",
  status: "active",
  is_open_source: false,
  cover_style: "aurora",
  org_id: "",
  competition_slug: "",
  dataset_slugs: [],
};

export function projectToForm(p: ProjectDetail): ProjectFormValues {
  return {
    title: p.title,
    summary: p.summary ?? "",
    description_md: p.description_md ?? "",
    tags: p.tags,
    technologies: p.technologies,
    repo_url: p.repo_url ?? "",
    demo_url: p.demo_url ?? "",
    paper_url: p.paper_url ?? "",
    video_url: p.video_url ?? "",
    docs_url: p.docs_url ?? "",
    visibility: (p.visibility as ProjectFormValues["visibility"]) ?? "public",
    status: (p.status as ProjectFormValues["status"]) ?? "active",
    is_open_source: p.is_open_source,
    cover_style: p.cover_style || "aurora",
    org_id: p.org?.id ?? "",
    competition_slug: p.competition?.slug ?? "",
    dataset_slugs: p.datasets.map((d) => d.slug),
  };
}

const URL_KEYS = ["repo_url", "demo_url", "paper_url", "video_url", "docs_url"] as const;

function normalize(v: ProjectFormValues): Record<string, unknown> {
  const out: Record<string, unknown> = {
    title: v.title.trim(),
    summary: v.summary.trim(),
    description_md: v.description_md,
    tags: v.tags,
    technologies: v.technologies,
    visibility: v.visibility,
    status: v.status,
    is_open_source: v.is_open_source,
    cover_style: v.cover_style,
    org_id: v.org_id || null,
    competition_slug: v.competition_slug.trim().toLowerCase() || null,
    dataset_slugs: v.dataset_slugs,
  };
  for (const k of URL_KEYS) out[k] = v[k].trim() || null;
  return out;
}

/**
 * JSON body for POST /projects (everything) or PATCH /projects/{slug} (only changed fields — so a maintainer who is
 * not a member of the project's organization can still save unrelated edits).
 */
export function projectPayload(v: ProjectFormValues, initial?: ProjectFormValues): Record<string, unknown> {
  const next = normalize(v);
  if (!initial) return next;
  const prev = normalize(initial);
  const out: Record<string, unknown> = {};
  for (const [k, val] of Object.entries(next)) if (JSON.stringify(val) !== JSON.stringify(prev[k])) out[k] = val;
  return out;
}

const PLACED = [
  "title", "summary", "description_md", "tags", "technologies", ...URL_KEYS, "visibility", "status", "is_open_source",
  "cover_style", "org_id", "competition_slug", "dataset_slugs",
];

const URL_LABELS: Record<(typeof URL_KEYS)[number], { label: string; hint: string }> = {
  repo_url: { label: "Repository", hint: "GitHub URL. Registered repos show live stats and can verify you as maintainer." },
  demo_url: { label: "Live demo", hint: "A deployed app, notebook or Space." },
  paper_url: { label: "Paper or report", hint: "arXiv, PDF or write-up." },
  video_url: { label: "Video", hint: "Walkthrough or presentation." },
  docs_url: { label: "Documentation", hint: "Docs site or README." },
};

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={description} />
      <CardBody className="space-y-5">{children}</CardBody>
    </Card>
  );
}

export function ProjectForm({
  initial,
  mode,
  memberships,
  currentOrg,
  knownDatasets,
  submitting,
  error,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: ProjectFormValues;
  mode: "create" | "edit";
  memberships: OrgMembership[] | undefined;
  /** The project's current org (edit), so it stays selectable even if the editor isn't a member. */
  currentOrg?: OrgMini | null;
  knownDatasets?: { slug: string; title: string }[];
  submitting: boolean;
  error: ApiError | null;
  submitLabel: string;
  onSubmit: (v: ProjectFormValues) => void;
  onCancel?: () => void;
}) {
  const [v, setV] = useState<ProjectFormValues>(initial);
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  const dirty = useMemo(() => JSON.stringify(v) !== JSON.stringify(initial), [v, initial]);
  useUnsavedChangesWarning(dirty && !submitting);

  const set = <K extends keyof ProjectFormValues>(key: K, value: ProjectFormValues[K]) => {
    setV((prev) => ({ ...prev, [key]: value }));
    setClientErrors((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key as string];
      return next;
    });
  };
  const fe = (k: string) => clientErrors[k] ?? error?.fields[k];

  const orgOptions = useMemo(() => {
    const list = (memberships ?? []).filter((m) => m.status === "active").map((m) => m.org);
    if (currentOrg && !list.some((o) => o.id === currentOrg.id)) list.unshift(currentOrg);
    return list;
  }, [memberships, currentOrg]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (v.title.trim().length < 3) errs.title = "Enter at least 3 characters.";
    for (const k of URL_KEYS) {
      const u = v[k].trim();
      if (u && !/^https?:\/\/\S+$/i.test(u)) errs[k] = "Use a full http(s):// URL.";
    }
    setClientErrors(errs);
    if (Object.keys(errs).length) return;
    onSubmit(v);
  }

  const general = error ? (Object.keys(error.fields).length ? unplacedFieldErrors(error, PLACED) : error.message) : null;

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <Section title="Basics" description="What you built and why it matters.">
        <Field label="Title" required error={fe("title")}>
          {(p) => <Input {...p} value={v.title} onChange={(e) => set("title", e.target.value)} maxLength={140} placeholder="e.g. Bangla sentiment classifier" />}
        </Field>
        <Field label="Summary" hint={`One or two sentences for cards and search (${v.summary.length}/280).`} error={fe("summary")}>
          {(p) => <Textarea {...p} rows={2} value={v.summary} onChange={(e) => set("summary", e.target.value)} maxLength={280} />}
        </Field>
        <Field label="Description" hint="Markdown. Problem, approach, results, how to run it." error={fe("description_md")}>
          {(p) => <MarkdownEditor {...p} value={v.description_md} onChange={(x) => set("description_md", x)} rows={12} maxLength={100_000} />}
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-fg" id="cover-style-label">Cover style</span>
          <CoverPicker value={v.cover_style} onChange={(x) => set("cover_style", x)} />
          <p className="text-xs text-subtle">Used when the project has no gallery images. The first gallery image becomes the cover.</p>
          {fe("cover_style") ? <p role="alert" className="text-xs font-medium text-danger">{fe("cover_style")}</p> : null}
        </div>
      </Section>

      <Section title="Stack & tags" description="Helps people find your project.">
        <Field label="Technologies" hint="Frameworks, languages, models. Press Enter to add (up to 20)." error={fe("technologies")}>
          {(p) => <TagInput id={p.id} value={v.technologies} onChange={(x) => set("technologies", x)} max={20} placeholder="e.g. pytorch, fastapi" />}
        </Field>
        <Field label="Tags" hint="Topics or domains (up to 12)." error={fe("tags")}>
          {(p) => <TagInput id={p.id} value={v.tags} onChange={(x) => set("tags", x)} max={12} placeholder="e.g. nlp, healthcare" />}
        </Field>
        <Switch
          checked={v.is_open_source}
          onChange={(x) => set("is_open_source", x)}
          label="Open source"
          description="The code is public under an open license. Open-source projects appear in the open source hub."
        />
      </Section>

      <Section title="Links" description="External links open in a new tab.">
        <div className="grid gap-5 md:grid-cols-2">
          {URL_KEYS.map((k) => (
            <Field key={k} label={URL_LABELS[k].label} hint={URL_LABELS[k].hint} error={fe(k)}>
              {(p) => <Input {...p} type="url" inputMode="url" value={v[k]} onChange={(e) => set(k, e.target.value)} maxLength={500} placeholder="https://" />}
            </Field>
          ))}
        </div>
      </Section>

      <Section title="Connections" description="Link the project to an organization, a competition and the datasets it uses.">
        <Field label="Organization" hint="Only organizations you're an active member of." error={fe("org_id")}>
          {(p) => (
            <Select {...p} value={v.org_id} onChange={(e) => set("org_id", e.target.value)}>
              <option value="">None</option>
              {orgOptions.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Competition" hint="A public competition this project was built for (its slug from the URL)." error={fe("competition_slug")}>
          {(p) => <CompetitionSlugInput {...p} value={v.competition_slug} onChange={(x) => set("competition_slug", x)} />}
        </Field>
        <Field label="Datasets" hint="Public datasets the project uses (up to 10)." error={fe("dataset_slugs")}>
          {(p) => <DatasetSlugsInput id={p.id} aria-describedby={p["aria-describedby"]} value={v.dataset_slugs} onChange={(x) => set("dataset_slugs", x)} known={knownDatasets} />}
        </Field>
      </Section>

      <Section title="Publishing">
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="Visibility" error={fe("visibility")}>
            {(p) => (
              <Select {...p} value={v.visibility} onChange={(e) => set("visibility", e.target.value as ProjectFormValues["visibility"])}>
                <option value="public">Public — listed and searchable</option>
                <option value="unlisted">Unlisted — anyone with the link</option>
                <option value="private">Private — members only</option>
              </Select>
            )}
          </Field>
          <Field label="Status" error={fe("status")}>
            {(p) => (
              <Select {...p} value={v.status} onChange={(e) => set("status", e.target.value as ProjectFormValues["status"])}>
                <option value="draft">Draft — hidden from everyone but members</option>
                <option value="active">Active</option>
                <option value="archived">Archived — no longer maintained</option>
              </Select>
            )}
          </Field>
        </div>
      </Section>

      {general ? <FormError message={general} /> : null}
      {Object.keys(clientErrors).length ? <FormError message="Please fix the highlighted fields." /> : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {dirty ? <span className="mr-auto text-xs text-subtle" aria-live="polite">Unsaved changes</span> : null}
        {onCancel ? <Button variant="secondary" onClick={onCancel}>Cancel</Button> : null}
        <Button type="submit" loading={submitting} disabled={mode === "edit" && !dirty}>{submitLabel}</Button>
      </div>
    </form>
  );
}
