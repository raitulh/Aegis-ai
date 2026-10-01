"use client";

import { Building2, Pencil, Trash2, UserPlus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ManageHeading, SPONSOR_TIERS, manageKey, useManage } from "@/components/organizer/shared";
import { UserLink } from "@/components/domain/cards";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { ApiError, del, post } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Message } from "@/lib/types";

function StaffCard({ slug, staff, scoringMode, archived }: {
  slug: string;
  staff: { user: { id: string; handle: string; display_name: string; avatar_url?: string | null }; role: string }[];
  scoringMode: string;
  archived: boolean;
}) {
  const me = useMe();
  const [handle, setHandle] = useState("");
  const [role, setRole] = useState<"organizer" | "judge">("organizer");
  const [error, setError] = useState<ApiError | null>(null);
  const add = useApiMutation(
    () => post<Message>(`/competitions/${slug}/staff`, { handle: handle.trim().replace(/^@/, "").toLowerCase(), role }),
    {
      success: () => `Added @${handle.trim().replace(/^@/, "")} as ${role}.`,
      invalidate: [manageKey(slug), qk.competition(slug)],
      onSuccess: () => {
        setHandle("");
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  const remove = useApiMutation(
    ({ userId, role: r }: { userId: string; role: string }) => del<Message>(`/competitions/${slug}/staff/${userId}/${r}`),
    { success: "Staff member removed.", invalidate: [manageKey(slug), qk.competition(slug)] },
  );
  const organizers = staff.filter((s) => s.role === "organizer");
  const judges = staff.filter((s) => s.role === "judge");

  const list = (rows: typeof staff, label: string) => (
    <div>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-subtle">{label} ({rows.length})</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-subtle">None yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
          {rows.map((s) => {
            const lastOrganizer = s.role === "organizer" && organizers.length <= 1;
            return (
              <li key={`${s.user.id}-${s.role}`} className="flex items-center justify-between gap-3 px-3 py-2.5">
                <div className="flex min-w-0 items-center gap-2">
                  <UserLink user={s.user} />
                  <span className="truncate text-xs text-subtle">@{s.user.handle}</span>
                  {s.user.id === me.data?.id ? <Badge tone="outline">You</Badge> : null}
                </div>
                {!archived ? (
                  <ConfirmDialog
                    trigger={
                      <Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} disabled={lastOrganizer} title={lastOrganizer ? "A competition needs at least one organizer" : undefined} aria-label={`Remove ${s.user.display_name} as ${s.role}`}>
                        Remove
                      </Button>
                    }
                    title={`Remove ${s.user.display_name} as ${s.role}?`}
                    description={s.role === "judge" ? "They lose access to the judging queue. Scores they already submitted are kept in the audit trail." : "They lose access to the organizer tools."}
                    confirmLabel="Remove"
                    onConfirm={() => remove.mutateAsync({ userId: s.user.id, role: s.role }).catch(() => undefined)}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );

  return (
    <Card>
      <CardHeader title="Staff" description="Organizers can manage everything here; judges score entries in judged events. Staff can't also participate." />
      <CardBody className="space-y-5">
        {!archived ? (
          <form
            className="grid gap-3 sm:grid-cols-[1fr_10rem_auto] sm:items-start"
            onSubmit={(e) => {
              e.preventDefault();
              if (handle.trim()) add.mutate(undefined);
            }}
          >
            <Field label="Handle" error={error?.fields.handle ?? (error && !Object.keys(error.fields).length ? error.message : undefined)} hint="The person's DataBattles username.">
              {(p) => <Input {...p} value={handle} onChange={(e) => { setHandle(e.target.value); setError(null); }} placeholder="@handle" autoComplete="off" maxLength={31} />}
            </Field>
            <Field label="Role">
              {(p) => (
                <Select {...p} value={role} onChange={(e) => setRole(e.target.value as "organizer" | "judge")}>
                  <option value="organizer">Organizer</option>
                  <option value="judge">Judge</option>
                </Select>
              )}
            </Field>
            <Button type="submit" icon={<UserPlus className="h-4 w-4" />} loading={add.isPending} disabled={!handle.trim()} className="sm:mt-6">
              Add
            </Button>
          </form>
        ) : null}
        <div className="grid gap-5 lg:grid-cols-2">
          {list(organizers, "Organizers")}
          {list(judges, "Judges")}
        </div>
        <p className="text-xs text-subtle">
          The competition creator and admins of the host organization always have organizer access, even if not listed.
          {scoringMode === "judged" ? <> Assign entries to judges on the <Link href={`/competitions/${slug}/manage/judging`} className="text-accent-strong hover:underline">Judging</Link> page.</> : null}
        </p>
      </CardBody>
    </Card>
  );
}

function SponsorsCard({ slug, sponsors, archived }: {
  slug: string;
  sponsors: { org: { id: string; slug: string; name: string; logo_url?: string | null; type: string }; tier: string; blurb?: string | null }[];
  archived: boolean;
}) {
  const [orgSlug, setOrgSlug] = useState("");
  const [tier, setTier] = useState<(typeof SPONSOR_TIERS)[number]>("partner");
  const [blurb, setBlurb] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const save = useApiMutation(
    () => post<Message>(`/competitions/${slug}/sponsors`, { org_slug: orgSlug.trim(), tier, blurb: blurb.trim() || null }),
    {
      success: "Sponsor saved.",
      invalidate: [manageKey(slug), qk.competition(slug)],
      onSuccess: () => {
        setOrgSlug("");
        setBlurb("");
        setTier("partner");
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  const remove = useApiMutation((orgId: string) => del<Message>(`/competitions/${slug}/sponsors/${orgId}`), {
    success: "Sponsor removed.",
    invalidate: [manageKey(slug), qk.competition(slug)],
  });
  const editing = sponsors.some((s) => s.org.slug === orgSlug.trim());
  return (
    <Card>
      <CardHeader title="Sponsors" description="Sponsor organizations are shown on the competition page. Their managers can propose announcements for your review." />
      <CardBody className="space-y-5">
        {sponsors.length === 0 ? (
          <p className="text-sm text-subtle">No sponsors yet.</p>
        ) : (
          <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
            {sponsors.map((s) => (
              <li key={s.org.id} className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={s.org.name} src={s.org.logo_url ?? null} size={32} />
                  <div className="min-w-0">
                    <Link href={`/orgs/${s.org.slug}`} className="font-medium text-fg hover:text-accent-strong">{s.org.name}</Link>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-subtle">
                      <Badge tone={s.tier === "title" || s.tier === "platinum" ? "accent" : "outline"}>{titleCase(s.tier)}</Badge>
                      {s.blurb ? <span className="line-clamp-1">{s.blurb}</span> : null}
                    </div>
                  </div>
                </div>
                {!archived ? (
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" icon={<Pencil className="h-4 w-4" />} aria-label={`Edit ${s.org.name}`} onClick={() => { setOrgSlug(s.org.slug); setTier(s.tier as (typeof SPONSOR_TIERS)[number]); setBlurb(s.blurb ?? ""); }}>
                      Edit
                    </Button>
                    <ConfirmDialog
                      trigger={<Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} aria-label={`Remove ${s.org.name}`}>Remove</Button>}
                      title={`Remove ${s.org.name} as a sponsor?`}
                      description="Their logo and blurb are removed from the competition page."
                      confirmLabel="Remove"
                      onConfirm={() => remove.mutateAsync(s.org.id).catch(() => undefined)}
                    />
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {!archived ? (
          <form
            className="grid gap-3 rounded-[var(--radius-md)] border border-border p-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (orgSlug.trim()) save.mutate(undefined);
            }}
          >
            <p className="text-sm font-medium text-fg">{editing ? "Update sponsor" : "Add a sponsor"}</p>
            <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
              <Field label="Organization slug" error={error?.fields.org_slug ?? (error && !Object.keys(error.fields).length ? error.message : undefined)} hint="From the organization's URL, e.g. /orgs/acme-ai → acme-ai.">
                {(p) => <Input {...p} value={orgSlug} onChange={(e) => { setOrgSlug(e.target.value); setError(null); }} placeholder="acme-ai" maxLength={80} />}
              </Field>
              <Field label="Tier" error={error?.fields.tier}>
                {(p) => (
                  <Select {...p} value={tier} onChange={(e) => setTier(e.target.value as (typeof SPONSOR_TIERS)[number])}>
                    {SPONSOR_TIERS.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
                  </Select>
                )}
              </Field>
            </div>
            <Field label="Blurb" error={error?.fields.blurb} hint={`Optional, shown next to the logo. ${blurb.length}/300`}>
              {(p) => <Textarea {...p} rows={2} value={blurb} maxLength={300} onChange={(e) => setBlurb(e.target.value)} />}
            </Field>
            <div className="flex justify-end">
              <Button type="submit" icon={<Building2 className="h-4 w-4" />} loading={save.isPending} disabled={!orgSlug.trim()}>
                {editing ? "Update sponsor" : "Add sponsor"}
              </Button>
            </div>
          </form>
        ) : null}
      </CardBody>
    </Card>
  );
}

export default function ManagePeoplePage() {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  if (manage.isPending) return <SkeletonRows rows={6} />;
  if (manage.isError) return <ErrorState error={manage.error} onRetry={() => manage.refetch()} />;
  const m = manage.data;
  const archived = m.lifecycle === "archived";
  return (
    <div className="space-y-6">
      <ManageHeading title="Staff & sponsors" description="Who runs, judges and supports this competition." />
      <StaffCard slug={slug} staff={m.staff} scoringMode={String(m.raw.scoring_mode ?? "")} archived={archived} />
      <SponsorsCard slug={slug} sponsors={m.sponsors} archived={archived} />
    </div>
  );
}
