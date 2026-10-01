"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Building2, ExternalLink, Search, ShieldX } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { AdminHeader } from "@/components/admin/admin-ui";
import type { VerificationQueueRow } from "@/components/admin/types";
import { OrgLogo, OrgVerificationBadge } from "@/components/orgs/org-ui";
import type { OrgDetail, PlanInfo } from "@/components/orgs/types";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select, TagInput, Textarea } from "@/components/ui/form";
import { EmptyState, ErrorState, QueryState, SkeletonRows, Spinner } from "@/components/ui/states";
import { ApiError, get, patch, put } from "@/lib/api";
import { formatDate, formatMoney, titleCase } from "@/lib/format";
import { useApiMutation, useDebounced } from "@/lib/hooks";
import type { OrgCard, Page } from "@/lib/types";

const INVALIDATE = [["admin", "verification-queue"], ["orgs"]] as const;

function VerifyActions({ slug, name, status }: { slug: string; name: string; status: string }) {
  const set = useApiMutation(
    (body: { status: "verified" | "unverified" | "pending"; reason: string }) => put<{ message: string }>(`/orgs/${slug}/verification`, body),
    { success: (r) => r.message, invalidate: INVALIDATE },
  );
  return (
    <div className="flex flex-wrap gap-2">
      {status !== "verified" ? (
        <ConfirmDialog
          trigger={<Button size="sm" icon={<BadgeCheck className="h-4 w-4" />}>Verify</Button>}
          title={`Verify ${name}?`}
          description="Verified organizations show a badge, and — once email domains are set — members can verify with institutional email."
          confirmLabel="Mark verified"
          tone="primary"
          requireReason
          reasonLabel="How was this confirmed? (recorded in the audit log)"
          onConfirm={async (reason) => {
            await set.mutateAsync({ status: "verified", reason }).catch(() => undefined);
          }}
        />
      ) : null}
      {status !== "unverified" ? (
        <ConfirmDialog
          trigger={<Button size="sm" variant="secondary" className="text-danger" icon={<ShieldX className="h-4 w-4" />}>{status === "verified" ? "Revoke verification" : "Reject"}</Button>}
          title={status === "verified" ? `Revoke verification for ${name}?` : `Reject verification for ${name}?`}
          description={status === "verified" ? "Institutional-email verification stops working for this organization. Existing verified memberships remain." : "The organization returns to unverified and can request again."}
          confirmLabel={status === "verified" ? "Revoke" : "Reject"}
          requireReason
          onConfirm={async (reason) => {
            await set.mutateAsync({ status: "unverified", reason }).catch(() => undefined);
          }}
        />
      ) : null}
    </div>
  );
}

function DomainsEditor({ slug, initial, known }: { slug: string; initial: string[]; known: boolean }) {
  const [domains, setDomains] = useState<string[]>(initial);
  const [error, setError] = useState<string | null>(null);
  const save = useApiMutation((email_domains: string[]) => patch<OrgDetail>(`/orgs/${slug}`, { email_domains }), {
    success: "Email domains saved",
    invalidate: INVALIDATE,
    onSuccess: () => setError(null),
    onError: (e: ApiError) => setError(e.fields.email_domains ?? null),
  });
  const dirty = JSON.stringify(domains) !== JSON.stringify(initial);
  return (
    <div className="space-y-2">
      <Field
        label="Institutional email domains"
        error={error}
        hint={known ? "Press Enter after each domain, e.g. example.edu. Subdomains are accepted. Public providers (gmail.com, …) are rejected." : "Current domains are only returned for verified organizations; saving replaces the full list."}
      >
        {(p) => <TagInput id={p.id} value={domains} onChange={setDomains} placeholder="example.edu" max={20} />}
      </Field>
      <div className="flex justify-end">
        <Button size="sm" variant="secondary" loading={save.isPending} disabled={!dirty} onClick={() => save.mutate(domains.map((d) => d.replace(/^@/, "")))}>
          Save domains
        </Button>
      </div>
    </div>
  );
}

function PlanChanger({ orgId, current }: { orgId: string; current: string | null }) {
  const plans = useQuery({ queryKey: ["billing", "plans"], queryFn: () => get<PlanInfo[]>("/billing/plans"), staleTime: 5 * 60_000 });
  const [planKey, setPlanKey] = useState(current ?? "");
  const [reason, setReason] = useState("");
  const change = useApiMutation((body: { plan_key: string; reason: string }) => put<{ message: string }>(`/admin/orgs/${orgId}/plan`, body), {
    success: (r) => r.message,
    invalidate: [["orgs"], ["admin", "revenue"]],
    onSuccess: () => setReason(""),
  });
  if (plans.isPending) return <Spinner label="Loading plans" />;
  if (plans.isError) return <ErrorState error={plans.error} onRetry={() => plans.refetch()} />;
  return (
    <div className="space-y-3">
      <Field label="Plan" hint={current ? `Current: ${plans.data.find((p) => p.key === current)?.name ?? current}` : undefined}>
        {(p) => (
          <Select {...p} value={planKey} onChange={(e) => setPlanKey(e.target.value)}>
            <option value="" disabled>Choose a plan</option>
            {plans.data.map((pl) => <option key={pl.key} value={pl.key}>{pl.name} — {pl.price_cents_monthly ? `${formatMoney(pl.price_cents_monthly)}/mo` : "Free"}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Reason (recorded in the audit log)" required>
        {(p) => <Textarea {...p} value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={500} placeholder="e.g. Invoice #1234 paid" />}
      </Field>
      <div className="flex justify-end">
        <Button size="sm" loading={change.isPending} disabled={!planKey || planKey === current || reason.trim().length < 3} onClick={() => change.mutate({ plan_key: planKey, reason: reason.trim() })}>
          Change plan
        </Button>
      </div>
    </div>
  );
}

function VerificationQueue() {
  const queue = useQuery({ queryKey: ["admin", "verification-queue"], queryFn: () => get<VerificationQueueRow[]>("/admin/orgs/verification-queue") });
  return (
    <section aria-labelledby="queue-heading">
      <h2 id="queue-heading" className="mb-3 text-base font-semibold text-fg">
        Verification requests {queue.data?.length ? <Badge tone="warning" className="ml-1 align-middle">{queue.data.length}</Badge> : null}
      </h2>
      <QueryState
        query={queue}
        loading={<SkeletonRows rows={3} />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={<BadgeCheck className="h-5 w-5" />} title="No pending verification requests" description="Organizations request verification from their admin settings." />}
      >
        {(rows) => (
          <div className="space-y-4">
            {rows.map((r) => (
              <Card key={r.org.id}>
                <CardBody className="space-y-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex min-w-0 items-center gap-3">
                      <OrgLogo name={r.org.name} logoUrl={r.org.logo_url} size={40} />
                      <div className="min-w-0">
                        <Link href={`/orgs/${r.org.slug}`} className="block truncate font-medium text-fg hover:text-accent-strong">{r.org.name}</Link>
                        <p className="text-xs text-subtle">{titleCase(r.org.type)} · created {formatDate(r.created_at)}</p>
                      </div>
                    </div>
                    <VerifyActions slug={r.org.slug} name={r.org.name} status={r.org.verification_status} />
                  </div>
                  <dl className="grid gap-3 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-xs text-subtle">Website</dt>
                      <dd className="mt-0.5">
                        {r.website_url ? (
                          <a href={r.website_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 break-all text-accent-strong hover:underline">
                            {r.website_url} <ExternalLink className="h-3 w-3 shrink-0" aria-hidden /><span className="sr-only">(opens in a new tab)</span>
                          </a>
                        ) : <span className="text-subtle">Not provided</span>}
                      </dd>
                    </div>
                    <div className="sm:col-span-2">
                      <dt className="text-xs text-subtle">Request note</dt>
                      <dd className="mt-0.5 whitespace-pre-wrap text-fg">{r.requested_note ?? <span className="text-subtle">No note.</span>}</dd>
                    </div>
                  </dl>
                  <div className="border-t border-border pt-4">
                    <DomainsEditor slug={r.org.slug} initial={r.email_domains} known />
                  </div>
                </CardBody>
              </Card>
            ))}
          </div>
        )}
      </QueryState>
    </section>
  );
}

function ManageOrg({ slug }: { slug: string }) {
  const org = useQuery({ queryKey: ["orgs", slug], queryFn: () => get<OrgDetail>(`/orgs/${slug}`) });
  if (org.isPending) return <Spinner />;
  if (org.isError) return <ErrorState error={org.error} onRetry={() => org.refetch()} />;
  const o = org.data;
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {o.name} <OrgVerificationBadge status={o.verification_status} /> {o.is_demo ? <DemoBadge /> : null}
          </span>
        }
        description={`${titleCase(o.type)} · ${o.member_count} members`}
        action={<Link href={`/orgs/${o.slug}/admin`} className="text-sm text-accent-strong hover:underline">Open org admin</Link>}
      />
      <CardBody className="space-y-6">
        <div>
          <p className="mb-2 text-sm font-medium text-fg">Verification</p>
          <VerifyActions slug={o.slug} name={o.name} status={o.verification_status} />
        </div>
        <div className="border-t border-border pt-5">
          <DomainsEditor key={o.email_domains.join(",")} slug={o.slug} initial={o.email_domains} known={o.verification_status === "verified"} />
        </div>
        <div className="border-t border-border pt-5">
          <p className="mb-2 text-sm font-medium text-fg">Subscription plan</p>
          <PlanChanger key={o.plan_key ?? ""} orgId={o.id} current={o.plan_key} />
        </div>
      </CardBody>
    </Card>
  );
}

function OrgFinder() {
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const debounced = useDebounced(q.trim(), 300);
  const results = useQuery({
    queryKey: ["orgs", "list", { q: debounced, admin: true }],
    queryFn: () => get<Page<OrgCard>>("/orgs", { q: debounced, page_size: 10 }),
    enabled: debounced.length >= 2,
  });
  return (
    <section aria-labelledby="manage-heading" className="space-y-4">
      <div>
        <h2 id="manage-heading" className="text-base font-semibold text-fg">Manage an organization</h2>
        <p className="mt-1 text-sm text-muted">Set verification, email domains or the subscription plan for any organization.</p>
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
        <Input aria-label="Find an organization" placeholder="Search organizations by name or city" className="pl-9" value={q} onChange={(e) => setQ(e.target.value)} maxLength={80} />
      </div>
      {debounced.length >= 2 ? (
        results.isPending ? <SkeletonRows rows={3} /> : results.isError ? <ErrorState error={results.error} onRetry={() => results.refetch()} /> : results.data.items.length === 0 ? (
          <p className="text-sm text-subtle">No organizations match “{debounced}”.</p>
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" aria-label="Matching organizations">
            {results.data.items.map((o) => (
              <li key={o.id}>
                <button
                  type="button"
                  aria-pressed={selected === o.slug}
                  onClick={() => setSelected(o.slug)}
                  className={`flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-2 ${selected === o.slug ? "bg-surface-2" : ""}`}
                >
                  <OrgLogo name={o.name} logoUrl={o.logo_url} accentColor={o.accent_color} size={32} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-fg">{o.name}</span>
                    <span className="block text-xs text-subtle">{titleCase(o.type)}{o.city ? ` · ${o.city}` : ""}</span>
                  </span>
                  <OrgVerificationBadge status={o.verification_status} />
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
      {selected ? <ManageOrg key={selected} slug={selected} /> : (
        <p className="flex items-center gap-2 text-sm text-subtle"><Building2 className="h-4 w-4" aria-hidden />Search and pick an organization to manage it.</p>
      )}
    </section>
  );
}

export default function AdminOrgsPage() {
  return (
    <div className="space-y-10">
      <AdminHeader title="Organizations" description="Verification protects students: only verified organizations with platform-set domains can verify membership by email." />
      <VerificationQueue />
      <OrgFinder />
    </div>
  );
}
