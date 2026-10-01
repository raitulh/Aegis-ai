"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select, Switch, TagInput } from "@/components/ui/form";
import { Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { localTimeZone } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

interface UniversityOption { id: string; name: string; slug: string; email_domains: string[]; departments: { id: string; name: string }[] }

const INTERESTS = ["classification", "regression", "nlp", "computer-vision", "time-series", "open-source", "research", "hackathons", "statistics"];

function Onboarding() {
  const me = useRequireAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const qc = useQueryClient();
  const unis = useQuery({ queryKey: ["university-options"], queryFn: () => get<UniversityOption[]>("/universities/options") });
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({
    display_name: "", headline: "", university_id: "", department_id: "", graduation_year: "",
    skills: [] as string[], interests: [] as string[], timezone: "UTC", open_to_opportunities: false,
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me.data) setForm((f) => ({ ...f, display_name: f.display_name || me.data!.display_name, headline: f.headline || me.data!.headline || "", timezone: localTimeZone() }));
  }, [me.data]);

  const depts = useMemo(() => unis.data?.find((u) => u.id === form.university_id)?.departments ?? [], [unis.data, form.university_id]);

  if (me.isPending || !me.data) return <Spinner />;

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await post("/me/onboarding", {
        display_name: form.display_name, headline: form.headline || null, university_id: form.university_id || null,
        department_id: form.department_id || null, graduation_year: form.graduation_year ? Number(form.graduation_year) : null,
        skills: form.skills, interests: form.interests, timezone: form.timezone, open_to_opportunities: form.open_to_opportunities,
      });
      await qc.invalidateQueries({ queryKey: qk.me });
      router.push(next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
    } catch (e) {
      setError(e as ApiError);
      setStep(0);
    } finally {
      setBusy(false);
    }
  }

  const steps = ["About you", "Interests", "Preferences"];
  return (
    <AuthCard title="Set up your profile" subtitle={`Step ${step + 1} of ${steps.length} · ${steps[step]}`}>
      <div className="mb-6 flex gap-1.5" aria-hidden>
        {steps.map((s, i) => <span key={s} className={`h-1 flex-1 rounded-full ${i <= step ? "bg-accent" : "bg-surface-3"}`} />)}
      </div>
      {step === 0 ? (
        <div className="space-y-4">
          <Field label="Display name" required error={error?.fields.display_name}>
            {(p) => <Input {...p} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} maxLength={80} />}
          </Field>
          <Field label="Headline" hint="e.g. “Stats undergrad exploring NLP”">
            {(p) => <Input {...p} value={form.headline} onChange={(e) => setForm({ ...form, headline: e.target.value })} maxLength={140} />}
          </Field>
          <Field label="University" hint="Self-declared until you verify with your university email." error={error?.fields.university_id}>
            {(p) => (
              <Select {...p} value={form.university_id} onChange={(e) => setForm({ ...form, university_id: e.target.value, department_id: "" })}>
                <option value="">Not listed / not a student</option>
                {unis.data?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </Select>
            )}
          </Field>
          {depts.length ? (
            <Field label="Department">
              {(p) => (
                <Select {...p} value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
                  <option value="">—</option>
                  {depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </Select>
              )}
            </Field>
          ) : null}
          <Field label="Graduation year" error={error?.fields.graduation_year}>
            {(p) => <Input {...p} type="number" min={1950} max={2100} value={form.graduation_year} onChange={(e) => setForm({ ...form, graduation_year: e.target.value })} />}
          </Field>
        </div>
      ) : step === 1 ? (
        <div className="space-y-5">
          <div>
            <p className="text-sm font-medium">What are you interested in?</p>
            <p className="text-xs text-muted">We use this (and only this) to suggest competitions — clearly labeled as such.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {INTERESTS.map((i) => {
                const on = form.interests.includes(i);
                return (
                  <button key={i} type="button" aria-pressed={on}
                    onClick={() => setForm({ ...form, interests: on ? form.interests.filter((x) => x !== i) : [...form.interests, i] })}
                    className={`rounded-full border px-3 py-1 text-sm ${on ? "border-accent bg-accent-soft text-accent-strong" : "border-border text-muted hover:text-fg"}`}>
                    {i}
                  </button>
                );
              })}
            </div>
          </div>
          <Field label="Skills" hint="Press Enter after each skill.">
            {(p) => <TagInput id={p.id} value={form.skills} onChange={(skills) => setForm({ ...form, skills })} placeholder="python, pandas, pytorch…" max={20} />}
          </Field>
        </div>
      ) : (
        <div className="space-y-5">
          <Field label="Time zone" hint="Deadlines are always shown in your local time with the zone name.">
            {(p) => <Input {...p} value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} />}
          </Field>
          <Switch checked={form.open_to_opportunities} onChange={(v) => setForm({ ...form, open_to_opportunities: v })}
            label="Open to opportunities" description="Sponsors of competitions you take part in may see your public profile. Your email is never shared." />
        </div>
      )}
      <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
      <div className="mt-6 flex justify-between gap-2">
        <Button variant="ghost" onClick={() => (step === 0 ? router.push("/dashboard") : setStep(step - 1))}>{step === 0 ? "Skip for now" : "Back"}</Button>
        {step < steps.length - 1 ? (
          <Button onClick={() => setStep(step + 1)} disabled={!form.display_name.trim()}>Continue</Button>
        ) : (
          <Button onClick={finish} loading={busy}>Finish</Button>
        )}
      </div>
    </AuthCard>
  );
}

export default function OnboardingPage() {
  return <Suspense><Onboarding /></Suspense>;
}
