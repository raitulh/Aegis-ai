"use client";
import { ShieldCheck, Trash2, UserPlus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { CopyButton, PlanLimitNotice, SectionTitle } from "@/components/ui/display";
import { Field, Input, Select } from "@/components/ui/forms";
import { Badge, Button, Card, CardBody, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import { useCan, useInvalidate, useRoles, useSession, useTeam } from "@/lib/queries";
import type { Member, RoleInfo } from "@/lib/types";
import { formatDateTime, initials, timeAgo, titleCase } from "@/lib/utils";

type InviteCreated = { message: string; invitation_id: string; invite_url: string; email_sent: boolean; expires_at: string };

export default function TeamPage() {
  const can = useCan();
  const router = useRouter();
  const search = useSearchParams();
  const { data: session } = useSession();
  const team = useTeam();
  const roles = useRoles();
  const invalidate = useInvalidate();
  const [inviting, setInviting] = useState(false);
  const [roleChange, setRoleChange] = useState<{ member: Member; role: string } | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const manage = can("team:manage");
  const assignable = (roles.data ?? []).filter((r) => r.assignable);

  const deepLink = search.get("invite") === "1" && manage;

  async function changeRole() {
    if (!roleChange) return;
    try {
      await api.patch(path`/team/${roleChange.member.membership_id}/role`, { role: roleChange.role });
      toast.success(`${roleChange.member.email} is now ${titleCase(roleChange.role)}`);
      invalidate("team");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  async function remove() {
    if (!removing) return;
    try {
      await api.delete(path`/team/${removing.membership_id}`);
      toast.success(`${removing.email} removed`);
      invalidate("team");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div>
      <PageHeader
        title="Members & roles"
        description="Who can access this workspace and what they can do. Every permission is enforced by the API; the interface only mirrors it."
        actions={
          manage ? (
            <Button icon={UserPlus} onClick={() => setInviting(true)}>
              Invite member
            </Button>
          ) : null
        }
      />
      <QueryBoundary query={team} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(members) => (
          <DataTable caption="Workspace members">
            <THead>
              <tr>
                <TH>Member</TH>
                <TH>Role</TH>
                <TH>Status</TH>
                <TH>Last active</TH>
                <TH />
              </tr>
            </THead>
            <tbody>
              {members.map((m) => {
                const self = m.user_id === session?.user.id;
                return (
                  <TR key={m.membership_id}>
                    <TD>
                      <div className="flex items-center gap-2.5">
                        <span aria-hidden className="grid h-7 w-7 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[10px] font-semibold text-[var(--color-accent-bright)]">
                          {initials(m.name, m.email)}
                        </span>
                        <div className="min-w-0">
                          <p className="truncate font-medium">
                            {m.name ?? m.email}
                            {self ? <span className="ml-1.5 text-xs text-[var(--color-text-subtle)]">(you)</span> : null}
                          </p>
                          {m.name ? <p className="truncate text-xs text-[var(--color-text-subtle)]">{m.email}</p> : null}
                        </div>
                      </div>
                    </TD>
                    <TD>
                      {manage && !self && m.role !== "owner" ? (
                        <Select aria-label={`Role for ${m.email}`} value={m.role} onChange={(e) => setRoleChange({ member: m, role: e.target.value })} className="h-8 w-44 text-xs">
                          {[...assignable.map((r) => r.role), ...(assignable.some((r) => r.role === m.role) ? [] : [m.role])].map((r) => (
                            <option key={r} value={r}>
                              {titleCase(r)}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        <Badge tone="accent">{titleCase(m.role)}</Badge>
                      )}
                    </TD>
                    <TD>
                      <StatusBadge status={m.status} />
                    </TD>
                    <TD className="text-[var(--color-text-subtle)]">{m.last_active_at ? timeAgo(m.last_active_at) : "—"}</TD>
                    <TD>
                      {manage && !self && m.role !== "owner" ? (
                        <button type="button" aria-label={`Remove ${m.email}`} onClick={() => setRemoving(m)} className="rounded-md p-1 text-[var(--color-text-subtle)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-critical)] focus-ring">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      ) : null}
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </DataTable>
        )}
      </QueryBoundary>

      <div className="mt-8">
        <SectionTitle>Roles</SectionTitle>
        <QueryBoundary query={roles} skeleton={<div className="h-40 skeleton rounded-[var(--radius-lg)]" />}>
          {(list) => (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {[...list].sort((a, b) => a.rank - b.rank).map((r) => (
                <RoleCard key={r.role} role={r} />
              ))}
            </div>
          )}
        </QueryBoundary>
      </div>

      <InviteDialog
        open={inviting || deepLink}
        onClose={() => {
          setInviting(false);
          if (deepLink) router.replace("/dashboard/team");
        }}
        roles={assignable}
      />
      <ConfirmDialog
        open={!!roleChange}
        onOpenChange={(o) => !o && setRoleChange(null)}
        tone="primary"
        title={`Change role to ${titleCase(roleChange?.role)}?`}
        confirmLabel="Change role"
        description={<>{roleChange?.member.email} gets exactly the permissions of the {titleCase(roleChange?.role)} role. Active sessions pick this up on their next request.</>}
        onConfirm={changeRole}
      />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.email}?`}
        confirmLabel="Remove member"
        description="They lose access immediately. Their past actions stay in the audit log and evidence."
        onConfirm={remove}
      />
    </div>
  );
}

function RoleCard({ role }: { role: RoleInfo }) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <CardBody className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="flex items-center gap-1.5 text-sm font-semibold">
            <ShieldCheck className="h-4 w-4 text-[var(--color-accent-bright)]" aria-hidden />
            {titleCase(role.role)}
          </p>
          {!role.assignable ? <Badge>Not assignable to people</Badge> : null}
        </div>
        <p className="text-sm text-[var(--color-text-muted)]">{role.description}</p>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="text-xs text-[var(--color-accent-bright)] hover:underline">
          {open ? "Hide" : "Show"} {role.permissions.length} permissions
        </button>
        {open ? (
          <ul className="flex flex-wrap gap-1">
            {role.permissions.map((p) => (
              <li key={p} className="rounded bg-[var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]">
                {p}
              </li>
            ))}
          </ul>
        ) : null}
      </CardBody>
    </Card>
  );
}

function InviteDialog({ open, onClose, roles }: { open: boolean; onClose: () => void; roles: RoleInfo[] }) {
  const invalidate = useInvalidate();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [created, setCreated] = useState<InviteCreated | null>(null);

  function close() {
    setEmail("");
    setRole("viewer");
    setError(null);
    setCreated(null);
    onClose();
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.post<InviteCreated>("/team/invite", { email, role }));
      invalidate("team");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()} title="Invite a member" description="Invitations expire. The person accepts with the link and joins with the role you choose.">
      {created ? (
        <div className="space-y-3 text-sm">
          <p className="text-[var(--color-success)]">{created.email_sent ? `Invitation emailed to ${email}.` : "Invitation created. Email delivery is not configured — share this link with them directly:"}</p>
          <div className="flex items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2">
            <code className="flex-1 truncate font-mono text-xs">{created.invite_url}</code>
            <CopyButton value={created.invite_url} label="Copy invite link" />
          </div>
          <p className="text-xs text-[var(--color-text-subtle)]">Expires {formatDateTime(created.expires_at)}. The link is shown only once.</p>
          <div className="flex justify-end">
            <Button onClick={close}>Done</Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Field label="Email">{(p) => <Input {...p} type="email" required autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Role" hint={roles.find((r) => r.role === role)?.description}>
            {(p) => (
              <Select {...p} value={role} onChange={(e) => setRole(e.target.value)}>
                {roles.map((r) => (
                  <option key={r.role} value={r.role}>
                    {titleCase(r.role)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {error ? <PlanLimitNotice error={error} /> : null}
          {error && !(error as { isPlanLimit?: boolean }).isPlanLimit ? (
            <p role="alert" className="text-sm text-[var(--color-critical)]">
              {errorMessage(error)}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" loading={busy} disabled={!email}>
              Send invitation
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
