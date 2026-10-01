"use client";

import { useQuery } from "@tanstack/react-query";
import { KeyRound, Link2, Mail, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAdminOrg, viewerIsOwnerLevel } from "@/components/orgs/org-admin-context";
import { ROLE_LABELS, RoleBadge } from "@/components/orgs/org-ui";
import type { Invite, InviteCreated, OrgRole } from "@/components/orgs/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select } from "@/components/ui/form";
import { CopyButton } from "@/components/ui/misc";
import { EmptyState, InlineNotice, QueryState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ApiError, del, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation, useMe } from "@/lib/hooks";

function inviteState(inv: Invite): { label: string; tone: "success" | "neutral" | "danger" | "warning" } {
  if (inv.revoked_at) return { label: "Revoked", tone: "danger" };
  if (new Date(inv.expires_at).getTime() < Date.now()) return { label: "Expired", tone: "neutral" };
  if (inv.uses >= inv.max_uses) return { label: "Used", tone: "neutral" };
  return { label: "Active", tone: "success" };
}

function CreateInvite({ slug, roles, onCreated }: { slug: string; roles: OrgRole[]; onCreated: (inv: InviteCreated) => void }) {
  const [mode, setMode] = useState<"email" | "link">("email");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [maxUses, setMaxUses] = useState("25");
  const [days, setDays] = useState("14");
  const [error, setError] = useState<ApiError | null>(null);
  const create = useApiMutation(
    (body: { email: string | null; role: OrgRole; max_uses: number; days: number }) => post<InviteCreated>(`/orgs/${slug}/invites`, body),
    {
      success: (inv) => (inv.email ? `Invite emailed to ${inv.email}` : "Invite link created"),
      invalidate: [["orgs", slug, "invites"]],
      onSuccess: (inv) => {
        setEmail("");
        setError(null);
        onCreated(inv);
      },
      onError: setError,
    },
  );
  const daysN = Math.min(90, Math.max(1, Number(days) || 14));
  const usesN = Math.min(1000, Math.max(1, Number(maxUses) || 1));
  const valid = mode === "link" || /.+@.+\..+/.test(email.trim());

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    create.mutate({ email: mode === "email" ? email.trim() : null, role, max_uses: mode === "email" ? 1 : usesN, days: daysN });
  }

  const fields = error?.fields ?? {};
  return (
    <Card>
      <CardHeader title="Create an invite" description="Invited people join as verified members with the chosen role." />
      <form onSubmit={submit} noValidate>
        <CardBody className="space-y-5">
          <fieldset>
            <legend className="text-sm font-medium text-fg">Invite type</legend>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {([
                { v: "email", icon: Mail, title: "Email one person", text: "Single use. Only the account with that email can accept." },
                { v: "link", icon: Link2, title: "Shareable link", text: "Anyone with the link can join, up to the usage limit." },
              ] as const).map((o) => (
                <label
                  key={o.v}
                  className={cn(
                    "flex cursor-pointer gap-3 rounded-[var(--radius-md)] border p-3 focus-within:ring-2 focus-within:ring-[var(--ring)]",
                    mode === o.v ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-2",
                  )}
                >
                  <input type="radio" name="invite-mode" value={o.v} checked={mode === o.v} onChange={() => setMode(o.v)} className="mt-1 h-4 w-4 accent-[var(--accent)]" />
                  <span>
                    <span className="flex items-center gap-1.5 text-sm font-medium text-fg"><o.icon className="h-4 w-4" aria-hidden />{o.title}</span>
                    <span className="mt-0.5 block text-xs text-muted">{o.text}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-5 sm:grid-cols-3">
            {mode === "email" ? (
              <Field label="Email" required error={fields.email} className="sm:col-span-3">
                {(p) => <Input {...p} type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={320} placeholder="name@example.edu" />}
              </Field>
            ) : (
              <Field label="Maximum uses" error={fields.max_uses} hint="1–1000">
                {(p) => <Input {...p} type="number" min={1} max={1000} value={maxUses} onChange={(e) => setMaxUses(e.target.value)} />}
              </Field>
            )}
            <Field label="Role" error={fields.role}>
              {(p) => (
                <Select {...p} value={role} onChange={(e) => setRole(e.target.value as OrgRole)}>
                  {roles.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Expires after (days)" error={fields.days} hint="1–90">
              {(p) => <Input {...p} type="number" min={1} max={90} value={days} onChange={(e) => setDays(e.target.value)} />}
            </Field>
          </div>
          <FormError message={error && !Object.keys(fields).length ? error.message : null} />
        </CardBody>
        <CardFooter>
          <Button type="submit" loading={create.isPending} disabled={!valid} icon={<Plus className="h-4 w-4" />}>
            {mode === "email" ? "Send invite" : "Create link"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function OrgInvitesPage() {
  const org = useAdminOrg();
  const me = useMe().data;
  const [created, setCreated] = useState<InviteCreated | null>(null);
  const invites = useQuery({ queryKey: ["orgs", org.slug, "invites"], queryFn: () => get<Invite[]>(`/orgs/${org.slug}/invites`) });
  const revoke = useApiMutation((id: string) => del<{ message: string }>(`/orgs/${org.slug}/invites/${id}`), {
    success: (r) => r.message,
    invalidate: [["orgs", org.slug, "invites"]],
  });
  const ownerLevel = viewerIsOwnerLevel(org, Boolean(me?.platform_roles.includes("platform_admin")));
  const roles: OrgRole[] = ownerLevel ? ["member", "manager", "admin"] : ["member", "manager"];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-fg">Invites</h1>
        <p className="mt-1 text-sm text-muted">Invite links verify membership. Treat them like passwords — anyone holding an open link can join.</p>
      </div>

      {created ? (
        <InlineNotice tone="success" title="Copy this link now — it won’t be shown again">
          <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
            <code className="block min-w-0 flex-1 break-all rounded-md border border-border bg-bg-elevated px-2 py-1.5 font-mono text-xs text-fg">{created.link}</code>
            <div className="flex shrink-0 gap-2">
              <CopyButton value={created.link} label="Copy link" />
              <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>Done</Button>
            </div>
          </div>
          <p className="mt-2 text-xs">
            {created.email ? `We also emailed it to ${created.email}. ` : ""}
            {ROLE_LABELS[created.role]} · {created.max_uses === 1 ? "single use" : `up to ${created.max_uses} uses`} · expires {formatDateTime(created.expires_at)}
          </p>
        </InlineNotice>
      ) : null}

      <CreateInvite slug={org.slug} roles={roles} onCreated={setCreated} />

      <section aria-labelledby="invites-heading">
        <h2 id="invites-heading" className="mb-3 text-base font-semibold text-fg">Invites</h2>
        <QueryState
          query={invites}
          loading={<SkeletonRows rows={4} />}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState icon={<KeyRound className="h-5 w-5" />} title="No invites yet" description="Create an email invite or a shareable link above." />}
        >
          {(rows) => (
            <Table>
              <THead>
                <tr>
                  <TH>Recipient</TH>
                  <TH>Role</TH>
                  <TH>Uses</TH>
                  <TH>Expires</TH>
                  <TH>Status</TH>
                  <TH>Created</TH>
                  <TH><span className="sr-only">Actions</span></TH>
                </tr>
              </THead>
              <TBody>
                {rows.map((inv) => {
                  const st = inviteState(inv);
                  return (
                    <TR key={inv.id}>
                      <TD>{inv.email ? <span className="text-fg">{inv.email}</span> : <span className="inline-flex items-center gap-1.5 text-muted"><Link2 className="h-3.5 w-3.5" aria-hidden />Open link</span>}</TD>
                      <TD><RoleBadge role={inv.role} /></TD>
                      <TD className="tabular-nums text-muted">{inv.uses} / {inv.max_uses}</TD>
                      <TD className="whitespace-nowrap text-muted" title={formatDateTime(inv.expires_at)}>{relativeTime(inv.expires_at)}</TD>
                      <TD><Badge tone={st.tone}>{st.label}</Badge></TD>
                      <TD className="whitespace-nowrap text-muted">{formatDate(inv.created_at)}</TD>
                      <TD className="text-right">
                        {st.label === "Active" ? (
                          <ConfirmDialog
                            trigger={<Button size="sm" variant="ghost" className="text-danger">Revoke</Button>}
                            title="Revoke this invite?"
                            description="The link stops working immediately. People who already joined keep their membership."
                            confirmLabel="Revoke invite"
                            onConfirm={async () => {
                              await revoke.mutateAsync(inv.id).catch(() => undefined);
                            }}
                          />
                        ) : null}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </QueryState>
      </section>
    </div>
  );
}
