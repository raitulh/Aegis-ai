"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, FormError, Input, Select, Switch, TagInput, Textarea } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { ApiError } from "@/lib/api";
import { useUnsavedChangesWarning } from "@/lib/hooks";
import { unplacedFieldErrors } from "./errors";
import type { OrgMembership } from "./types";

export interface DatasetFormValues {
  title: string;
  subtitle: string;
  description_md: string;
  license: string;
  citation: string;
  attribution: string;
  source_url: string;
  tags: string[];
  visibility: "public" | "org" | "private";
  requires_terms: boolean;
  terms_md: string;
  owner_org_id: string;
}

export const EMPTY_DATASET: DatasetFormValues = {
  title: "",
  subtitle: "",
  description_md: "",
  license: "cc-by-4.0",
  citation: "",
  attribution: "",
  source_url: "",
  tags: [],
  visibility: "public",
  requires_terms: false,
  terms_md: "",
  owner_org_id: "",
};

const PLACED = ["title", "subtitle", "description_md", "license", "citation", "attribution", "source_url", "tags", "visibility", "requires_terms", "terms_md", "owner_org_id"];

/** Builds the JSON body for POST/PATCH /datasets. In edit mode, blank terms keep the stored terms. */
export function datasetPayload(v: DatasetFormValues, mode: "create" | "edit"): Record<string, unknown> {
  const body: Record<string, unknown> = {
    title: v.title.trim(),
    subtitle: v.subtitle.trim(),
    description_md: v.description_md,
    license: v.license,
    citation: v.citation.trim(),
    attribution: v.attribution.trim(),
    source_url: v.source_url.trim() || null,
    tags: v.tags,
    visibility: v.visibility,
    requires_terms: v.requires_terms,
  };
  if (v.terms_md.trim()) body.terms_md = v.terms_md;
  if (mode === "create" && v.owner_org_id) body.owner_org_id = v.owner_org_id;
  return body;
}

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={description} />
      <CardBody className="space-y-5">{children}</CardBody>
    </Card>
  );
}

export function DatasetMetaForm({
  mode,
  initial,
  licenses,
  orgs,
  hasOrgOwner,
  existingTermsHtml,
  submitting,
  error,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  mode: "create" | "edit";
  initial: DatasetFormValues;
  licenses: Record<string, string> | undefined;
  /** Organizations the user may publish for (create only). */
  orgs?: OrgMembership[];
  /** Edit mode: whether the dataset is owned by an organization ("org" visibility needs one). */
  hasOrgOwner?: boolean;
  existingTermsHtml?: string | null;
  submitting: boolean;
  error: ApiError | null;
  submitLabel: string;
  onSubmit: (values: DatasetFormValues) => void;
  onCancel?: () => void;
}) {
  const [v, setV] = useState<DatasetFormValues>(initial);
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  const dirty = useMemo(() => JSON.stringify(v) !== JSON.stringify(initial), [v, initial]);
  useUnsavedChangesWarning(dirty && !submitting);

  const set = <K extends keyof DatasetFormValues>(key: K, value: DatasetFormValues[K]) => {
    setV((prev) => ({ ...prev, [key]: value }));
    setClientErrors((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key as string];
      return next;
    });
  };

  const fe = (k: string) => clientErrors[k] ?? error?.fields[k];
  const orgOwned = mode === "create" ? Boolean(v.owner_org_id) : Boolean(hasOrgOwner);

  function submit(e: FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (v.title.trim().length < 3) errs.title = "Enter at least 3 characters.";
    if (v.source_url.trim() && !/^https?:\/\/\S+$/i.test(v.source_url.trim())) errs.source_url = "Use a full http(s):// URL.";
    if (v.requires_terms && !v.terms_md.trim() && !(mode === "edit" && existingTermsHtml)) errs.terms_md = "Provide the terms people must accept.";
    if (v.visibility === "org" && !orgOwned) errs.visibility = "Organization visibility needs an owning organization.";
    setClientErrors(errs);
    if (Object.keys(errs).length) return;
    onSubmit(v);
  }

  const general = error ? (Object.keys(error.fields).length ? unplacedFieldErrors(error, PLACED) : error.message) : null;

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <Section title="Basics" description="How the dataset appears in listings and search.">
        <Field label="Title" required error={fe("title")}>
          {(p) => <Input {...p} value={v.title} onChange={(e) => set("title", e.target.value)} maxLength={140} placeholder="e.g. Campus air quality 2024" />}
        </Field>
        <Field label="Subtitle" hint="One line that says what's inside." error={fe("subtitle")}>
          {(p) => <Input {...p} value={v.subtitle} onChange={(e) => set("subtitle", e.target.value)} maxLength={200} />}
        </Field>
        <Field label="Description" hint="Markdown. Describe collection method, columns, known issues and intended use." error={fe("description_md")}>
          {(p) => <MarkdownEditor {...p} value={v.description_md} onChange={(x) => set("description_md", x)} rows={10} maxLength={100_000} />}
        </Field>
        <Field label="Tags" hint="Press Enter or comma to add. Up to 12." error={fe("tags")}>
          {(p) => <TagInput id={p.id} value={v.tags} onChange={(x) => set("tags", x)} placeholder="e.g. tabular, nlp" max={12} />}
        </Field>
      </Section>

      <Section title="License & attribution" description="Tell people how they may use the data and how to credit it.">
        <Field label="License" required error={fe("license")}>
          {(p) => (
            <Select {...p} value={v.license} onChange={(e) => set("license", e.target.value)} disabled={!licenses}>
              {licenses
                ? Object.entries(licenses).map(([key, name]) => <option key={key} value={key}>{name}</option>)
                : <option value={v.license}>Loading licenses…</option>}
            </Select>
          )}
        </Field>
        <Field label="Citation" hint="How to cite this dataset (BibTeX or plain text)." error={fe("citation")}>
          {(p) => <Textarea {...p} rows={3} className="font-mono text-[13px]" value={v.citation} onChange={(e) => set("citation", e.target.value)} maxLength={2000} />}
        </Field>
        <Field label="Attribution" hint="Credit for original creators or upstream sources." error={fe("attribution")}>
          {(p) => <Textarea {...p} rows={2} value={v.attribution} onChange={(e) => set("attribution", e.target.value)} maxLength={2000} />}
        </Field>
        <Field label="Source URL" hint="Where the data originally came from, if anywhere." error={fe("source_url")}>
          {(p) => <Input {...p} type="url" inputMode="url" value={v.source_url} onChange={(e) => set("source_url", e.target.value)} maxLength={500} placeholder="https://" />}
        </Field>
      </Section>

      <Section title="Access" description="Who can find and download the dataset.">
        {mode === "create" ? (
          <Field
            label="Owner"
            hint={orgs && orgs.length ? "Publish under an organization you manage, or under your own account." : "You can publish under organizations where you're an owner, admin or manager."}
            error={fe("owner_org_id")}
          >
            {(p) => (
              <Select
                {...p}
                value={v.owner_org_id}
                onChange={(e) => {
                  const org = e.target.value;
                  setV((prev) => ({ ...prev, owner_org_id: org, visibility: !org && prev.visibility === "org" ? "public" : prev.visibility }));
                }}
              >
                <option value="">Me (personal)</option>
                {(orgs ?? []).map((m) => <option key={m.org.id} value={m.org.id}>{m.org.name}</option>)}
              </Select>
            )}
          </Field>
        ) : null}
        <Field label="Visibility" error={fe("visibility")}>
          {(p) => (
            <Select {...p} value={v.visibility} onChange={(e) => set("visibility", e.target.value as DatasetFormValues["visibility"])}>
              <option value="public">Public — anyone can find and download it</option>
              <option value="org" disabled={!orgOwned}>Organization — members of the owning organization{orgOwned ? "" : " (needs an organization owner)"}</option>
              <option value="private">Private — only managers (and competitions that use it)</option>
            </Select>
          )}
        </Field>
        <Switch
          checked={v.requires_terms}
          onChange={(x) => set("requires_terms", x)}
          label="Require people to accept terms before downloading"
          description="Downloads are blocked until a signed-in user accepts the terms below. Changing the terms asks everyone to accept again."
        />
        {v.requires_terms ? (
          <div className="space-y-3">
            {mode === "edit" && existingTermsHtml ? (
              <div className="rounded-[var(--radius-md)] border border-border bg-surface-2 px-4 py-3">
                <p className="mb-2 text-xs font-medium text-subtle">Current terms</p>
                <Prose html={existingTermsHtml} className="max-h-48 overflow-y-auto text-sm" />
              </div>
            ) : null}
            <Field
              label={mode === "edit" && existingTermsHtml ? "Replace terms" : "Terms"}
              required={!(mode === "edit" && existingTermsHtml)}
              hint={mode === "edit" && existingTermsHtml ? "Leave blank to keep the current terms." : "Markdown. Shown to people before they can download."}
              error={fe("terms_md")}
            >
              {(p) => <MarkdownEditor {...p} value={v.terms_md} onChange={(x) => set("terms_md", x)} rows={6} maxLength={20_000} />}
            </Field>
          </div>
        ) : null}
      </Section>

      {general ? <FormError message={general} /> : null}
      {Object.keys(clientErrors).length ? <FormError message="Please fix the highlighted fields." /> : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {dirty ? <span className="mr-auto text-xs text-subtle" aria-live="polite">Unsaved changes</span> : null}
        {onCancel ? <Button variant="secondary" onClick={onCancel}>Cancel</Button> : null}
        <Button type="submit" loading={submitting}>{submitLabel}</Button>
      </div>
    </form>
  );
}
