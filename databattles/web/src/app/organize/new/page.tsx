"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, BookOpen, Building2, CalendarClock, Check, FileText, Gauge, ListChecks, Lock, RotateCcw, Sparkles, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  BasicsSection,
  ContentSection,
  ParticipationSection,
  ScheduleSection,
  ScoringSection,
  emptyCompetitionForm,
  sectionForField,
  sectionGroups,
  toCompetitionPayload,
  validateCompetitionForm,
  type CompetitionFormState,
  type HostOption,
} from "@/components/organizer/competition-form";
import { browserTimeZone, zonedInputToUtc } from "@/components/organizer/datetime";
import { EVENT_TYPES, SCORING_MODES, TASK_TYPES, VISIBILITIES } from "@/components/organizer/shared";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { FormError } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { ProgressBar } from "@/components/ui/misc";
import { InlineNotice, Skeleton } from "@/components/ui/states";
import { ApiError, get, post, type FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import { hasRole, useConfig, useDraft, useRequireAuth } from "@/lib/hooks";
import type { CompetitionDetail, Schemas } from "@/lib/types";

interface Membership {
  org: Schemas["OrgMini"];
  role: string;
  status: string;
}

const STEPS = [
  { key: "basics", label: "Basics", description: "Name, type and host" },
  { key: "schedule", label: "Schedule", description: "Dates and time zone" },
  { key: "participation", label: "Participation", description: "Visibility, teams and limits" },
  { key: "scoring", label: "Scoring", description: "Mode, metric and certificates" },
  { key: "content", label: "Content", description: "Statement, rules, FAQ, links" },
] as const;

const STEP_ICONS = { basics: FileText, schedule: CalendarClock, participation: Users, scoring: Gauge, content: BookOpen } as const;

const labelOf = (list: { value: string; label: string }[], v: string) => list.find((o) => o.value === v)?.label ?? v;

/** Read-only recap of what has been entered so far (the organizer's own input, nothing else). */
function DraftSummary({ form, hosts }: { form: CompetitionFormState; hosts: HostOption[] }) {
  const tz = form.timezone || "UTC";
  const starts = zonedInputToUtc(form.starts_at, tz);
  const ends = zonedInputToUtc(form.ends_at, tz);
  const host = hosts.find((h) => h.id === form.host_org_id)?.name;
  const rows: [string, string][] = [
    ["Type", `${labelOf(EVENT_TYPES, form.event_type)} · ${labelOf(TASK_TYPES, form.task_type)}`],
    ["Host", host ?? "Not chosen"],
    ["Window", starts && ends ? `${formatDate(starts, { month: "short", day: "numeric" })} → ${formatDate(ends, { month: "short", day: "numeric", year: "numeric" })}` : "Dates not set"],
    ["Visibility", labelOf(VISIBILITIES, form.visibility)],
    ["Scoring", labelOf(SCORING_MODES, form.scoring_mode)],
  ];
  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
      <div className="border-b border-border px-3.5 py-3">
        <p className="text-eyebrow text-subtle">Draft summary</p>
        <p className={cn("mt-1.5 line-clamp-2 text-sm font-semibold leading-snug tracking-[-0.01em]", form.title.trim() ? "text-fg" : "text-subtle")}>
          {form.title.trim() || "Untitled competition"}
        </p>
      </div>
      <dl className="divide-y divide-border text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-3 px-3.5 py-2">
            <dt className="shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] text-subtle">{k}</dt>
            <dd className="min-w-0 truncate text-right text-muted" title={v}>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
type StepKey = (typeof STEPS)[number]["key"];

const EVAL_KEYS = new Set(["evaluator", "metric", "secondary_metrics", "id_column", "target_column", "strict_schema", "positive_label", "expected_row_count"]);

/** Form key → API field root (used to clear stale server errors as the user edits). */
function apiRoot(formKey: string): string {
  if (EVAL_KEYS.has(formKey)) return "evaluation";
  if (formKey.startsWith("cert_")) return "certificate_rules";
  return formKey;
}

function errorsForStep(errors: FieldErrors, step: StepKey): FieldErrors {
  const out: FieldErrors = {};
  for (const [k, v] of Object.entries(errors)) if (sectionForField(k) === step) out[k] = v;
  return out;
}

interface Draft {
  step: number;
  form: CompetitionFormState;
}

export default function NewCompetitionPage() {
  const me = useRequireAuth();
  const config = useConfig();
  const router = useRouter();
  const qc = useQueryClient();
  const [draft, setDraft, clearDraft] = useDraft<Draft>("organize:new-competition", { step: 0, form: emptyCompetitionForm("") });
  const [clientErrors, setClientErrors] = useState<FieldErrors>({});
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const memberships = useQuery({
    queryKey: ["orgs", "me", "memberships"],
    queryFn: () => get<Membership[]>("/orgs/me/memberships"),
    enabled: !!me.data,
  });
  const hosts: HostOption[] = useMemo(
    () =>
      (memberships.data ?? [])
        .filter((m) => m.status === "active" && ["owner", "admin", "manager"].includes(m.role))
        .map((m) => ({ id: m.org.id, name: m.org.name, role: m.role })),
    [memberships.data],
  );

  const form = draft.form;
  const step = Math.min(Math.max(draft.step, 0), STEPS.length - 1);
  const stepKey = STEPS[step].key;

  // Default the time zone to the browser's once the saved draft (if any) has loaded.
  useEffect(() => {
    if (!draft.form.timezone) setDraft((d) => (d.form.timezone ? d : { ...d, form: { ...d.form, timezone: browserTimeZone() } }));
  }, [draft.form.timezone, setDraft]);

  // Preselect the only organization the user can host with.
  useEffect(() => {
    if (hosts.length === 1 && !draft.form.host_org_id) setDraft((d) => ({ ...d, form: { ...d.form, host_org_id: hosts[0].id } }));
  }, [hosts, draft.form.host_org_id, setDraft]);

  if (me.isPending || !me.data) {
    return (
      <Container size="xl">
        <div className="grid gap-8 pb-16 pt-12 lg:grid-cols-[248px_minmax(0,1fr)]" role="status" aria-label="Loading the competition builder">
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-[var(--radius-md)]" />)}
          </div>
          <div className="space-y-4">
            <Skeleton className="h-3 w-32" />
            <Skeleton className="h-9 w-80 max-w-full" />
            <Skeleton className="h-[26rem] w-full rounded-[var(--radius-lg)]" />
          </div>
        </div>
      </Container>
    );
  }
  const isAdmin = hasRole(me.data, "platform_admin");
  const errors: FieldErrors = { ...clientErrors, ...serverErrors };

  const set = (patch: Partial<CompetitionFormState>) => {
    setDraft((d) => ({ ...d, form: { ...d.form, ...patch } }));
    const roots = new Set(Object.keys(patch).map(apiRoot));
    const clear = (e: FieldErrors) => {
      const next = { ...e };
      let changed = false;
      for (const k of Object.keys(next)) {
        if (roots.has(k.split(".")[0])) {
          delete next[k];
          changed = true;
        }
      }
      return changed ? next : e;
    };
    setServerErrors(clear);
    setClientErrors(clear);
  };
  const goTo = (i: number) => {
    setDraft((d) => ({ ...d, step: i }));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const validateStep = (key: StepKey) => errorsForStep(validateCompetitionForm(form, { requireHost: !isAdmin }), key);

  const next = () => {
    const e = validateStep(stepKey);
    setClientErrors(e);
    if (Object.keys(e).length) {
      toast.error("Fix the highlighted fields to continue.");
      return;
    }
    goTo(step + 1);
  };

  async function create() {
    const all = validateCompetitionForm(form, { requireHost: !isAdmin });
    setClientErrors(all);
    setFormError(null);
    if (Object.keys(all).length) {
      const first = STEPS.findIndex((s) => Object.keys(errorsForStep(all, s.key)).length > 0);
      if (first >= 0) goTo(first);
      toast.error("Some fields need attention before creating the competition.");
      return;
    }
    setBusy(true);
    try {
      const payload = toCompetitionPayload(form);
      if (!payload.host_org_id) delete payload.host_org_id;
      const created = await post<CompetitionDetail>("/competitions", payload);
      clearDraft();
      await qc.invalidateQueries({ queryKey: ["competitions", "organizing"] });
      toast.success("Competition created as a draft.");
      router.push(`/competitions/${created.slug}/manage`);
    } catch (err) {
      const e = err as ApiError;
      const fields = e instanceof ApiError ? e.fields : {};
      if (Object.keys(fields).length) {
        setServerErrors(fields);
        const firstKey = Object.keys(fields)[0];
        const section = sectionForField(firstKey);
        const idx = STEPS.findIndex((s) => s.key === section);
        if (idx >= 0) goTo(idx);
        setFormError(e.message && e.message !== "Some fields are invalid." ? e.message : null);
        toast.error("Some fields need attention.");
      } else {
        setFormError(e instanceof Error ? e.message : "Could not create the competition.");
      }
    } finally {
      setBusy(false);
    }
  }

  const stepErrorCount = (key: StepKey) => Object.keys(errorsForStep(errors, key)).length;
  const unmapped = Object.entries(serverErrors).filter(([k]) => sectionForField(k) === null);

  const groups = sectionGroups(stepKey, form, { showHost: true });
  const StepIcon = STEP_ICONS[stepKey];

  return (
    <Container size="xl">
      <PageHeader
        eyebrow="Organizer tools"
        icon={<Sparkles />}
        title="Create a competition"
        description="Set it up in five steps. It stays a private draft until you publish from the manage page — you can change everything later."
        meta={
          <>
            <span className="inline-flex items-center gap-1.5"><ListChecks className="h-3.5 w-3.5" aria-hidden /> {STEPS.length} steps</span>
            <span className="inline-flex items-center gap-1.5"><Lock className="h-3.5 w-3.5" aria-hidden /> Private draft until you publish</span>
          </>
        }
        actions={
          <ConfirmDialog
            trigger={<Button variant="ghost" icon={<RotateCcw className="h-4 w-4" />}>Start over</Button>}
            title="Discard this draft?"
            description="Everything you've entered on this device will be cleared."
            confirmLabel="Discard draft"
            onConfirm={() => {
              clearDraft();
              setDraft({ step: 0, form: emptyCompetitionForm(browserTimeZone()) });
              setClientErrors({});
              setServerErrors({});
              setFormError(null);
            }}
          />
        }
      />

      {!me.data.email_verified ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Verify your email first" action={<LinkButton href="/verify-email" size="sm" variant="secondary">Verify email</LinkButton>}>
            You can fill in the form now, but creating a competition requires a verified email address.
          </InlineNotice>
        </div>
      ) : null}
      {memberships.isSuccess && hosts.length === 0 && !isAdmin ? (
        <div className="mb-6">
          <InlineNotice
            tone="warning"
            title="You don't manage any organization yet"
            action={<LinkButton href="/orgs/new" size="sm" variant="secondary" icon={<Building2 className="h-4 w-4" />}>Create an organization</LinkButton>}
          >
            Competitions are hosted by an organization where you are an owner, admin or manager. Create a community organization or ask
            your university or club admin for the manager role. Your draft is saved on this device meanwhile.
          </InlineNotice>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[248px_minmax(0,1fr)] lg:gap-8">
        <aside className="min-w-0 lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7rem)] lg:self-start lg:overflow-y-auto lg:pb-4 lg:[scrollbar-width:thin]">
          <nav aria-label="Wizard steps">
            <div className="mb-3 hidden lg:block">
              <div className="mb-1.5 flex items-baseline justify-between">
                <p className="text-eyebrow text-subtle">Progress</p>
                <p className="tabular font-mono text-[10.5px] text-subtle">
                  {step + 1}/{STEPS.length}
                </p>
              </div>
              <ProgressBar value={((step + 1) / STEPS.length) * 100} label={`Step ${step + 1} of ${STEPS.length}`} />
            </div>
            <ol className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:-mx-6 sm:px-6 lg:mx-0 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:px-0">
              {STEPS.map((s, i) => {
                const current = i === step;
                const count = stepErrorCount(s.key);
                return (
                  <li key={s.key} className="shrink-0">
                    <button
                      type="button"
                      onClick={() => goTo(i)}
                      aria-current={current ? "step" : undefined}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-[var(--radius-md)] border px-3 py-2 text-left text-sm transition-colors duration-200 lg:border-transparent",
                        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                        current
                          ? "border-[color-mix(in_oklab,var(--accent)_40%,var(--border))] bg-surface-2 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)]"
                          : "border-border text-muted hover:bg-surface-2/70 hover:text-fg",
                      )}
                    >
                      <span
                        className={cn(
                          "tabular flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-colors",
                          count ? "border-danger text-danger" : i < step ? "border-transparent bg-brand text-accent-fg" : current ? "border-accent text-accent-strong shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_18%,transparent)]" : "border-border-strong",
                        )}
                        aria-hidden
                      >
                        {count ? "!" : i < step ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : i + 1}
                      </span>
                      <span>
                        <span className="block font-medium">{s.label}</span>
                        <span className="hidden text-xs text-subtle lg:block">{s.description}</span>
                        {count ? <span className="sr-only"> — {count} field{count === 1 ? "" : "s"} need attention</span> : null}
                      </span>
                    </button>
                    {current && groups.length ? (
                      <ul className="mb-2 ml-[1.45rem] mt-1 hidden space-y-0.5 border-l border-border pl-4 lg:block" aria-label={`${s.label} sections`}>
                        {groups.map((g) => (
                          <li key={g.id}>
                            <a
                              href={`#${g.id}`}
                              className="block rounded-[var(--radius-sm)] px-2 py-1 text-xs text-muted transition-colors hover:bg-surface-2/70 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
                            >
                              {g.label}
                            </a>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                );
              })}
            </ol>
            <p className="mt-3 hidden text-xs text-subtle lg:block">Your progress is saved automatically in this browser.</p>
          </nav>
          <div className="mt-5 hidden lg:block">
            <DraftSummary form={form} hosts={hosts} />
          </div>
        </aside>

        <Card className="min-w-0">
          <div className="h-0.5 overflow-hidden rounded-t-[var(--radius-lg)] bg-surface-3" aria-hidden>
            <div className="h-full bg-brand transition-[width] duration-500 ease-out-expo" style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} />
          </div>
          <CardHeader icon={<StepIcon />} title={`Step ${step + 1} of ${STEPS.length} · ${STEPS[step].label}`} description={STEPS[step].description} className="px-5 sm:px-7" />
          <CardBody key={stepKey} className="animate-fade-in px-5 py-7 sm:px-7">
            {stepKey === "basics" ? (
              <BasicsSection form={form} set={set} errors={errors} hosts={hosts} hostsLoading={memberships.isPending} allowNoHost={isAdmin} />
            ) : stepKey === "schedule" ? (
              <ScheduleSection form={form} set={set} errors={errors} />
            ) : stepKey === "participation" ? (
              <ParticipationSection form={form} set={set} errors={errors} />
            ) : stepKey === "scoring" ? (
              config.isError ? (
                <InlineNotice tone="danger" title="Couldn't load the metric list" action={<Button size="sm" variant="secondary" onClick={() => config.refetch()}>Retry</Button>} />
              ) : (
                <ScoringSection form={form} set={set} errors={errors} config={config.data} />
              )
            ) : (
              <ContentSection form={form} set={set} errors={errors} />
            )}
            {unmapped.length ? (
              <div className="mt-6">
                <FormError message={unmapped.map(([k, v]) => `${k}: ${v}`).join(" · ")} />
              </div>
            ) : null}
            {formError ? (
              <div className="mt-6">
                <FormError message={formError} />
              </div>
            ) : null}
          </CardBody>
          <CardFooter className="sticky bottom-0 z-10 justify-between rounded-b-[var(--radius-lg)] bg-[var(--glass-strong)] px-5 backdrop-blur-xl sm:px-7">
            <Button variant="ghost" icon={<ArrowLeft className="h-4 w-4" />} disabled={step === 0} onClick={() => goTo(step - 1)}>
              Back
            </Button>
            <span className="tabular hidden font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle sm:block" aria-hidden>
              {STEPS[step].label} · {step + 1}/{STEPS.length}
            </span>
            {step < STEPS.length - 1 ? (
              <Button onClick={next}>
                Continue <ArrowRight className="h-4 w-4" aria-hidden />
              </Button>
            ) : (
              <Button onClick={create} loading={busy} disabled={!me.data.email_verified}>
                Create draft competition
              </Button>
            )}
          </CardFooter>
        </Card>
      </div>
      <p className="sr-only" aria-live="polite">
        {`Step ${step + 1} of ${STEPS.length}: ${STEPS[step].label}`}
      </p>
    </Container>
  );
}
