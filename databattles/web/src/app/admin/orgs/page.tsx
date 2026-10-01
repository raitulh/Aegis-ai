"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, BadgeCheck, Building2, ExternalLink, Search, ShieldX } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { AdminHeader, PanelHeader, SectionHeading } from "@/components/admin/admin-ui";
import type { VerificationQueueRow } from "@/components/admin/types";
import { OrgLogo, OrgVerificationBadge } from "@/components/orgs/org-ui";
import type { OrgDetail, PlanInfo } from "@/components/orgs/types";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select, TagInput, Textarea } from "@/components/ui/form";
import { EmptyState, ErrorState, QueryState, Skeleton, SkeletonRows, Spinner } from "@/components/ui/states";
import { ApiError, get, patch, put } from "@/lib/api";
import { cn } from "@/lib/cn";
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
      <SectionHeading
        id="queue-heading"
        eyebrow="Review"
        title={<>Verification requests {queue.data?.length ? <Badge tone="warning" className="tabular">{queue.data.length}</Badge> : null}</>}
        description="Confirm each organization is genuine before marking it verified. Every decision needs a reason."
      />
      <QueryState
        query={queue}
        loading={<SkeletonRows rows={3} />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={<BadgeCheck className="h-5 w-5" />} title="No pending verification requests" description="Organizations request verification from their admin settings." />}
      >
        {(rows) => (
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
            {rows.map((r) => (
              <li key={r.org.id} className="space-y-5 p-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-center gap-3">
                    <OrgLogo name={r.org.name} logoUrl={r.org.logo_url} size={40} />
                    <div className="min-w-0">
                      <Link href={`/orgs/${r.org.slug}`} className="block truncate font-medium text-fg hover:text-accent-strong">{r.org.name}</Link>
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
                        <span>{titleCase(r.org.type)}</span>
                        <span aria-hidden>·</span>
                        <span className="tabular">created {formatDate(r.created_at)}</span>
                        <OrgVerificationBadge status={r.org.verification_status} />
                      </p>
                    </div>
                  </div>
                  <VerifyActions slug={r.org.slug} name={r.org.name} status={r.org.verification_status} />
                </div>
                <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
                  <div className="min-w-0 bg-bg-elevated px-3.5 py-3">
                    <dt className="text-eyebrow text-subtle">Website</dt>
                    <dd className="mt-1">
                      {r.website_url ? (
                        <a href={r.website_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 break-all text-accent-strong hover:underline">
                          {r.website_url} <ExternalLink className="h-3 w-3 shrink-0" aria-hidden /><span className="sr-only">(opens in a new tab)</span>
                        </a>
                      ) : <span className="text-subtle">Not provided</span>}
                    </dd>
                  </div>
                  <div className="min-w-0 bg-bg-elevated px-3.5 py-3">
                    <dt className="text-eyebrow text-subtle">Request note</dt>
                    <dd className="mt-1 whitespace-pre-wrap break-words text-fg">{r.requested_note ?? <span className="text-subtle">No note.</span>}</dd>
                  </div>
                </dl>
                <div className="border-t border-border pt-4">
                  <DomainsEditor slug={r.org.slug} initial={r.email_domains} known />
                </div>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </section>
  );
}

function ManageOrg({ slug }: { slug: string }) {
  const org = useQuery({ queryKey: ["orgs", slug], queryFn: () => get<OrgDetail>(`/orgs/${slug}`) });
  if (org.isPending) {
    return (
      <div className="space-y-4 rounded-[var(--radius-lg)] border border-border bg-surface p-5" role="status" aria-label="Loading organization">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-3 w-32" />
        <Skeleton className="mt-6 h-9 w-40" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (org.isError) return <ErrorState error={org.error} onRetry={() => org.refetch()} />;
  const o = org.data;
  return (
    <Card className="animate-rise">
      <PanelHeader
        icon={<Building2 />}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {o.name} <OrgVerificationBadge status={o.verification_status} /> {o.is_demo ? <DemoBadge /> : null}
          </span>
        }
        description={<span className="tabular">{`${titleCase(o.type)} · ${o.member_count} members`}</span>}
        action={
          <Link href={`/orgs/${o.slug}/admin`} className="inline-flex items-center gap-1 whitespace-nowrap text-sm text-accent-strong hover:underline">
            Open org admin <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        }
      />
      <CardBody className="divide-y divide-border p-0">
        <div className="px-5 py-5">
          <p className="mb-3 text-eyebrow text-subtle">Verification</p>
          <VerifyActions slug={o.slug} name={o.name} status={o.verification_status} />
        </div>
        <div className="px-5 py-5">
          <DomainsEditor key={o.email_domains.join(",")} slug={o.slug} initial={o.email_domains} known={o.verification_status === "verified"} />
        </div>
        <div className="px-5 py-5">
          <p className="mb-3 text-eyebrow text-subtle">Subscription plan</p>
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
    <section aria-labelledby="manage-heading">
      <SectionHeading
        id="manage-heading"
        eyebrow="Directory"
        title="Manage an organization"
        description="Set verification, email domains or the subscription plan for any organization."
      />
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] xl:items-start">
        <div className="min-w-0 space-y-3">
          <div className="rounded-[var(--radius-lg)] border border-border surface-glass p-2.5 shadow-card" role="search">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
              <Input aria-label="Find an organization" placeholder="Search organizations by name or city" className="pl-9" value={q} onChange={(e) => setQ(e.target.value)} maxLength={80} />
            </div>
          </div>
          {debounced.length >= 2 ? (
            results.isPending ? <SkeletonRows rows={3} /> : results.isError ? <ErrorState error={results.error} onRetry={() => results.refetch()} /> : results.data.items.length === 0 ? (
              <p className="rounded-[var(--radius-lg)] border border-dashed border-border-strong px-4 py-6 text-center text-sm text-subtle">No organizations match “{debounced}”.</p>
            ) : (
              <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card" aria-label="Matching organizations">
                {results.data.items.map((o) => (
                  <li key={o.id}>
                    <button
                      type="button"
                      aria-pressed={selected === o.slug}
                      onClick={() => setSelected(o.slug)}
                      className={cn(
                        "relative grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 px-4 py-3 text-left transition-colors hover:bg-surface-2 sm:grid-cols-[auto_minmax(0,1fr)_auto]",
                        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                        selected === o.slug && "bg-surface-2",
                      )}
                    >
                      <span aria-hidden className={cn("absolute inset-y-2 left-0 w-[2px] rounded-full bg-brand transition-opacity", selected === o.slug ? "opacity-100" : "opacity-0")} />
                      <OrgLogo name={o.name} logoUrl={o.logo_url} accentColor={o.accent_color} size={32} className="row-span-2 sm:row-span-1" />
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-fg">{o.name}</span>
                        <span className="block truncate text-xs text-subtle">{titleCase(o.type)}{o.city ? ` · ${o.city}` : ""}</span>
                      </span>
                      {/* Below sm the badge drops to a second line under the name; from sm it sits in its own right column. */}
                      <span className="col-start-2 inline-flex justify-self-start sm:col-start-3 sm:row-start-1 sm:justify-self-end"><OrgVerificationBadge status={o.verification_status} /></span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <p className="px-1 text-xs text-subtle">Type at least two characters to search.</p>
          )}
        </div>
        <div className="min-w-0 xl:sticky xl:top-24">
          {selected ? <ManageOrg key={selected} slug={selected} /> : (
            <div className="flex flex-col items-center justify-center gap-3 rounded-[var(--radius-lg)] border border-dashed border-border-strong bg-surface/40 px-6 py-12 text-center">
              <span className="flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface-2 text-accent-strong" aria-hidden>
                <Building2 className="h-4 w-4" />
              </span>
              <p className="text-sm text-subtle">Search and pick an organization to manage it.</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export default function AdminOrgsPage() {
  return (
    <div className="space-y-12">
      <AdminHeader
        eyebrow="People"
        icon={<Building2 />}
        title="Organizations"
        description="Verification protects students: only verified organizations with platform-set domains can verify membership by email."
      />
      <VerificationQueue />
      <OrgFinder />
    </div>
  );
}
