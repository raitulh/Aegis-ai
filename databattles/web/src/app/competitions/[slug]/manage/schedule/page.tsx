"use client";

import { Award, CalendarPlus, ExternalLink, MapPin, Plus, Trash2, Trophy } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";

import { zonedInputToUtc } from "@/components/organizer/datetime";
import { ManageHeading, SCHEDULE_KINDS, TeamPicker, ZonedDateTimeField, fieldError, useCompetitionDetail, useManage } from "@/components/organizer/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { ApiError, del, post, put } from "@/lib/api";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail, Message, Schemas } from "@/lib/types";

type ScheduleItem = Schemas["ScheduleItemOut"];
type AwardCategory = Schemas["AwardCategoryOut"];
type Kind = (typeof SCHEDULE_KINDS)[number];

function ScheduleForm({ slug, timeZone }: { slug: string; timeZone: string }) {
  const empty = { title: "", kind: "session" as Kind, starts_at: "", ends_at: "", location: "", url: "", description: "" };
  const [f, setF] = useState(empty);
  const [error, setError] = useState<ApiError | null>(null);
  const [localErr, setLocalErr] = useState<Record<string, string>>({});
  const add = useApiMutation(
    () =>
      post<ScheduleItem>(`/competitions/${slug}/schedule`, {
        title: f.title.trim(),
        kind: f.kind,
        starts_at: zonedInputToUtc(f.starts_at, timeZone),
        ends_at: zonedInputToUtc(f.ends_at, timeZone),
        location: f.location.trim() || null,
        url: f.url.trim() || null,
        description: f.description.trim() || null,
      }),
    {
      success: "Added to the schedule.",
      invalidate: [qk.competition(slug)],
      onSuccess: () => {
        setF(empty);
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  function submit(e: React.FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (f.title.trim().length < 2) errs.title = "Use at least 2 characters.";
    const s = zonedInputToUtc(f.starts_at, timeZone);
    const en = zonedInputToUtc(f.ends_at, timeZone);
    if (!s) errs.starts_at = "Choose a start time.";
    if (s && en && new Date(en) < new Date(s)) errs.ends_at = "End must be after start.";
    if (f.url.trim() && !/^https?:\/\/\S+$/i.test(f.url.trim())) errs.url = "Use a full http(s):// URL.";
    setLocalErr(errs);
    if (!Object.keys(errs).length) add.mutate(undefined);
  }
  const err = (k: string) => localErr[k] ?? fieldError(error?.fields, k);
  return (
    <form onSubmit={submit} className="grid gap-4 rounded-[var(--radius-md)] border border-border p-4" noValidate>
      <p className="text-sm font-medium text-fg">Add a schedule item</p>
      <div className="grid gap-4 sm:grid-cols-[1fr_12rem]">
        <Field label="Title" required error={err("title")}>
          {(p) => <Input {...p} value={f.title} maxLength={160} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Kick-off livestream" />}
        </Field>
        <Field label="Kind" error={err("kind")}>
          {(p) => (
            <Select {...p} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as Kind })}>
              {SCHEDULE_KINDS.map((k) => <option key={k} value={k}>{titleCase(k)}</option>)}
            </Select>
          )}
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <ZonedDateTimeField label="Starts" required timeZone={timeZone} value={f.starts_at} onChange={(v) => setF({ ...f, starts_at: v })} error={err("starts_at")} hint={`Event time zone: ${timeZone}`} />
        <ZonedDateTimeField label="Ends" timeZone={timeZone} value={f.ends_at} onChange={(v) => setF({ ...f, ends_at: v })} error={err("ends_at")} hint="Optional." />
        <Field label="Location" error={err("location")} hint="Room or venue. Optional.">
          {(p) => <Input {...p} value={f.location} maxLength={200} onChange={(e) => setF({ ...f, location: e.target.value })} />}
        </Field>
        <Field label="Link" error={err("url")} hint="Livestream or meeting URL. Optional.">
          {(p) => <Input {...p} type="url" value={f.url} maxLength={500} placeholder="https://" onChange={(e) => setF({ ...f, url: e.target.value })} />}
        </Field>
      </div>
      <Field label="Description" error={err("description")}>
        {(p) => <Textarea {...p} rows={2} value={f.description} maxLength={2000} onChange={(e) => setF({ ...f, description: e.target.value })} />}
      </Field>
      <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
      <div className="flex justify-end">
        <Button type="submit" icon={<CalendarPlus className="h-4 w-4" />} loading={add.isPending}>Add item</Button>
      </div>
    </form>
  );
}

function ScheduleList({ slug, items, timeZone, readOnly }: { slug: string; items: ScheduleItem[]; timeZone: string; readOnly: boolean }) {
  const remove = useApiMutation((id: string) => del<Message>(`/competitions/${slug}/schedule/${id}`), {
    success: "Removed from the schedule.",
    invalidate: [qk.competition(slug)],
  });
  if (!items.length) return <EmptyState title="No schedule items" description="Add workshops, deadlines, presentations or the award ceremony." className="py-8" />;
  return (
    <ol className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
      {[...items].sort((a, b) => a.starts_at.localeCompare(b.starts_at)).map((s) => (
        <li key={s.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={s.kind === "deadline" ? "warning" : s.kind === "ceremony" ? "accent" : "outline"}>{titleCase(s.kind)}</Badge>
              <p className="font-medium text-fg">{s.title}</p>
            </div>
            <p className="mt-1 text-sm text-muted">
              {formatDateTime(s.starts_at)}
              {s.ends_at ? <> – {formatDateTime(s.ends_at)}</> : null} <span className="text-xs text-subtle">({relativeTime(s.starts_at)})</span>
            </p>
            <p className="text-xs text-subtle">Event time: {formatDateTime(s.starts_at, timeZone)}</p>
            <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted">
              {s.location ? <span className="inline-flex items-center gap-1"><MapPin className="h-3.5 w-3.5" aria-hidden /> {s.location}</span> : null}
              {s.url ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-strong hover:underline">
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden /> Link
                </a>
              ) : null}
            </div>
            {s.description ? <p className="mt-1 text-sm text-muted">{s.description}</p> : null}
          </div>
          {!readOnly ? (
            <ConfirmDialog
              trigger={<Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} aria-label={`Remove ${s.title}`}>Remove</Button>}
              title={`Remove “${s.title}”?`}
              description="It disappears from the public schedule."
              confirmLabel="Remove"
              onConfirm={() => remove.mutateAsync(s.id).catch(() => undefined)}
            />
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function AwardRow({ slug, award, readOnly }: { slug: string; award: AwardCategory; readOnly: boolean }) {
  const [editing, setEditing] = useState(false);
  const [teamId, setTeamId] = useState(award.winner_team_id ?? "");
  const setWinner = useApiMutation(
    (team_id: string | null) => put<Message>(`/competitions/${slug}/awards/${award.id}/winner`, { team_id }),
    {
      success: "Award updated.",
      invalidate: [qk.competition(slug)],
      onSuccess: () => setEditing(false),
    },
  );
  const remove = useApiMutation(() => del<Message>(`/competitions/${slug}/awards/${award.id}`), {
    success: "Award category removed.",
    invalidate: [qk.competition(slug)],
  });
  return (
    <li className="px-4 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Award className="h-4 w-4 text-accent-strong" aria-hidden />
            <p className="font-medium text-fg">{award.name}</p>
            <span className="text-xs text-subtle">#{award.position}</span>
          </div>
          {award.description ? <p className="mt-0.5 text-sm text-muted">{award.description}</p> : null}
          <p className="mt-1 text-sm">
            {award.winner_team_id ? (
              <>
                <Trophy className="mr-1 inline h-3.5 w-3.5 text-warning" aria-hidden />
                Winner: <span className="font-medium text-fg">{award.winner_team_name ?? "Team"}</span>
                {award.awarded_at ? <span className="text-xs text-subtle"> · {relativeTime(award.awarded_at)}</span> : null}
              </>
            ) : (
              <span className="text-subtle">Not awarded yet</span>
            )}
          </p>
        </div>
        {!readOnly ? (
          <div className="flex shrink-0 flex-wrap gap-1">
            <Button size="sm" variant="secondary" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
              {award.winner_team_id ? "Change winner" : "Set winner"}
            </Button>
            {award.winner_team_id ? (
              <ConfirmDialog
                trigger={<Button size="sm" variant="ghost">Clear</Button>}
                title="Clear the winner?"
                description="Certificates already issued for this award stay valid unless you revoke them on the Results page."
                confirmLabel="Clear winner"
                onConfirm={() => setWinner.mutateAsync(null).catch(() => undefined)}
              />
            ) : null}
            <ConfirmDialog
              trigger={<Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} aria-label={`Delete award ${award.name}`}>Delete</Button>}
              title={`Delete “${award.name}”?`}
              description="The category and its winner are removed. Issued certificates stay valid unless revoked."
              confirmLabel="Delete"
              onConfirm={() => remove.mutateAsync(undefined).catch(() => undefined)}
            />
          </div>
        ) : null}
      </div>
      {editing ? (
        <div className="mt-3 grid gap-3 rounded-[var(--radius-md)] border border-border bg-surface-2/50 p-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <TeamPicker slug={slug} value={teamId} onChange={setTeamId} label="Winning team" />
          <Button loading={setWinner.isPending} disabled={!teamId || teamId === award.winner_team_id} onClick={() => setWinner.mutate(teamId)} className="sm:mb-5">
            Save winner
          </Button>
        </div>
      ) : null}
    </li>
  );
}

function AwardForm({ slug, nextPosition }: { slug: string; nextPosition: number }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [position, setPosition] = useState(String(nextPosition));
  const [error, setError] = useState<ApiError | null>(null);
  const add = useApiMutation(
    () => post<AwardCategory>(`/competitions/${slug}/awards`, { name: name.trim(), description: description.trim() || null, position: Number(position) || 0 }),
    {
      success: "Award category added.",
      invalidate: [qk.competition(slug)],
      onSuccess: () => {
        setName("");
        setDescription("");
        setPosition(String(nextPosition + 1));
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  return (
    <form
      className="grid gap-3 rounded-[var(--radius-md)] border border-border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim().length >= 2) add.mutate(undefined);
      }}
    >
      <p className="text-sm font-medium text-fg">Add an award category</p>
      <div className="grid gap-3 sm:grid-cols-[1fr_7rem]">
        <Field label="Name" required error={error?.fields.name} hint="e.g. “Best use of open data”.">
          {(p) => <Input {...p} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Order" error={error?.fields.position}>
          {(p) => <Input {...p} type="number" value={position} onChange={(e) => setPosition(e.target.value)} />}
        </Field>
      </div>
      <Field label="Description" error={error?.fields.description}>
        {(p) => <Textarea {...p} rows={2} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
      <div className="flex justify-end">
        <Button type="submit" icon={<Plus className="h-4 w-4" />} loading={add.isPending} disabled={name.trim().length < 2}>Add award</Button>
      </div>
    </form>
  );
}

export default function ManageSchedulePage() {
  const { slug } = useParams<{ slug: string }>();
  const detail = useCompetitionDetail(slug);
  const manage = useManage(slug);
  if (detail.isPending) return <SkeletonRows rows={6} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const c: CompetitionDetail = detail.data;
  const readOnly = manage.data?.lifecycle === "archived";
  const awards = [...c.awards].sort((a, b) => a.position - b.position);
  return (
    <div className="space-y-6">
      <ManageHeading title="Schedule & awards" description={`Times are entered in the event's time zone (${c.timezone}) and shown to participants in their own.`} />
      <Card>
        <CardHeader title="Schedule" description="Sessions, workshops, deadlines and ceremonies shown on the competition page." />
        <CardBody className="space-y-5">
          <ScheduleList slug={slug} items={c.schedule} timeZone={c.timezone} readOnly={readOnly} />
          {!readOnly ? <ScheduleForm slug={slug} timeZone={c.timezone} /> : null}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Award categories" description="Special prizes beyond the ranking, e.g. “Best visualization”. Winners receive award certificates when you issue certificates." />
        <CardBody className="space-y-5">
          {awards.length ? (
            <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
              {awards.map((a) => <AwardRow key={a.id} slug={slug} award={a} readOnly={readOnly} />)}
            </ul>
          ) : (
            <EmptyState icon={<Award className="h-5 w-5" />} title="No award categories" description="Add categories now and pick winners once judging or results are in." className="py-8" />
          )}
          {!readOnly ? <AwardForm slug={slug} nextPosition={awards.length ? Math.max(...awards.map((a) => a.position)) + 1 : 1} /> : null}
        </CardBody>
      </Card>
    </div>
  );
}
