"use client";

import { useQuery } from "@tanstack/react-query";
import { Search, ShieldCheck, UserCog, Users } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AdminHeader, EmailText, FilterBar, ResultCount, TableSkeleton, UserStatusBadge } from "@/components/admin/admin-ui";
import type { AdminUserRow } from "@/components/admin/types";
import { UserLink } from "@/components/domain/cards";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, NoResults } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, put } from "@/lib/api";
import { formatDate, formatDateTime, relativeTime, titleCase } from "@/lib/format";
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
          <h3 id={`status-${row.user.id}`} className="text-eyebrow text-subtle">Account status</h3>
          <div className="flex flex-wrap items-center gap-2 rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2.5 text-xs text-muted">
            <span>Current</span> <UserStatusBadge status={row.status} />
            {row.status_reason ? <span className="min-w-0 break-words">— “{row.status_reason}”</span> : null}
          </div>
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
            <h3 id={`roles-${row.user.id}`} className="text-eyebrow text-subtle">Platform roles</h3>
            <Field label="Reason for role change" required hint="At least 3 characters.">
              {(p) => <Textarea {...p} value={roleReason} onChange={(e) => setRoleReason(e.target.value)} maxLength={500} rows={2} />}
            </Field>
            <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-md)] border border-border">
              {ROLES.map((r) => {
                const has = row.roles.includes(r.key);
                return (
                  <li key={r.key} className="flex items-start justify-between gap-3 bg-bg-elevated/50 p-3">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-fg">{r.label} {has ? <Badge tone="accent" icon={<ShieldCheck className="h-3 w-3" aria-hidden />}>Granted</Badge> : null}</p>
                      <p className="mt-0.5 text-xs leading-relaxed text-muted">{r.description}</p>
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
      <AdminHeader
        eyebrow="People"
        icon={<Users />}
        title="Users"
        description={isAdmin ? "Search by handle, name or email." : "Search by handle or name. Email lookup is limited to platform admins."}
      />
      <FilterBar role="search">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input aria-label="Search users" placeholder={isAdmin ? "Handle, name or email" : "Handle or name"} className="pl-9" value={q} onChange={(e) => setQ(e.target.value)} maxLength={80} />
        </div>
        <div className="grid grid-cols-2 gap-2.5 sm:flex">
          <Select aria-label="Account status" className="sm:w-40" value={state.status ?? ""} onChange={(e) => setState({ status: e.target.value })}>
            <option value="">Any status</option>
            {["active", "suspended", "banned", "deleted"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </Select>
          <Select aria-label="Platform role" className="sm:w-44" value={state.role ?? ""} onChange={(e) => setState({ role: e.target.value })}>
            <option value="">Any role</option>
            {ROLES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </Select>
        </div>
      </FilterBar>

      {users.isPending ? (
        <TableSkeleton rows={8} cols={6} />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : users.data.items.length === 0 ? (
        filtered ? <NoResults onReset={() => { setQ(""); reset(); }} /> : <EmptyState icon={<Users className="h-5 w-5" />} title="No users yet" />
      ) : (
        <>
          <ResultCount total={users.data.total} noun="users" />
          <Table>
            <THead>
              <tr>
                <TH>User</TH>
                {isAdmin ? <TH>Email</TH> : null}
                <TH>Status</TH>
                <TH>Roles</TH>
                <TH className="leading-relaxed"><span className="block">Joined</span><span className="block">Last sign-in</span></TH>
                <TH className="relative"><span className="sr-only">Actions</span></TH>
              </tr>
            </THead>
            <TBody>
              {users.data.items.map((row) => (
                <TR key={row.user.id}>
                  <TD className="min-w-44">
                    <div className="flex min-w-0 flex-col gap-0.5">
                      {row.status === "deleted" ? <span className="text-sm text-subtle">Deleted user</span> : <UserLink user={row.user} className="font-medium" />}
                      <span className={`flex flex-wrap items-center gap-1.5 ${row.status === "deleted" ? "" : "pl-8"}`}>
                        <span className="font-mono text-[11px] text-subtle">@{row.user.handle}</span>
                        {row.is_demo ? <DemoBadge className="w-fit" /> : null}
                      </span>
                    </div>
                  </TD>
                  {isAdmin ? (
                    <TD className="min-w-48 text-xs">
                      {row.email ? <EmailText email={row.email} className="text-fg" /> : <span className="text-fg">—</span>}
                      {!row.email_verified ? <Badge tone="warning" className="ml-1.5">Unverified</Badge> : null}
                    </TD>
                  ) : null}
                  <TD><UserStatusBadge status={row.status} title={row.status_reason ?? undefined} /></TD>
                  <TD>
                    <div className="flex flex-wrap gap-1">
                      {row.roles.length ? row.roles.map((r) => <Badge key={r} tone="accent" icon={<ShieldCheck className="h-3 w-3" aria-hidden />}>{titleCase(r)}</Badge>) : <span className="text-xs text-subtle">—</span>}
                    </div>
                  </TD>
                  <TD className="tabular relative whitespace-nowrap text-xs">
                    <span className="block text-fg"><span className="sr-only">Joined </span>{formatDate(row.created_at)}</span>
                    <span className="mt-0.5 block text-subtle" title={formatDateTime(row.last_login_at)}>
                      <span className="sr-only">Last sign-in: </span>{row.last_login_at ? relativeTime(row.last_login_at) : "Never"}
                    </span>
                  </TD>
                  <TD className="pl-1 pr-3 text-right">
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
    <Suspense fallback={<TableSkeleton rows={8} cols={6} />}>
      <UsersList />
    </Suspense>
  );
}
