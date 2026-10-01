"use client";

import { useQuery } from "@tanstack/react-query";
import { Lock, RotateCcw, Save } from "lucide-react";
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
  toCompetitionPayload,
  validateCompetitionForm,
  type CompetitionFormState,
  type HostOption,
} from "@/components/organizer/competition-form";
import { ManageHeading, competitionKeys, isTerminal, useManage, type CompetitionManage } from "@/components/organizer/shared";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Field, FormError, Input } from "@/components/ui/form";
import { ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { ApiError, api, get, type FieldErrors } from "@/lib/api";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useApiMutation, useConfig, useMe, useUnsavedChangesWarning } from "@/lib/hooks";
import type { CompetitionDetail, CompetitionWrite, Schemas } from "@/lib/types";

/** Mirrors the backend: after finalization only presentation fields may change. */
const EDITABLE_AFTER_FINALIZATION = new Set([
  "description_md", "summary", "faq", "prize_md", "scoring_notes_md", "cover_style", "organizer_notes", "venue_notes", "tags",
]);
const EVALUATION_FIELDS = new Set(["evaluation", "scoring_mode"]);

const SECTIONS = ["basics", "schedule", "participation", "scoring", "content"] as const;
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

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v as SectionKey)}
        tabs={SECTIONS.map((s) => ({ value: s, label: <>{titleCase(s)}{errorCount(s) ? <span className="ml-1.5 text-danger" aria-label={`${errorCount(s)} errors`}>•</span> : null}</> }))}
      >
        <TabPanel value="basics">
          <Card><CardBody className="py-6">
            <BasicsSection form={form} set={set} errors={errors} locked={locked} hosts={hosts} hostsLoading={memberships.isPending} allowNoHost={isAdmin} />
          </CardBody></Card>
        </TabPanel>
        <TabPanel value="schedule">
          <Card><CardBody className="py-6">
            <ScheduleSection form={form} set={set} errors={errors} locked={locked} />
          </CardBody></Card>
        </TabPanel>
        <TabPanel value="participation">
          <Card><CardBody className="py-6">
            <ParticipationSection form={form} set={set} errors={errors} locked={locked} />
          </CardBody></Card>
        </TabPanel>
        <TabPanel value="scoring">
          <Card><CardBody className="py-6">
            {scoringLocked ? (
              <p className="mb-5 flex items-center gap-2 text-xs text-muted"><Lock className="h-3.5 w-3.5" aria-hidden /> Metric, columns and scoring mode are locked.</p>
            ) : null}
            <ScoringSection form={form} set={set} errors={errors} locked={locked} config={config.data} />
          </CardBody></Card>
        </TabPanel>
        <TabPanel value="content">
          <Card><CardBody className="py-6">
            <ContentSection form={form} set={set} errors={errors} locked={locked} />
          </CardBody></Card>
        </TabPanel>
      </Tabs>

      {Object.entries(serverErrors).filter(([k]) => !sectionForField(k)).length ? (
        <FormError message={Object.entries(serverErrors).filter(([k]) => !sectionForField(k)).map(([k, v]) => `${k}: ${v}`).join(" · ")} />
      ) : null}

      <div className="sticky bottom-0 z-20 -mx-4 border-t border-border bg-bg/90 backdrop-blur sm:mx-0 sm:rounded-t-[var(--radius-lg)] sm:border-x">
        <div className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-end">
          <div className="min-w-0 flex-1 text-sm">
            <p className="font-medium text-fg" aria-live="polite">
              {dirty ? `${changedKeys.length} unsaved change${changedKeys.length === 1 ? "" : "s"}` : "All changes saved"}
            </p>
            {dirty ? <p className="truncate text-xs text-subtle">{changedKeys.map(titleCase).join(", ")}</p> : null}
          </div>
          {m.lifecycle !== "draft" ? (
            <Field label="Reason for change" hint="Shown in the change history for versioned fields." className="lg:w-80">
              {(p) => <Input {...p} value={reason} maxLength={500} placeholder="e.g. Extended deadline after outage" onChange={(e) => setReason(e.target.value)} />}
            </Field>
          ) : null}
          <div className="flex gap-2">
            <Button variant="ghost" icon={<RotateCcw className="h-4 w-4" />} disabled={!dirty || save.isPending} onClick={() => { setForm(initial); setServerErrors({}); setLockError(null); }}>
              Discard
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
  if (manage.isPending) return <SkeletonRows rows={8} />;
  if (manage.isError) return <ErrorState error={manage.error} onRetry={() => manage.refetch()} />;
  return (
    <div>
      <ManageHeading title="Settings" description="Edit details, schedule, participation rules, scoring and content. Only changed fields are sent." />
      <SettingsForm key={version} m={manage.data} onSaved={() => setVersion((v) => v + 1)} tab={tab} setTab={setTab} />
    </div>
  );
}
