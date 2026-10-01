"use client";

import { Field, Input } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import type { FieldErrors } from "@/lib/api";

export interface OrgProfileValues {
  name: string;
  tagline: string;
  description_md: string;
  website_url: string;
  country: string;
  city: string;
  accent_color: string;
}

export const EMPTY_ORG_PROFILE: OrgProfileValues = {
  name: "",
  tagline: "",
  description_md: "",
  website_url: "",
  country: "",
  city: "",
  accent_color: "#7C5CFF",
};

const HEX = /^#[0-9a-fA-F]{6}$/;

export function isHexColor(v: string) {
  return HEX.test(v);
}

/** Converts form values into the PATCH/POST body (empty strings become null so the API clears them). */
export function orgProfilePayload(v: OrgProfileValues) {
  return {
    name: v.name.trim(),
    tagline: v.tagline.trim() || null,
    description_md: v.description_md,
    website_url: v.website_url.trim() || null,
    country: v.country.trim().toUpperCase() || null,
    city: v.city.trim() || null,
    accent_color: v.accent_color.trim() || null,
  };
}

export function OrgProfileFields({
  value,
  onChange,
  errors,
}: {
  value: OrgProfileValues;
  onChange: (v: OrgProfileValues) => void;
  errors: FieldErrors;
}) {
  const set = <K extends keyof OrgProfileValues>(k: K, v: OrgProfileValues[K]) => onChange({ ...value, [k]: v });
  const colorOk = !value.accent_color || isHexColor(value.accent_color);
  return (
    <div className="space-y-5">
      <Field label="Name" required error={errors.name} hint="The official name, e.g. “University of Example” or “Example AI Society”.">
        {(p) => <Input {...p} value={value.name} onChange={(e) => set("name", e.target.value)} minLength={3} maxLength={160} autoComplete="organization" />}
      </Field>
      <Field label="Tagline" error={errors.tagline} hint="One line shown on cards and search results.">
        {(p) => <Input {...p} value={value.tagline} onChange={(e) => set("tagline", e.target.value)} maxLength={200} />}
      </Field>
      <Field label="Description" error={errors.description_md} hint="Who you are, what you run and who can join.">
        {(p) => (
          <MarkdownEditor
            id={p.id}
            aria-invalid={p["aria-invalid"]}
            aria-describedby={p["aria-describedby"]}
            value={value.description_md}
            onChange={(v) => set("description_md", v)}
            maxLength={20000}
            rows={8}
          />
        )}
      </Field>
      <Field label="Website" error={errors.website_url} hint="Full URL including https://">
        {(p) => <Input {...p} type="url" inputMode="url" placeholder="https://" value={value.website_url} onChange={(e) => set("website_url", e.target.value)} maxLength={500} />}
      </Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Country" error={errors.country} hint="Two-letter ISO code, e.g. GB, US, DE.">
          {(p) => (
            <Input
              {...p}
              value={value.country}
              onChange={(e) => set("country", e.target.value.replace(/[^a-zA-Z]/g, "").toUpperCase())}
              maxLength={2}
              autoComplete="country"
              className="uppercase"
            />
          )}
        </Field>
        <Field label="City" error={errors.city}>
          {(p) => <Input {...p} value={value.city} onChange={(e) => set("city", e.target.value)} maxLength={80} autoComplete="address-level2" />}
        </Field>
      </div>
      <Field label="Accent color" error={errors.accent_color ?? (colorOk ? undefined : "Use a hex color like #7C5CFF.")} hint="Used for your initial badge and highlights.">
        {(p) => (
          <div className="flex items-center gap-3">
            <input
              type="color"
              aria-label="Pick accent color"
              value={isHexColor(value.accent_color) ? value.accent_color : "#7C5CFF"}
              onChange={(e) => set("accent_color", e.target.value.toUpperCase())}
              className="h-10 w-12 shrink-0 cursor-pointer rounded-[var(--radius-md)] border border-border bg-bg-elevated p-1"
            />
            <Input {...p} value={value.accent_color} onChange={(e) => set("accent_color", e.target.value.trim())} maxLength={7} placeholder="#7C5CFF" className="max-w-40 font-mono" />
          </div>
        )}
      </Field>
    </div>
  );
}
