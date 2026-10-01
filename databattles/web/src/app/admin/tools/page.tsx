"use client";

import { useQuery } from "@tanstack/react-query";
import { Award, BadgePlus, RefreshCcw, SearchCode, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AdminHeader } from "@/components/admin/admin-ui";
import type { BadgeDef } from "@/components/admin/types";
import { isHexColor } from "@/components/orgs/org-form";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Switch, Textarea } from "@/components/ui/form";
import { InlineNotice, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { useApiMutation, useConfig } from "@/lib/hooks";

type Msg = { message: string };

const CRITERIA: { type: string; label: string; param?: { key: "count" | "max_rank" | "course_slug" | "path_slug"; label: string; numeric: boolean } }[] = [
  { type: "first_submission", label: "First scored submission" },
  { type: "competition_rank", label: "Finish within rank N of a finalized competition", param: { key: "max_rank", label: "Max rank", numeric: true } },
  { type: "competitions_joined", label: "Join N competitions", param: { key: "count", label: "Competitions", numeric: true } },
  { type: "course_completed", label: "Complete a specific course", param: { key: "course_slug", label: "Course slug", numeric: false } },
  { type: "courses_completed", label: "Complete N courses", param: { key: "count", label: "Courses", numeric: true } },
  { type: "path_completed", label: "Complete a learning path", param: { key: "path_slug", label: "Path slug", numeric: false } },
  { type: "merged_prs", label: "N merged pull requests", param: { key: "count", label: "Pull requests", numeric: true } },
  { type: "accepted_answers", label: "N accepted answers", param: { key: "count", label: "Answers", numeric: true } },
];

function MaintenanceCard() {
  const reindex = useApiMutation(() => post<Msg>("/admin/search/reindex"), { success: (r) => r.message });
  const recalc = useApiMutation(() => post<Msg>("/admin/badges/recalculate"), { success: (r) => r.message, invalidate: [["badges"]] });
  return (
    <Card>
      <CardHeader title="Maintenance" description="Safe to run any time; both are recorded in the audit log." />
      <CardBody className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium text-fg">Rebuild the search index</p>
            <p className="text-xs text-muted">Re-indexes every searchable record. Use after bulk imports or if search results look stale.</p>
          </div>
          <ConfirmDialog
            trigger={<Button variant="secondary" size="sm" icon={<SearchCode className="h-4 w-4" />} loading={reindex.isPending}>Reindex</Button>}
            title="Rebuild the search index?"
            description="This can take a while on large databases. Search keeps working while it runs."
            confirmLabel="Reindex now"
            tone="primary"
            onConfirm={async () => {
              await reindex.mutateAsync(undefined).catch(() => undefined);
            }}
          />
        </div>
        <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium text-fg">Recalculate automatic badges</p>
            <p className="text-xs text-muted">Evaluates badge criteria for every active user and awards any that are missing. Never revokes badges.</p>
          </div>
          <ConfirmDialog
            trigger={<Button variant="secondary" size="sm" icon={<RefreshCcw className="h-4 w-4" />} loading={recalc.isPending}>Recalculate</Button>}
            title="Recalculate badges for all users?"
            description="Users are notified about newly earned badges."
            confirmLabel="Recalculate"
            tone="primary"
            onConfirm={async () => {
              await recalc.mutateAsync(undefined).catch(() => undefined);
            }}
          />
        </div>
      </CardBody>
    </Card>
  );
}

const PURGE_PHRASE = "PURGE-DEMO";

function PurgeDemoCard() {
  const config = useConfig().data;
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const purge = useApiMutation(() => post<Msg>("/admin/demo/purge", undefined, { confirm: PURGE_PHRASE }), {
    success: "Demo data removed",
    onSuccess: (r) => {
      setResult(r.message);
      setOpen(false);
      setTyped("");
    },
  });
  return (
    <Card className="border-danger/40">
      <CardHeader title={<span className="text-danger">Danger zone</span>} />
      <CardBody className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium text-fg">Purge demo data</p>
            <p className="text-xs text-muted">
              Permanently deletes all synthetic records created by the seed script (demo users, organizations, competitions, submissions…). Real
              data is untouched. This cannot be undone.
              {config ? ` Demo mode is currently ${config.demo_mode ? "on" : "off"}.` : ""}
            </p>
          </div>
          <Dialog
            open={open}
            onOpenChange={(o) => {
              setOpen(o);
              if (!o) setTyped("");
            }}
            trigger={<Button variant="danger" size="sm" icon={<Trash2 className="h-4 w-4" />}>Purge demo data</Button>}
            title="Purge all demo data?"
            description="This permanently deletes every record flagged as demo data. It cannot be undone."
            size="sm"
            footer={
              <>
                <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
                <Button variant="danger" loading={purge.isPending} disabled={typed !== PURGE_PHRASE} onClick={() => purge.mutate(undefined)}>
                  Permanently purge
                </Button>
              </>
            }
          >
            <Field label={<>Type <span className="font-mono">{PURGE_PHRASE}</span> to confirm</>}>
              {(p) => <Input {...p} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} className="font-mono" />}
            </Field>
          </Dialog>
        </div>
        {result ? <InlineNotice tone="success" title="Purge complete">{result}</InlineNotice> : null}
      </CardBody>
    </Card>
  );
}

function CreateBadgeCard() {
  const [form, setForm] = useState({
    name: "", slug: "", description: "", category: "competition", icon: "award", color: "#7C5CFF", rarity_label: "", is_manual: true,
    criteria_type: "first_submission", criteria_value: "",
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [created, setCreated] = useState<BadgeDef | null>(null);
  const create = useApiMutation((body: Record<string, unknown>) => post<BadgeDef>("/badges", body), {
    success: (b) => `Badge “${b.name}” created`,
    invalidate: [["badges"]],
    onSuccess: (b) => {
      setCreated(b);
      setError(null);
      setForm((f) => ({ ...f, name: "", slug: "", description: "", rarity_label: "", criteria_value: "" }));
    },
    onError: setError,
  });
  const crit = CRITERIA.find((c) => c.type === form.criteria_type);
  const needsParam = !form.is_manual && crit?.param;
  const valid =
    form.name.trim().length >= 2 && form.description.trim().length >= 5 && /^[a-z0-9-]{2,40}$/.test(form.icon) && isHexColor(form.color) &&
    (!needsParam || form.criteria_value.trim().length > 0);

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const criteria: Record<string, unknown> = {};
    if (!form.is_manual && crit) {
      criteria.type = crit.type;
      if (crit.param) criteria[crit.param.key] = crit.param.numeric ? Number(form.criteria_value) : form.criteria_value.trim();
    }
    create.mutate({
      name: form.name.trim(),
      slug: form.slug.trim() || null,
      description: form.description.trim(),
      category: form.category,
      icon: form.icon,
      color: form.color,
      rarity_label: form.rarity_label.trim() || null,
      is_manual: form.is_manual,
      criteria,
    });
  }

  const f = error?.fields ?? {};
  return (
    <Card>
      <CardHeader title={<span className="inline-flex items-center gap-2"><BadgePlus className="h-4 w-4 text-accent-strong" aria-hidden />Create a badge</span>} description="Automatic badges are awarded by criteria; manual badges are awarded by staff with a reason." />
      <form onSubmit={submit} noValidate>
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" required error={f.name}>
            {(p) => <Input {...p} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={80} />}
          </Field>
          <Field label="Slug" error={f.slug} hint="Optional; generated from the name.">
            {(p) => <Input {...p} value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase() })} maxLength={80} className="font-mono" />}
          </Field>
          <Field label="Description" required error={f.description} className="sm:col-span-2" hint="What it recognizes, in one sentence (5–300 characters).">
            {(p) => <Textarea {...p} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={300} rows={2} />}
          </Field>
          <Field label="Category" error={f.category}>
            {(p) => (
              <Select {...p} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {["learning", "competition", "contribution", "community"].map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Rarity label" error={f.rarity_label} hint="Optional, e.g. Rare.">
            {(p) => <Input {...p} value={form.rarity_label} onChange={(e) => setForm({ ...form, rarity_label: e.target.value })} maxLength={24} />}
          </Field>
          <Field label="Icon" error={f.icon ?? (/^[a-z0-9-]{2,40}$/.test(form.icon) ? undefined : "Lowercase letters, digits and dashes.")} hint="Icon name, e.g. award, trophy, rocket.">
            {(p) => <Input {...p} value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value.toLowerCase() })} maxLength={40} className="font-mono" />}
          </Field>
          <Field label="Color" error={f.color ?? (isHexColor(form.color) ? undefined : "Use a hex color like #7C5CFF.")}>
            {(p) => (
              <div className="flex items-center gap-2">
                <input type="color" aria-label="Pick badge color" value={isHexColor(form.color) ? form.color : "#7C5CFF"} onChange={(e) => setForm({ ...form, color: e.target.value.toUpperCase() })} className="h-10 w-12 shrink-0 cursor-pointer rounded-[var(--radius-md)] border border-border bg-bg-elevated p-1" />
                <Input {...p} value={form.color} onChange={(e) => setForm({ ...form, color: e.target.value.trim() })} maxLength={7} className="font-mono" />
              </div>
            )}
          </Field>
          <div className="sm:col-span-2">
            <Switch checked={form.is_manual} onChange={(v) => setForm({ ...form, is_manual: v })} label="Manual badge" description="Awarded by staff with a reason instead of by automatic criteria." />
          </div>
          {!form.is_manual ? (
            <>
              <Field label="Criteria" error={f.criteria}>
                {(p) => (
                  <Select {...p} value={form.criteria_type} onChange={(e) => setForm({ ...form, criteria_type: e.target.value, criteria_value: "" })}>
                    {CRITERIA.map((c) => <option key={c.type} value={c.type}>{c.label}</option>)}
                  </Select>
                )}
              </Field>
              {crit?.param ? (
                <Field label={crit.param.label} required>
                  {(p) => (
                    <Input {...p} type={crit.param!.numeric ? "number" : "text"} min={crit.param!.numeric ? 1 : undefined} max={crit.param!.numeric ? 10000 : undefined} value={form.criteria_value} onChange={(e) => setForm({ ...form, criteria_value: e.target.value })} maxLength={80} />
                  )}
                </Field>
              ) : null}
            </>
          ) : null}
          <div className="sm:col-span-2"><FormError message={error && !Object.keys(f).length ? error.message : null} /></div>
          {created ? (
            <div className="sm:col-span-2">
              <InlineNotice tone="success" title={`Created “${created.name}”`}>Slug <span className="font-mono">{created.slug}</span>{created.is_manual ? " — you can award it below." : " — run “Recalculate” to award it to existing users."}</InlineNotice>
            </div>
          ) : null}
        </CardBody>
        <CardFooter>
          <Button type="submit" loading={create.isPending} disabled={!valid}>Create badge</Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function AwardBadgeCard() {
  const badges = useQuery({ queryKey: ["badges", "catalog"], queryFn: () => get<BadgeDef[]>("/badges") });
  const manual = (badges.data ?? []).filter((b) => b.is_manual);
  const [slug, setSlug] = useState("");
  const [handle, setHandle] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const award = useApiMutation((body: { badge_slug: string; handle: string; reason: string }) => post<Msg>("/badges/award", body), {
    success: (r) => r.message,
    invalidate: [["badges"]],
    onSuccess: () => {
      setHandle("");
      setReason("");
      setError(null);
    },
    onError: setError,
  });
  const valid = Boolean(slug) && handle.trim().replace(/^@/, "").length > 0 && reason.trim().length >= 3;

  return (
    <Card>
      <CardHeader title={<span className="inline-flex items-center gap-2"><Award className="h-4 w-4 text-accent-strong" aria-hidden />Award a manual badge</span>} description="The award shows as manually granted on the badge’s public verification page." />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          award.mutate({ badge_slug: slug, handle: handle.trim().replace(/^@/, ""), reason: reason.trim() });
        }}
        noValidate
      >
        <CardBody className="space-y-4">
          {badges.isPending ? (
            <Spinner label="Loading badges" />
          ) : badges.isError ? (
            <FormError message="Could not load the badge catalog." />
          ) : !manual.length ? (
            <InlineNotice tone="info">No manual badges exist yet. Create one above with “Manual badge” turned on.</InlineNotice>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Badge" required error={error?.fields.badge_slug}>
                {(p) => (
                  <Select {...p} value={slug} onChange={(e) => setSlug(e.target.value)}>
                    <option value="" disabled>Choose a badge</option>
                    {manual.map((b) => <option key={b.id} value={b.slug}>{b.name} ({b.awarded_count} awarded)</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Recipient handle" required error={error?.fields.handle}>
                {(p) => <Input {...p} value={handle} onChange={(e) => setHandle(e.target.value)} maxLength={32} placeholder="@handle" autoComplete="off" />}
              </Field>
              <Field label="Reason (shown as evidence, recorded in the audit log)" required error={error?.fields.reason} className="sm:col-span-2">
                {(p) => <Textarea {...p} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} rows={2} />}
              </Field>
            </div>
          )}
          <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
        </CardBody>
        {manual.length ? (
          <CardFooter>
            <Button type="submit" loading={award.isPending} disabled={!valid}>Award badge</Button>
          </CardFooter>
        ) : null}
      </form>
    </Card>
  );
}

export default function AdminToolsPage() {
  return (
    <div className="space-y-6">
      <AdminHeader title="Tools" description="Maintenance and credential tools. Every action is audited." />
      <MaintenanceCard />
      <CreateBadgeCard />
      <AwardBadgeCard />
      <PurgeDemoCard />
    </div>
  );
}
