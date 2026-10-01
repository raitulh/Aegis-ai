"use client";

import { useQuery } from "@tanstack/react-query";
import { Lock, RotateCcw, Save, Settings2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  BasicsSection,
  ContentSection,
  ParticipationSection,
  ScheduleSection,
  ScoringSection,
  diffPayload,
  formFromManage,
  sectionForField,
  sectionGroups,
  toCompetitionPayload,
  validateCompetitionForm,
  type CompetitionFormState,
  type HostOption,
} from "@/components/organizer/competition-form";
import { ManageHeading, competitionKeys, isTerminal, useManage, type CompetitionManage } from "@/components/organizer/shared";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { ErrorState, InlineNotice, Skeleton } from "@/components/ui/states";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { ApiError, api, get, type FieldErrors } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useApiMutation, useConfig, useMe, useUnsavedChangesWarning } from "@/lib/hooks";
import type { CompetitionDetail, CompetitionWrite, Schemas } from "@/lib/types";

/** Mirrors the backend: after finalization only presentation fields may change. */
const EDITABLE_AFTER_FINALIZATION = new Set([
  "description_md", "summary", "faq", "prize_md", "scoring_notes_md", "cover_style", "organizer_notes", "venue_notes", "tags",
]);
const EVALUATION_FIELDS = new Set(["evaluation", "scoring_mode"]);

const SECTIONS = ["basics", "schedule", "participation", "scoring", "content"] as const;

const FORM_SURFACE = "rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card sm:p-7";
type SectionKey = (typeof SECTIONS)[number];

interface Membership {
  org: Schemas["OrgMini"];
  role: string;
  status: string;
}

function SettingsForm({ m, onSaved, tab, setTab }: { m: CompetitionManage; onSaved: () => void; tab: SectionKey; setTab: (t: SectionKey) => void }) {
  const me = useMe();
  const config = useConfig();
  const isAdmin = hasRole(me.data, "platform_admin");
  const memberships = useQuery({
    queryKey: ["orgs", "me", "memberships"],
    queryFn: () => get<Membership[]>("/orgs/me/memberships"),
  });
  const hosts: HostOption[] = useMemo(
    () =>
      (memberships.data ?? [])
        .filter((x) => x.status === "active" && ["owner", "admin", "manager"].includes(x.role))
        .map((x) => ({ id: x.org.id, name: x.org.name, role: x.role })),
    [memberships.data],
  );

  const initial = useMemo(() => formFromManage(m), [m]);
  const baseline = useMemo(() => {
    const b = toCompetitionPayload(initial);
    // No stored evaluation config yet: treat the defaults shown in the form as a pending change.
    if (!m.evaluation?.metric) delete b.evaluation;
    return b;
  }, [initial, m.evaluation]);
  const [form, setForm] = useState<CompetitionFormState>(initial);
  const [reason, setReason] = useState("");
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [lockError, setLockError] = useState<ApiError | null>(null);

  const finalized = isTerminal(m.lifecycle);
  const scoringLocked = Boolean(m.evaluation_locked_at) && !isAdmin;
  const locked = (field: string) => {
    if (finalized && !EDITABLE_AFTER_FINALIZATION.has(field)) return true;
    if (scoringLocked && EVALUATION_FIELDS.has(field)) return true;
    return false;
  };

  const changes = useMemo(() => diffPayload(baseline, toCompetitionPayload(form)), [baseline, form]);
  const changedKeys = Object.keys(changes);
  const dirty = changedKeys.length > 0;
  useUnsavedChangesWarning(dirty);

  const clientErrors = useMemo(() => (dirty ? validateCompetitionForm(form, { requireHost: false }) : {}), [dirty, form]);
  const errors: FieldErrors = { ...clientErrors, ...serverErrors };
  const errorCount = (s: SectionKey) => Object.keys(errors).filter((k) => sectionForField(k) === s).length;

  const set = (patch: Partial<CompetitionFormState>) => {
    setForm((f) => ({ ...f, ...patch }));
    if (Object.keys(serverErrors).length) setServerErrors({});
  };

  const save = useApiMutation(
    (body: CompetitionWrite) => api<CompetitionDetail>(`/competitions/${m.slug}`, { method: "PATCH", body }),
    {
      success: "Settings saved.",
      invalidate: [...competitionKeys(m.slug), ["competitions", "organizing"]],
      onSuccess: () => {
        setServerErrors({});
        setLockError(null);
        onSaved();
      },
      onError: (e) => {
        const fields = e.fields;
        setServerErrors(fields);
        if (e.code === "scoring_locked" || e.code === "competition_finalized") setLockError(e);
        const first = Object.keys(fields)[0];
        const section = first ? sectionForField(first) : null;
        if (section) setTab(section);
      },
    },
  );

  function submit() {
    const invalid = Object.keys(clientErrors);
    if (invalid.length) {
      const section = sectionForField(invalid[0]);
      if (section) setTab(section);
      toast.error("Fix the highlighted fields before saving.");
      return;
    }
    const body: CompetitionWrite = { ...changes };
    if (reason.trim()) body.change_reason = reason.trim();
    save.mutate(body);
  }

  const lockedFieldsNote = finalized
    ? "Only the summary, problem statement, FAQ, prize details, scoring notes, tags, venue notes and organizer notes can change now."
    : null;

  return (
    <div className="space-y-5">
      {lockedFieldsNote ? (
        <InlineNotice tone="warning" title={m.lifecycle === "archived" ? "Competition archived" : "Competition finalized"}>{lockedFieldsNote}</InlineNotice>
      ) : null}
      {m.evaluation_locked_at && !finalized ? (
        <InlineNotice tone={isAdmin ? "warning" : "info"} title="Scoring rules are locked">
          The first submission was scored {relativeTime(m.evaluation_locked_at)} ({formatDateTime(m.evaluation_locked_at)}), so the scoring mode,
          metric and columns can no longer change{isAdmin ? " — as a platform admin you can still override this; changes are audited" : ""}.
        </InlineNotice>
      ) : null}
      {lockError ? <InlineNotice tone="danger" title="Some changes were rejected">{lockError.message}</InlineNotice> : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_11rem]">
        <div className="min-w-0">
          <Tabs
            value={tab}
            onValueChange={(v) => setTab(v as SectionKey)}
            tabs={SECTIONS.map((s) => ({ value: s, label: <>{titleCase(s)}{errorCount(s) ? <span className="ml-1.5 text-danger" aria-label={`${errorCount(s)} errors`}>•</span> : null}</> }))}
          >
            <TabPanel value="basics">
              <div className={FORM_SURFACE}>
                <BasicsSection form={form} set={set} errors={errors} locked={locked} hosts={hosts} hostsLoading={memberships.isPending} allowNoHost={isAdmin} />
              </div>
            </TabPanel>
            <TabPanel value="schedule">
              <div className={FORM_SURFACE}>
                <ScheduleSection form={form} set={set} errors={errors} locked={locked} />
              </div>
            </TabPanel>
            <TabPanel value="participation">
              <div className={FORM_SURFACE}>
                <ParticipationSection form={form} set={set} errors={errors} locked={locked} />
              </div>
            </TabPanel>
            <TabPanel value="scoring">
              <div className={FORM_SURFACE}>
                {scoringLocked ? (
                  <p className="mb-6 flex items-center gap-2 rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 px-3 py-2 text-xs text-muted"><Lock className="h-3.5 w-3.5 shrink-0" aria-hidden /> Metric, columns and scoring mode are locked.</p>
                ) : null}
                <ScoringSection form={form} set={set} errors={errors} locked={locked} config={config.data} />
              </div>
            </TabPanel>
            <TabPanel value="content">
              <div className={FORM_SURFACE}>
                <ContentSection form={form} set={set} errors={errors} locked={locked} />
              </div>
            </TabPanel>
          </Tabs>
        </div>
        <aside className="hidden xl:block" aria-label="On this tab">
          <div className="sticky top-[8.5rem] pt-14">
            <p className="mb-2 px-2 text-eyebrow text-subtle">On this tab</p>
            <ul className="space-y-0.5 border-l border-border">
              {sectionGroups(tab, form, { showHost: true }).map((g) => (
                <li key={g.id}>
                  <a
                    href={`#${g.id}`}
                    className="-ml-px block border-l border-transparent px-3 py-1 text-xs text-muted transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
                  >
                    {g.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>

      {Object.entries(serverErrors).filter(([k]) => !sectionForField(k)).length ? (
        <FormError message={Object.entries(serverErrors).filter(([k]) => !sectionForField(k)).map(([k, v]) => `${k}: ${v}`).join(" · ")} />
      ) : null}

      <div className="sticky bottom-0 z-20 -mx-4 border-t border-border bg-[var(--glass-strong)] shadow-elevated backdrop-blur-xl sm:mx-0 sm:mb-2 sm:rounded-[var(--radius-lg)] sm:border">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2.5 px-4 py-3 lg:flex lg:items-end">
          <div className="col-start-1 row-start-1 min-w-0 text-sm lg:flex-1">
            <p className="flex items-center gap-2 font-medium text-fg" aria-live="polite">
              <span className={cn("h-2 w-2 shrink-0 rounded-full", dirty ? "bg-warning" : "bg-success")} aria-hidden />
              {dirty ? `${changedKeys.length} unsaved change${changedKeys.length === 1 ? "" : "s"}` : "All changes saved"}
            </p>
            {dirty ? <p className="truncate pl-4 text-xs text-subtle">{changedKeys.map(titleCase).join(", ")}</p> : null}
          </div>
          {m.lifecycle !== "draft" ? (
            <Field label="Reason for change" hint="Shown in the change history for versioned fields." className="col-span-2 row-start-2 lg:w-80">
              {(p) => <Input {...p} value={reason} maxLength={500} placeholder="e.g. Extended deadline after outage" onChange={(e) => setReason(e.target.value)} />}
            </Field>
          ) : null}
          <div className="col-start-2 row-start-1 flex gap-2">
            <Button variant="ghost" icon={<RotateCcw className="h-4 w-4" />} disabled={!dirty || save.isPending} onClick={() => { setForm(initial); setServerErrors({}); setLockError(null); }}>
              <span className="max-sm:sr-only">Discard</span>
            </Button>
            <Button icon={<Save className="h-4 w-4" />} loading={save.isPending} disabled={!dirty} onClick={submit}>
              Save changes
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ManageSettingsPage() {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  const [version, setVersion] = useState(0);
  const [tab, setTab] = useState<SectionKey>("basics");
  if (manage.isPending) {
    return (
      <div className="space-y-6" role="status" aria-label="Loading settings">
        <div className="space-y-2 border-b border-border pb-5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-7 w-40" />
        </div>
        <Skeleton className="h-10 w-full max-w-lg" />
        <Skeleton className="h-[28rem] w-full rounded-[var(--radius-lg)]" />
      </div>
    );
  }
  if (manage.isError) return <ErrorState error={manage.error} onRetry={() => manage.refetch()} />;
  return (
    <div>
      <ManageHeading eyebrow="Configure" icon={<Settings2 />} title="Settings" description="Edit details, schedule, participation rules, scoring and content. Only changed fields are sent." />
      <SettingsForm key={version} m={manage.data} onSaved={() => setVersion((v) => v + 1)} tab={tab} setTab={setTab} />
    </div>
  );
}
