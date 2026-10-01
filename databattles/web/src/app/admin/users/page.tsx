"use client";

import { useQuery } from "@tanstack/react-query";
import { Search, ShieldCheck, UserCog, Users } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AdminHeader, UserStatusBadge } from "@/components/admin/admin-ui";
import type { AdminUserRow } from "@/components/admin/types";
import { UserLink } from "@/components/domain/cards";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, NoResults, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, put } from "@/lib/api";
import { formatDate, formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation, useDebounced, useMe } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 25;
const ROLES = [
  { key: "platform_admin", label: "Platform admin", description: "Full access to the admin console, billing, flags and data tools." },
  { key: "moderator", label: "Moderator", description: "Reviews reports, hides content and suspends accounts." },
] as const;

function ManageUser({ row, isAdmin, isSelf }: { row: AdminUserRow; isAdmin: boolean; isSelf: boolean }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<"active" | "suspended" | "banned">(row.status === "deleted" ? "active" : row.status);
  const [statusReason, setStatusReason] = useState("");
  const [roleReason, setRoleReason] = useState("");
  const invalidate = [["admin", "users"]] as const;
  const setUserStatus = useApiMutation(
    (body: { status: string; reason: string }) => put<{ message: string }>(`/moderation/users/${row.user.id}/status`, body),
    { success: (r) => r.message, invalidate, onSuccess: () => setStatusReason("") },
  );
  const setRole = useApiMutation(
    (body: { role: string; grant: boolean; reason: string }) => put<{ message: string }>(`/admin/users/${row.user.id}/roles`, body),
    { success: (r) => r.message, invalidate, onSuccess: () => setRoleReason("") },
  );
  const staff = row.roles.length > 0;
  const canChangeStatus = !isSelf && (isAdmin || !staff);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setStatus(row.status === "deleted" ? "active" : row.status);
          setStatusReason("");
          setRoleReason("");
        }
      }}
      trigger={<Button size="sm" variant="ghost" icon={<UserCog className="h-4 w-4" />} aria-label={`Manage ${row.user.display_name}`}>Manage</Button>}
      title={`Manage @${row.user.handle}`}
      description="Every change requires a reason and is recorded in the audit log."
      size="md"
    >
      <div className="space-y-6">
        <section aria-labelledby={`status-${row.user.id}`} className="space-y-3">
          <h3 id={`status-${row.user.id}`} className="text-sm font-semibold text-fg">Account status</h3>
          <p className="text-xs text-muted">
            Current: <UserStatusBadge status={row.status} />
            {row.status_reason ? <span className="ml-1">— “{row.status_reason}”</span> : null}
          </p>
          {isSelf ? (
            <InlineNotice tone="info">You can’t change your own account status.</InlineNotice>
          ) : !canChangeStatus ? (
            <InlineNotice tone="info">Only platform admins can change the status of staff accounts.</InlineNotice>
          ) : (
            <>
              <Field label="New status">
                {(p) => (
                  <Select {...p} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                    <option value="active">Active</option>
                    <option value="suspended">Suspended — can’t sign in; sessions are revoked</option>
                    {isAdmin ? <option value="banned">Banned — permanent (admins only)</option> : null}
                  </Select>
                )}
              </Field>
              <Field label="Reason (recorded in the audit log)" required>
                {(p) => <Textarea {...p} value={statusReason} onChange={(e) => setStatusReason(e.target.value)} maxLength={500} rows={2} />}
              </Field>
              <div className="flex justify-end">
                <Button
                  variant={status === "active" ? "primary" : "danger"}
                  loading={setUserStatus.isPending}
                  disabled={statusReason.trim().length < 3 || status === row.status}
                  onClick={() => setUserStatus.mutate({ status, reason: statusReason.trim() })}
                >
                  {status === "active" ? "Reactivate account" : `Set ${status}`}
                </Button>
              </div>
            </>
          )}
        </section>

        {isAdmin ? (
          <section aria-labelledby={`roles-${row.user.id}`} className="space-y-3 border-t border-border pt-5">
            <h3 id={`roles-${row.user.id}`} className="text-sm font-semibold text-fg">Platform roles</h3>
            <Field label="Reason for role change" required hint="At least 3 characters.">
              {(p) => <Textarea {...p} value={roleReason} onChange={(e) => setRoleReason(e.target.value)} maxLength={500} rows={2} />}
            </Field>
            <ul className="space-y-2">
              {ROLES.map((r) => {
                const has = row.roles.includes(r.key);
                return (
                  <li key={r.key} className="flex items-start justify-between gap-3 rounded-[var(--radius-md)] border border-border p-3">
                    <div>
                      <p className="text-sm font-medium text-fg">{r.label} {has ? <Badge tone="accent" className="ml-1">Granted</Badge> : null}</p>
                      <p className="text-xs text-muted">{r.description}</p>
                    </div>
                    <Button
                      size="sm"
                      variant={has ? "danger" : "secondary"}
                      loading={setRole.isPending && setRole.variables?.role === r.key}
                      disabled={roleReason.trim().length < 3 || setRole.isPending || row.status === "deleted"}
                      onClick={() => setRole.mutate({ role: r.key, grant: !has, reason: roleReason.trim() })}
                    >
                      {has ? "Revoke" : "Grant"}
                    </Button>
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
      </div>
    </Dialog>
  );
}

function UsersList() {
  const me = useMe().data;
  const isAdmin = Boolean(me?.platform_roles.includes("platform_admin"));
  const [state, setState, reset] = useUrlState({ q: "", status: "", role: "", page: "1" });
  const [q, setQ] = useState(state.q ?? "");
  const debounced = useDebounced(q, 300);
  useEffect(() => {
    if (debounced !== (state.q ?? "")) setState({ q: debounced });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  const page = Math.max(1, Number(state.page) || 1);
  const filters = { q: state.q || undefined, status: state.status || undefined, role: state.role || undefined, page, page_size: PAGE_SIZE };
  const users = useQuery({ queryKey: ["admin", "users", filters], queryFn: () => get<Page<AdminUserRow>>("/admin/users", filters) });
  const filtered = Boolean(state.q || state.status || state.role);

  return (
    <div>
      <AdminHeader title="Users" description={isAdmin ? "Search by handle, name or email." : "Search by handle or name. Email lookup is limited to platform admins."} />
      <div className="mb-4 flex flex-col gap-3 sm:flex-row" role="search">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input aria-label="Search users" placeholder={isAdmin ? "Handle, name or email" : "Handle or name"} className="pl-9" value={q} onChange={(e) => setQ(e.target.value)} maxLength={80} />
        </div>
        <Select aria-label="Account status" className="sm:w-40" value={state.status ?? ""} onChange={(e) => setState({ status: e.target.value })}>
          <option value="">Any status</option>
          {["active", "suspended", "banned", "deleted"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
        </Select>
        <Select aria-label="Platform role" className="sm:w-44" value={state.role ?? ""} onChange={(e) => setState({ role: e.target.value })}>
          <option value="">Any role</option>
          {ROLES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </Select>
      </div>

      {users.isPending ? (
        <SkeletonRows rows={8} />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : users.data.items.length === 0 ? (
        filtered ? <NoResults onReset={() => { setQ(""); reset(); }} /> : <EmptyState icon={<Users className="h-5 w-5" />} title="No users yet" />
      ) : (
        <>
          <p className="mb-2 text-sm text-muted" aria-live="polite">{formatNumber(users.data.total)} users</p>
          <Table>
            <THead>
              <tr>
                <TH>User</TH>
                {isAdmin ? <TH>Email</TH> : null}
                <TH>Status</TH>
                <TH>Roles</TH>
                <TH>Joined</TH>
                <TH>Last sign-in</TH>
                <TH><span className="sr-only">Actions</span></TH>
              </tr>
            </THead>
            <TBody>
              {users.data.items.map((row) => (
                <TR key={row.user.id}>
                  <TD>
                    <div className="flex flex-col gap-1">
                      {row.status === "deleted" ? <span className="text-sm text-subtle">Deleted user</span> : <UserLink user={row.user} />}
                      <span className="text-xs text-subtle">@{row.user.handle}</span>
                      {row.is_demo ? <DemoBadge className="w-fit" /> : null}
                    </div>
                  </TD>
                  {isAdmin ? (
                    <TD className="text-xs">
                      <span className="break-all text-fg">{row.email ?? "—"}</span>
                      {!row.email_verified ? <Badge tone="warning" className="ml-1.5">Unverified</Badge> : null}
                    </TD>
                  ) : null}
                  <TD><UserStatusBadge status={row.status} title={row.status_reason ?? undefined} /></TD>
                  <TD>
                    <div className="flex flex-wrap gap-1">
                      {row.roles.length ? row.roles.map((r) => <Badge key={r} tone="accent" icon={<ShieldCheck className="h-3 w-3" aria-hidden />}>{titleCase(r)}</Badge>) : <span className="text-xs text-subtle">—</span>}
                    </div>
                  </TD>
                  <TD className="whitespace-nowrap text-muted">{formatDate(row.created_at)}</TD>
                  <TD className="whitespace-nowrap text-muted" title={formatDateTime(row.last_login_at)}>{row.last_login_at ? relativeTime(row.last_login_at) : "Never"}</TD>
                  <TD className="text-right">
                    {row.status !== "deleted" ? <ManageUser row={row} isAdmin={isAdmin} isSelf={me?.id === row.user.id} /> : null}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={users.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </div>
  );
}

export default function AdminUsersPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <UsersList />
    </Suspense>
  );
}
