"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Download, Search, UserMinus, UserCog, Users, X } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { UserLink } from "@/components/domain/cards";
import { useAdminOrg, viewerIsOwnerLevel } from "@/components/orgs/org-admin-context";
import { AdminPageHeader } from "@/components/orgs/org-visuals";
import { ROLE_DESCRIPTIONS, ROLE_LABELS, RoleBadge, VERIFICATION_METHOD_LABELS } from "@/components/orgs/org-ui";
import type { OrgDetail, OrgRole, RosterRow } from "@/components/orgs/types";
import { Badge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { API_BASE, get, post, put } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatNumber, titleCase } from "@/lib/format";
import { useApiMutation, useDebounced, useMe } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 25;
const RANK: Record<OrgRole, number> = { member: 0, manager: 1, admin: 2, owner: 3 };
const STATUSES = [
  { value: "active", label: "Active" },
  { value: "pending", label: "Pending requests" },
  { value: "rejected", label: "Declined" },
  { value: "removed", label: "Removed" },
];

function ReviewDialog({ org, row }: { org: OrgDetail; row: RosterRow }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const review = useApiMutation(
    (approve: boolean) => post<{ message: string }>(`/orgs/${org.slug}/members/${row.id}/review`, { approve, note: note.trim() || null }),
    {
      success: (r) => r.message,
      invalidate: [["orgs", org.slug]],
      onSuccess: () => {
        setOpen(false);
        setNote("");
      },
    },
  );
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setNote("");
      }}
      trigger={<Button size="sm" variant="secondary">Review</Button>}
      title={`Review request from ${row.user.display_name}`}
      description="Approving makes this a verified membership. The member is notified either way."
      footer={
        <>
          <Button variant="danger" icon={<X className="h-4 w-4" />} loading={review.isPending && review.variables === false} disabled={review.isPending} onClick={() => review.mutate(false)}>
            Decline
          </Button>
          <Button icon={<Check className="h-4 w-4" />} loading={review.isPending && review.variables === true} disabled={review.isPending} onClick={() => review.mutate(true)}>
            Approve
          </Button>
        </>
      }
    >
      <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border text-sm">
        <div className="bg-bg-elevated px-3.5 py-2.5">
          <dt className="text-eyebrow text-subtle">Applicant</dt>
          <dd className="mt-1.5"><UserLink user={row.user} /></dd>
        </div>
        <div className="bg-bg-elevated px-3.5 py-2.5">
          <dt className="text-eyebrow text-subtle">Department</dt>
          <dd className="mt-1 text-fg">{row.department ?? "Not specified"}</dd>
        </div>
        <div className="bg-bg-elevated px-3.5 py-2.5">
          <dt className="text-eyebrow text-subtle">Their note</dt>
          <dd className="mt-1 whitespace-pre-wrap text-fg">{row.request_note || <span className="text-subtle">No note.</span>}</dd>
        </div>
      </dl>
      <Field label="Private review note" hint="Optional. Recorded in the audit log; never shown to the applicant." className="mt-4">
        {(p) => <Textarea {...p} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={3} />}
      </Field>
    </Dialog>
  );
}

function RoleDialog({ org, row, allowed }: { org: OrgDetail; row: RosterRow; allowed: OrgRole[] }) {
  const [open, setOpen] = useState(false);
  const [role, setRole] = useState<OrgRole>(row.role);
  const change = useApiMutation((r: OrgRole) => put<{ message: string }>(`/orgs/${org.slug}/members/${row.id}/role`, { role: r }), {
    success: (r) => r.message,
    invalidate: [["orgs", org.slug]],
    onSuccess: () => setOpen(false),
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) setRole(row.role);
      }}
      trigger={<Button size="sm" variant="ghost" icon={<UserCog className="h-4 w-4" />}>Role</Button>}
      title={`Change role for ${row.user.display_name}`}
      description="The member is notified of the change. An organization always needs at least one owner."
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button loading={change.isPending} disabled={role === row.role} onClick={() => change.mutate(role)}>Save role</Button>
        </>
      }
    >
      <fieldset>
        <legend className="sr-only">Role</legend>
        <div className="space-y-2">
          {(["member", "manager", "admin", "owner"] as OrgRole[]).map((r) => {
            const enabled = allowed.includes(r);
            return (
              <label
                key={r}
                className={cn(
                  "flex gap-3 rounded-[var(--radius-md)] border p-3 transition-[background-color,border-color] duration-200 focus-within:ring-2 focus-within:ring-[var(--ring)]",
                  role === r ? "border-[color-mix(in_oklab,var(--accent)_55%,var(--border))] bg-accent-soft" : "border-border",
                  enabled ? "cursor-pointer hover:border-border-strong" : "cursor-not-allowed opacity-50",
                )}
              >
                <input type="radio" name={`role-${row.id}`} value={r} checked={role === r} disabled={!enabled} onChange={() => setRole(r)} className="mt-1 h-4 w-4 accent-[var(--accent)]" />
                <span>
                  <span className="block text-sm font-medium text-fg">{ROLE_LABELS[r]}</span>
                  <span className="block text-xs text-muted">{ROLE_DESCRIPTIONS[r]}</span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
    </Dialog>
  );
}

function Members() {
  const org = useAdminOrg();
  const me = useMe().data;
  const [state, setState, reset] = useUrlState({ status: "active", role: "", department_id: "", q: "", page: "1" });
  const [q, setQ] = useState(state.q ?? "");
  const debounced = useDebounced(q, 300);
  useEffect(() => {
    if (debounced !== (state.q ?? "")) setState({ q: debounced });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const page = Math.max(1, Number(state.page) || 1);
  const filters = {
    status: state.status || "active",
    role: state.role || undefined,
    department_id: state.department_id || undefined,
    q: state.q || undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const roster = useQuery({
    queryKey: ["orgs", org.slug, "members", filters],
    queryFn: () => get<Page<RosterRow>>(`/orgs/${org.slug}/members`, filters),
  });

  const canAct = org.viewer.can_manage;
  const ownerLevel = viewerIsOwnerLevel(org, Boolean(me?.platform_roles.includes("platform_admin")));
  const myRank = ownerLevel ? RANK.owner : org.viewer.role ? RANK[org.viewer.role] : -1;
  const allowedRoles: OrgRole[] = ownerLevel ? ["member", "manager", "admin", "owner"] : (["member", "manager", "admin", "owner"] as OrgRole[]).filter((r) => RANK[r] < myRank);
  const canTouch = (row: RosterRow) => ownerLevel || RANK[row.role] < myRank;
  const filtered = Boolean(state.role || state.department_id || state.q);
  const status = filters.status;

  const remove = useApiMutation(
    ({ id, reason }: { id: string; reason: string }) => post<{ message: string }>(`/orgs/${org.slug}/members/${id}/remove`, { reason }),
    { success: (r) => r.message, invalidate: [["orgs", org.slug]] },
  );

  return (
    <div className="space-y-5">
      <AdminPageHeader
        eyebrow="People"
        title="Members"
        description="Handles, names and roles only — personal emails are never shown."
        actions={
          canAct ? (
            <a
              href={`${API_BASE}/orgs/${org.slug}/members.csv`}
              download
              className="inline-flex h-9 items-center gap-2 self-start rounded-[var(--radius-md)] border border-border bg-surface-2 px-3 text-sm font-medium text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)] transition-colors hover:border-border-strong hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
            >
              <Download className="h-4 w-4" aria-hidden /> Export CSV
            </a>
          ) : null
        }
      />

      <div className="rounded-[var(--radius-lg)] border border-border bg-surface/70 p-2.5 shadow-card">
        {/* Status on its own row: the content column is capped (max-w-7xl minus the rail), so sharing a row with
            the search and both selects squeezes the search field at every desktop width. */}
        <div className="flex flex-col gap-2.5">
          <div
            className="inline-flex max-w-full shrink-0 items-center self-start gap-0.5 overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-0.5 [scrollbar-width:none]"
            role="group"
            aria-label="Membership status"
          >
            {STATUSES.map((s) => (
              <button
                key={s.value}
                type="button"
                aria-pressed={status === s.value}
                onClick={() => setState({ status: s.value })}
                className={cn(
                  "inline-flex h-9 shrink-0 items-center rounded-[8px] px-3 text-[13px] font-medium transition-[background-color,color,box-shadow] duration-200",
                  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)]",
                  status === s.value ? "bg-surface-3 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight),0_1px_2px_rgb(0_0_0/0.2)]" : "text-muted hover:text-fg",
                )}
              >
                {s.label}
              </button>
            ))}
          </div>

          <div className="flex min-w-0 flex-1 flex-col gap-2.5 sm:flex-row" role="search">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
              <Input aria-label="Search members by handle or name" placeholder="Search handle or name" className="pl-9" value={q} onChange={(e) => setQ(e.target.value)} maxLength={60} />
            </div>
            <Select aria-label="Filter by role" className="sm:w-40" value={state.role ?? ""} onChange={(e) => setState({ role: e.target.value })}>
              <option value="">All roles</option>
              {(["owner", "admin", "manager", "member"] as OrgRole[]).map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </Select>
            {org.departments.length ? (
              <Select aria-label="Filter by department" className="sm:w-52" value={state.department_id ?? ""} onChange={(e) => setState({ department_id: e.target.value })}>
                <option value="">All departments</option>
                {org.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            ) : null}
          </div>
        </div>
      </div>

      <div>
        {roster.isPending ? (
          <SkeletonRows rows={6} />
        ) : roster.isError ? (
          <ErrorState error={roster.error} onRetry={() => roster.refetch()} />
        ) : roster.data.items.length === 0 ? (
          filtered ? (
            <NoResults onReset={() => { setQ(""); reset(); }} />
          ) : (
            <EmptyState
              icon={<Users />}
              title={status === "pending" ? "No pending requests" : status === "active" ? "No members yet" : `No ${STATUSES.find((s) => s.value === status)?.label.toLowerCase()} memberships`}
              description={status === "pending" ? "New requests appear here and admins are notified." : status === "active" ? "Invite people or enable membership requests in settings." : undefined}
            />
          )
        ) : (
          <>
            <p className="mb-3 text-sm text-muted" aria-live="polite">
              <span className="tabular font-medium text-fg">{formatNumber(roster.data.total)}</span> {roster.data.total === 1 ? "membership" : "memberships"}
              <span className="text-subtle"> · {STATUSES.find((s) => s.value === status)?.label.toLowerCase() ?? status}</span>
            </p>
            <Table className="relative">
              <THead>
                <tr>
                  <TH>Member</TH>
                  <TH>Role</TH>
                  <TH className="hidden 2xl:table-cell">Department</TH>
                  <TH>Verification</TH>
                  <TH>{status === "pending" ? "Requested" : "Joined"}</TH>
                  <TH className="text-right">Competitions</TH>
                  {canAct && (status === "active" || status === "pending") ? <TH className="text-right"><span className="sr-only">Actions</span></TH> : null}
                </tr>
              </THead>
              <TBody>
                {roster.data.items.map((row) => (
                  <TR key={row.id}>
                    <TD className="min-w-56">
                      <div className="flex flex-col gap-1">
                        <UserLink user={row.user} size={28} className="font-medium" />
                        <span className="pl-9 font-mono text-[11px] text-subtle">
                          @{row.user.handle}
                          {me && row.user.id === me.id ? <span className="font-sans text-accent-strong"> · you</span> : ""}
                        </span>
                        {/* Department moves under the name until the wide layout has room for its own column. */}
                        <span className="pl-9 text-xs text-muted 2xl:hidden">
                          <span className="sr-only">Department: </span>
                          {row.department ?? <span className="text-subtle">No department</span>}
                        </span>
                        {row.account_status !== "active" ? <Badge tone="danger" className="ml-9 w-fit">Account {row.account_status}</Badge> : null}
                        {status === "pending" && row.request_note ? <p className="ml-9 max-w-xs text-xs italic text-muted line-clamp-2">“{row.request_note}”</p> : null}
                      </div>
                    </TD>
                    <TD>{status === "pending" ? <StatusBadge status="pending" /> : <RoleBadge role={row.role} />}</TD>
                    <TD className="hidden whitespace-nowrap text-muted 2xl:table-cell">{row.department ?? "—"}</TD>
                    <TD>
                      {row.verified ? (
                        <VerifiedBadge label={VERIFICATION_METHOD_LABELS[row.verification_method] ?? titleCase(row.verification_method)} title="Verified membership" />
                      ) : (
                        <SelfDeclaredBadge />
                      )}
                    </TD>
                    <TD className="tabular whitespace-nowrap text-muted">{formatDate(row.joined_at)}</TD>
                    <TD className="tabular text-right">{formatNumber(row.competitions_joined)}</TD>
                    {canAct && (status === "active" || status === "pending") ? (
                      <TD>
                        <div className="flex justify-end gap-1">
                          {status === "pending" ? (
                            <ReviewDialog org={org} row={row} />
                          ) : canTouch(row) ? (
                            <>
                              <RoleDialog org={org} row={row} allowed={allowedRoles} />
                              <ConfirmDialog
                                trigger={<Button size="sm" variant="ghost" className="text-danger" icon={<UserMinus className="h-4 w-4" />}>Remove</Button>}
                                title={`Remove ${row.user.display_name}?`}
                                description="Their membership and its verification are removed. They can rejoin only by verifying again or being approved."
                                confirmLabel="Remove member"
                                requireReason
                                onConfirm={async (reason) => {
                                  await remove.mutateAsync({ id: row.id, reason }).catch(() => undefined);
                                }}
                              />
                            </>
                          ) : (
                            <span className="text-xs text-subtle">—</span>
                          )}
                        </div>
                      </TD>
                    ) : null}
                  </TR>
                ))}
              </TBody>
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} total={roster.data.total} onPage={(p) => setState({ page: String(p) })} />
          </>
        )}
      </div>
    </div>
  );
}

export default function OrgMembersPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={6} />}>
      <Members />
    </Suspense>
  );
}
