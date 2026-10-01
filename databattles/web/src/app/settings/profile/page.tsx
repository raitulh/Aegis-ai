"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, User } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { COVER_STYLES, type ProfileOut, type UniversityOption } from "@/components/profile/types";
import { Avatar } from "@/components/ui/avatar";
import { SelfDeclaredBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select, Switch, TagInput } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { Cover, FileDrop } from "@/components/ui/misc";
import { ErrorState } from "@/components/ui/states";
import { ApiError, del, errorMessage, get, patch, upload, type FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import { localTimeZone, titleCase } from "@/lib/format";
import { useApiMutation, useConfig, useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { SettingsPageHeading, SettingsSection, SettingsSkeleton } from "../_components/settings-ui";

const PROFILE_KEY = ["me", "profile"] as const;
const INTEREST_SUGGESTIONS = ["classification", "regression", "nlp", "computer-vision", "time-series", "open-source", "research", "hackathons", "statistics"];

interface FormState {
  handle: string;
  display_name: string;
  headline: string;
  bio_md: string;
  website_url: string;
  country: string;
  graduation_year: string;
  skills: string[];
  interests: string[];
  university_id: string;
  department_id: string;
  timezone: string;
  cover_style: string;
  open_to_opportunities: boolean;
}

function fromProfile(p: ProfileOut): FormState {
  return {
    handle: p.handle,
    display_name: p.display_name,
    headline: p.headline ?? "",
    bio_md: p.bio_md ?? "",
    website_url: p.website_url ?? "",
    country: p.country ?? "",
    graduation_year: p.graduation_year ? String(p.graduation_year) : "",
    skills: p.skills ?? [],
    interests: p.interests ?? [],
    university_id: p.university_id ?? "",
    department_id: p.department_id ?? "",
    timezone: p.timezone || "UTC",
    cover_style: p.cover_style || "aurora",
    open_to_opportunities: p.open_to_opportunities,
  };
}

function toPayload(f: FormState): Record<string, unknown> {
  const out: Record<string, unknown> = {
    handle: f.handle.trim().toLowerCase(),
    display_name: f.display_name.trim(),
    headline: f.headline.trim() || null,
    bio_md: f.bio_md.trim() ? f.bio_md : null,
    website_url: f.website_url.trim() || null,
    country: f.country.trim().toUpperCase() || null,
    graduation_year: f.graduation_year ? Number(f.graduation_year) : null,
    skills: f.skills,
    interests: f.interests,
    university_id: f.university_id || null,
    department_id: f.department_id || null,
    cover_style: f.cover_style,
    open_to_opportunities: f.open_to_opportunities,
  };
  if (f.timezone.trim()) out.timezone = f.timezone.trim();
  return out;
}

/** Only fields that actually changed are sent, so untouched fields can never fail validation. */
function diff(a: Record<string, unknown>, b: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(b)) if (JSON.stringify(a[k]) !== JSON.stringify(v)) out[k] = v;
  return out;
}

function timeZones(current: string): string[] {
  let zones: string[] = [];
  try {
    zones = Intl.supportedValuesOf("timeZone");
  } catch {
    zones = [];
  }
  const set = new Set(["UTC", ...zones]);
  if (current) set.add(current);
  return [...set];
}

function AvatarSection({ profile }: { profile: ProfileOut }) {
  const qc = useQueryClient();
  const config = useConfig();
  const [progress, setProgress] = useState<number | null>(null);
  const [removing, setRemoving] = useState(false);
  const maxMb = config.data?.limits.image_mb ?? 5;

  const onUpdated = async (p: ProfileOut) => {
    qc.setQueryData(PROFILE_KEY, p);
    await Promise.all([qc.invalidateQueries({ queryKey: qk.me }), qc.invalidateQueries({ queryKey: qk.dashboard }), qc.invalidateQueries({ queryKey: qk.profile(p.handle) })]);
  };

  return (
    <SettingsSection title="Profile photo" description={`PNG, JPEG or WebP, up to ${maxMb} MB. Images are cropped to a square.`}>
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:gap-6">
        <div className="flex items-center gap-4 sm:flex-col sm:gap-2.5">
          <span className="relative inline-flex rounded-full p-[3px]">
            <span aria-hidden className="absolute inset-0 rounded-full bg-brand opacity-60" />
            <span aria-hidden className="absolute inset-[2px] rounded-full bg-surface" />
            <Avatar name={profile.display_name} src={profile.avatar_url} size={88} className="relative" />
          </span>
          <span className="text-eyebrow text-subtle sm:text-center">{profile.avatar_url ? "Current photo" : "Initials"}</span>
        </div>
        <div className="min-w-0 flex-1">
          <FileDrop
            accept=".png,.jpg,.jpeg,.webp"
            maxBytes={maxMb * 1024 * 1024}
            disabled={progress !== null}
            progress={progress}
            hint="Drop an image or click to choose"
            onFile={async (file) => {
              const fd = new FormData();
              fd.append("file", file);
              setProgress(0);
              try {
                const p = await upload<ProfileOut>("/me/avatar", fd, { onProgress: setProgress });
                await onUpdated(p);
                toast.success("Profile photo updated");
              } catch (e) {
                toast.error(errorMessage(e, "Upload failed."));
              } finally {
                setProgress(null);
              }
            }}
          />
          {profile.avatar_url ? (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2"
              loading={removing}
              onClick={async () => {
                setRemoving(true);
                try {
                  await onUpdated(await del<ProfileOut>("/me/avatar"));
                  toast.success("Profile photo removed");
                } catch (e) {
                  toast.error(errorMessage(e));
                } finally {
                  setRemoving(false);
                }
              }}
            >
              Remove photo
            </Button>
          ) : null}
        </div>
      </div>
    </SettingsSection>
  );
}

function ProfileForm({ profile }: { profile: ProfileOut }) {
  const qc = useQueryClient();
  const unis = useQuery({ queryKey: ["university-options"], queryFn: () => get<UniversityOption[]>("/universities/options"), staleTime: 5 * 60_000 });
  const [initial, setInitial] = useState<FormState>(() => fromProfile(profile));
  const [form, setForm] = useState<FormState>(initial);
  const [errors, setErrors] = useState<FieldErrors>({});
  const zones = useMemo(() => timeZones(form.timezone), [form.timezone]);
  const depts = useMemo(() => unis.data?.find((u) => u.id === form.university_id)?.departments ?? [], [unis.data, form.university_id]);
  const changes = useMemo(() => diff(toPayload(initial), toPayload(form)), [initial, form]);
  const dirty = Object.keys(changes).length > 0;
  useUnsavedChangesWarning(dirty);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: "" }));
  };

  const save = useApiMutation((body: Record<string, unknown>) => patch<ProfileOut>("/me/profile", body), {
    success: "Profile saved",
    onSuccess: async (p) => {
      qc.setQueryData(PROFILE_KEY, p);
      const next = fromProfile(p);
      setInitial(next);
      setForm(next);
      setErrors({});
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.me }),
        qc.invalidateQueries({ queryKey: qk.dashboard }),
        qc.invalidateQueries({ queryKey: ["users"] }),
      ]);
    },
    onError: (e: ApiError) => setErrors(e.fields),
  });

  const fieldErrorCount = Object.values(errors).filter(Boolean).length;

  return (
    <form
      className="space-y-10"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!form.display_name.trim()) return setErrors({ display_name: "Enter your name." });
        if (form.country && !/^[A-Za-z]{2}$/.test(form.country.trim())) return setErrors({ country: "Use a two-letter ISO country code, e.g. BD or US." });
        if (dirty) save.mutate(changes);
      }}
    >
      <SettingsSection title="Basics" description="Shown on your public profile, leaderboards and discussions.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Display name" required error={errors.display_name}>
            {(p) => <Input {...p} value={form.display_name} maxLength={80} autoComplete="name" onChange={(e) => set("display_name", e.target.value)} />}
          </Field>
          <Field
            label="Handle"
            required
            error={errors.handle}
            hint={form.handle !== initial.handle ? "Changing your handle breaks links to your old profile URL." : `Your profile: /u/${form.handle}`}
          >
            {(p) => (
              <div className="flex items-center rounded-[var(--radius-md)] border border-border bg-bg-elevated shadow-[inset_0_1px_2px_rgb(0_0_0/0.12)] transition-[border-color,box-shadow] duration-200 hover:border-border-strong focus-within:border-[color-mix(in_oklab,var(--accent)_70%,var(--border-strong))] focus-within:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_22%,transparent)]">
                <span className="pl-3 font-mono text-sm text-subtle" aria-hidden>@</span>
                <Input
                  {...p}
                  className="border-0 bg-transparent pl-1 shadow-none hover:border-0 focus:shadow-none focus:ring-0"
                  value={form.handle}
                  maxLength={30}
                  autoComplete="username"
                  spellCheck={false}
                  onChange={(e) => set("handle", e.target.value.toLowerCase())}
                />
              </div>
            )}
          </Field>
        </div>
        <Field label="Headline" error={errors.headline} hint="One line, e.g. “Stats undergrad exploring NLP”.">
          {(p) => <Input {...p} value={form.headline} maxLength={140} onChange={(e) => set("headline", e.target.value)} />}
        </Field>
        <Field label="Bio" error={errors.bio_md} hint={`${form.bio_md.length}/5000 characters. Markdown supported.`}>
          {(p) => <MarkdownEditor id={p.id} aria-invalid={p["aria-invalid"]} aria-describedby={p["aria-describedby"]} value={form.bio_md} onChange={(v) => set("bio_md", v)} maxLength={5000} rows={7} placeholder="What are you learning, building or looking for?" />}
        </Field>
      </SettingsSection>

      <SettingsSection
        title="Education"
        description={
          <>
            Your university stays self-declared until you verify membership on the{" "}
            <Link href="/orgs" className="text-accent-strong hover:underline">university's page</Link>.
          </>
        }
        action={<SelfDeclaredBadge />}
      >
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="University" error={errors.university_id}>
            {(p) => (
              <Select
                {...p}
                value={form.university_id}
                disabled={unis.isPending}
                onChange={(e) => {
                  setForm((f) => ({ ...f, university_id: e.target.value, department_id: "" }));
                  setErrors((er) => ({ ...er, university_id: "", department_id: "" }));
                }}
              >
                <option value="">{unis.isPending ? "Loading…" : "Not listed / not a student"}</option>
                {unis.data?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Department" error={errors.department_id} hint={form.university_id && !depts.length ? "This university has no departments listed." : undefined}>
            {(p) => (
              <Select {...p} value={form.department_id} disabled={!depts.length} onChange={(e) => set("department_id", e.target.value)}>
                <option value="">—</option>
                {depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Graduation year" error={errors.graduation_year}>
            {(p) => <Input {...p} type="number" inputMode="numeric" min={1950} max={2100} value={form.graduation_year} onChange={(e) => set("graduation_year", e.target.value)} />}
          </Field>
        </div>
        {unis.isError ? <p className="text-xs text-danger">Couldn&apos;t load universities: {errorMessage(unis.error)}</p> : null}
      </SettingsSection>

      <SettingsSection title="Skills & interests" description="Skills appear on your profile. Interests are private and only used to recommend competitions.">
        <Field label="Skills" error={errors.skills} hint="Press Enter or comma after each skill (up to 20).">
          {(p) => <TagInput id={p.id} value={form.skills} onChange={(v) => set("skills", v)} placeholder="python, pandas, pytorch…" max={20} />}
        </Field>
        <Field label="Interests" error={errors.interests} hint="Press Enter after each interest, or pick suggestions below.">
          {(p) => <TagInput id={p.id} value={form.interests} onChange={(v) => set("interests", v)} placeholder="nlp, computer-vision…" max={20} />}
        </Field>
        <div className="-mt-1 flex flex-wrap items-center gap-1.5" aria-label="Suggested interests">
          <span className="mr-1 text-eyebrow text-subtle" aria-hidden>Suggestions</span>
          {INTEREST_SUGGESTIONS.filter((i) => !form.interests.includes(i)).map((i) => (
            <button
              key={i}
              type="button"
              disabled={form.interests.length >= 20}
              onClick={() => set("interests", [...form.interests, i])}
              className="inline-flex h-9 items-center rounded-full border border-dashed border-border-strong px-3 font-mono text-[11.5px] text-muted transition-colors duration-200 hover:border-[color-mix(in_oklab,var(--accent)_50%,var(--border-strong))] hover:bg-accent-soft hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] disabled:opacity-50 sm:h-7"
            >
              + {i}
            </button>
          ))}
        </div>
      </SettingsSection>

      <SettingsSection title="Location & links" description="Where you are and where people can find more of your work.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Country" error={errors.country} hint="Two-letter ISO code, e.g. BD, IN, US.">
            {(p) => (
              <Input
                {...p}
                value={form.country}
                maxLength={2}
                autoComplete="country"
                className="uppercase"
                onChange={(e) => set("country", e.target.value.replace(/[^A-Za-z]/g, "").toUpperCase())}
              />
            )}
          </Field>
          <Field label="Time zone" error={errors.timezone} hint="Deadlines are always shown in your browser's local time with the zone name.">
            {(p) => (
              <div className="flex gap-2">
                <Select {...p} value={form.timezone} onChange={(e) => set("timezone", e.target.value)}>
                  {zones.map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
                </Select>
                <Button variant="secondary" onClick={() => set("timezone", localTimeZone())} title="Use this device's time zone">Detect</Button>
              </div>
            )}
          </Field>
        </div>
        <Field label="Website" error={errors.website_url} hint="A full http(s):// URL — portfolio, blog or LinkedIn.">
          {(p) => <Input {...p} type="url" inputMode="url" value={form.website_url} maxLength={500} placeholder="https://" onChange={(e) => set("website_url", e.target.value)} />}
        </Field>
      </SettingsSection>

      <SettingsSection title="Cover" description="Generated artwork shown behind your profile header.">
        <div aria-hidden className="overflow-hidden rounded-[var(--radius-lg)] border border-border">
          <Cover style={form.cover_style} className="h-24 sm:h-28">
            <span className="absolute right-3 top-3 rounded-md bg-black/40 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-white/85 backdrop-blur-md">
              Preview
            </span>
          </Cover>
          <div className="flex items-end gap-3 bg-bg-elevated px-4 pb-3.5">
            <span className="-mt-7 inline-flex rounded-full bg-bg-elevated p-1">
              <Avatar name={form.display_name || profile.display_name} src={profile.avatar_url} size={52} />
            </span>
            <span className="min-w-0 pb-0.5">
              <span className="block truncate text-sm font-semibold text-fg">{form.display_name || profile.display_name}</span>
              <span className="block truncate text-xs text-muted">{form.headline || `@${form.handle}`}</span>
            </span>
          </div>
        </div>
        <div role="radiogroup" aria-label="Cover style" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {COVER_STYLES.map((s) => (
            <label key={s} className="group relative cursor-pointer">
              <input type="radio" name="cover_style" value={s} checked={form.cover_style === s} onChange={() => set("cover_style", s)} className="peer sr-only" />
              <Cover
                style={s}
                className={cn(
                  "h-16 rounded-[var(--radius-md)] ring-offset-2 ring-offset-surface transition-shadow duration-200 peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--ring)]",
                  form.cover_style === s ? "ring-2 ring-accent" : "ring-1 ring-border group-hover:ring-border-strong",
                )}
              >
                {form.cover_style === s ? (
                  <span aria-hidden className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-accent text-accent-fg shadow-[0_0_0_2px_rgb(0_0_0/0.25)] animate-pop">
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </span>
                ) : null}
              </Cover>
              <span className={cn("mt-1.5 block text-center text-xs", form.cover_style === s ? "font-medium text-fg" : "text-muted")}>
                {titleCase(s)}
                {form.cover_style === s ? <span className="sr-only"> (selected)</span> : null}
              </span>
            </label>
          ))}
        </div>
        {errors.cover_style ? <p role="alert" className="text-xs font-medium text-danger">{errors.cover_style}</p> : null}
      </SettingsSection>

      <SettingsSection title="Opportunities" description="Let sponsors know you are open to internships, research roles or jobs.">
        <Switch
          checked={form.open_to_opportunities}
          onChange={(v) => set("open_to_opportunities", v)}
          label="Open to opportunities"
          description="Sponsors of competitions you take part in may see your public profile. Your email is never shared."
        />
      </SettingsSection>

      {fieldErrorCount ? <FormError message="Some fields need attention — see the messages above." /> : null}

      <div
        className={cn(
          "sticky bottom-4 z-10 flex flex-col gap-3 rounded-[var(--radius-lg)] border px-4 py-3 transition-[background-color,border-color,box-shadow] duration-300 sm:flex-row sm:items-center sm:justify-between",
          dirty
            ? "border-border-strong bg-[var(--glass-strong)] shadow-elevated backdrop-blur-xl animate-slide-down"
            : "static border-border bg-surface/40 shadow-none",
        )}
      >
        <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite">
          {dirty ? (
            <>
              <span aria-hidden className="relative flex h-2 w-2">
                <span className="absolute inset-0 rounded-full bg-warning opacity-60 motion-safe:animate-ping" />
                <span className="relative h-2 w-2 rounded-full bg-warning" />
              </span>
              You have unsaved changes.
            </>
          ) : (
            <Link href={`/u/${initial.handle}`} className="inline-flex items-center gap-1 text-accent-strong hover:underline">
              View public profile <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </Link>
          )}
        </p>
        <div className="flex gap-2 self-end sm:self-auto">
          {dirty ? (
            <Button
              variant="ghost"
              onClick={() => {
                setForm(initial);
                setErrors({});
              }}
            >
              Discard
            </Button>
          ) : null}
          <Button type="submit" loading={save.isPending} disabled={!dirty}>Save changes</Button>
        </div>
      </div>
    </form>
  );
}

export default function ProfileSettingsPage() {
  const me = useRequireAuth();
  const profile = useQuery({ queryKey: PROFILE_KEY, queryFn: () => get<ProfileOut>("/me/profile"), enabled: Boolean(me.data) });

  useEffect(() => {
    document.title = "Profile settings · DataBattles";
  }, []);

  const heading = (
    <SettingsPageHeading
      icon={<User />}
      title="Profile"
      description="How you appear on leaderboards, in discussions and on your public profile."
    />
  );
  if (profile.isPending) return <SettingsSkeleton sections={3} />;
  if (profile.isError)
    return (
      <div className="space-y-8">
        {heading}
        <ErrorState error={profile.error} onRetry={() => profile.refetch()} />
      </div>
    );
  return (
    <div className="space-y-10">
      {heading}
      <AvatarSection profile={profile.data} />
      <ProfileForm key={profile.data.id} profile={profile.data} />
    </div>
  );
}
