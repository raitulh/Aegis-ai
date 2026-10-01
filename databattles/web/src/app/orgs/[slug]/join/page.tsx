"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Clock, KeyRound, MailCheck, Send, ShieldCheck, UserPlus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";

import { OrgLogo, OrgVerificationBadge } from "@/components/orgs/org-ui";
import { AccentEdge, AccentGlow, DomainChips, StateMark, Stepper, SuccessMark, type StepState } from "@/components/orgs/org-visuals";
import type { OrgDetail } from "@/components/orgs/types";
import { DemoBadge, SelfDeclaredBadge, VerifiedBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

/** A join option: icon chip, option label, title, explanation and its form. */
function PathPanel({ option, icon, tone, title, description, children }: { option: string; icon: ReactNode; tone: "success" | "accent"; title: string; description: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="relative overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card">
      <div className="flex items-start gap-3.5 border-b border-border px-5 py-4 sm:px-6">
        <span
          aria-hidden
          className={
            tone === "success"
              ? "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-success-soft text-success"
              : "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-accent-soft text-accent-strong"
          }
        >
          {icon}
        </span>
        <div className="min-w-0">
          <p className="text-eyebrow text-subtle">{option}</p>
          <h2 className="mt-1 text-base font-semibold tracking-[-0.015em] text-fg">{title}</h2>
          <p className="mt-0.5 text-sm leading-relaxed text-muted">{description}</p>
        </div>
      </div>
      <div className="px-5 py-5 sm:px-6">{children}</div>
    </section>
  );
}

function EmailPath({ org }: { org: OrgDetail }) {
  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const m = useApiMutation((body: { email: string }) => post<{ message: string }>(`/orgs/${org.slug}/membership/verify-email`, body), {
    onSuccess: (_, vars) => {
      setSentTo(vars.email);
      setError(null);
    },
    onError: setError,
  });
  const domainOk = !email.includes("@") || org.email_domains.some((d) => {
    const dom = email.split("@").pop()?.toLowerCase() ?? "";
    return dom === d || dom.endsWith("." + d);
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    m.mutate({ email: email.trim() });
  }

  return (
    <PathPanel
      option="Option 1 · Instant"
      tone="success"
      icon={<ShieldCheck className="h-[18px] w-[18px]" />}
      title="Verify with your institutional email"
      description="Instant and verified: we email a one-time confirmation link to your institutional address."
    >
      {sentTo ? (
        <div className="flex flex-col items-center py-4 text-center animate-scale-in" role="status">
          <StateMark tone="info" icon={<MailCheck />} />
          <p className="mt-4 text-base font-semibold text-fg">Check that inbox</p>
          <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-muted">
            We sent a confirmation link to <span className="font-medium text-fg [overflow-wrap:anywhere]">{sentTo}</span>. Open it while signed in to this account.
            The link expires in 24 hours.
          </p>
          <Button variant="ghost" size="sm" className="mt-4" onClick={() => setSentTo(null)}>Use a different address</Button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-5" noValidate>
          <div>
            <p className="text-eyebrow text-subtle">Accepted domains</p>
            <DomainChips domains={org.email_domains} className="mt-2" />
          </div>
          <Field
            label="Institutional email"
            required
            error={error?.fields.email ?? (domainOk ? undefined : `Use an address ending in ${org.email_domains.map((d) => "@" + d).join(", ")}.`)}
            hint="Used only to confirm membership. It isn’t shown on your profile or to the organization."
          >
            {(p) => <Input {...p} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={320} placeholder={`you@${org.email_domains[0] ?? "university.edu"}`} />}
          </Field>
          <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
          <Button type="submit" loading={m.isPending} disabled={!email.includes("@") || !domainOk} icon={<Send className="h-4 w-4" />}>
            Send confirmation link
          </Button>
        </form>
      )}
    </PathPanel>
  );
}

function RequestPath({ org, option }: { org: OrgDetail; option: string }) {
  const [note, setNote] = useState("");
  const [dept, setDept] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const m = useApiMutation(
    (body: { note: string | null; department_id: string | null }) => post<{ message: string }>(`/orgs/${org.slug}/membership/request`, body),
    { success: (r) => r.message, invalidate: [qk.org(org.slug), ["orgs", "mine"]], onError: setError },
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    m.mutate({ note: note.trim() || null, department_id: dept || null });
  }

  return (
    <PathPanel
      option={option}
      tone="accent"
      icon={<UserPlus className="h-[18px] w-[18px]" />}
      title="Request membership"
      description="An administrator reviews your request. Approved memberships are verified."
    >
      <form onSubmit={submit} className="space-y-5" noValidate>
        {org.departments.length ? (
          <Field label="Department" error={error?.fields.department_id}>
            {(p) => (
              <Select {...p} value={dept} onChange={(e) => setDept(e.target.value)}>
                <option value="">Not specified</option>
                {org.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            )}
          </Field>
        ) : null}
        <Field label="Note to the administrators" error={error?.fields.note} hint="Optional. E.g. your programme and year, or who told you about the group. Only admins see it.">
          {(p) => <Textarea {...p} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={3} />}
        </Field>
        <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
        <Button type="submit" variant="secondary" loading={m.isPending} icon={<Send className="h-4 w-4" />}>Send request</Button>
      </form>
    </PathPanel>
  );
}

function JoinSkeleton() {
  return (
    <Container size="lg" className="pb-20">
      <div role="status" aria-label="Loading organization">
        <Skeleton className="mt-8 h-4 w-48" />
        <div className="mt-5 rounded-[var(--radius-2xl)] border border-border bg-surface p-6 sm:p-8">
          <div className="flex items-center gap-4">
            <Skeleton className="h-14 w-14 shrink-0 rounded-xl" />
            <div className="flex-1 space-y-2.5">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="h-7 w-2/3" />
            </div>
          </div>
          <Skeleton className="mt-8 h-16 w-full rounded-[var(--radius-lg)]" />
        </div>
        <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <Skeleton className="h-72 w-full rounded-[var(--radius-xl)]" />
          <Skeleton className="h-40 w-full rounded-[var(--radius-lg)]" />
        </div>
      </div>
    </Container>
  );
}

export default function JoinOrgPage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const org = useQuery({ queryKey: qk.org(slug), queryFn: () => get<OrgDetail>(`/orgs/${slug}`), enabled: Boolean(me.data) });

  if (me.isPending || !me.data) return <JoinSkeleton />;
  if (org.isPending) return <JoinSkeleton />;
  if (org.isError) return <Container className="py-10"><ErrorState error={org.error} onRetry={() => org.refetch()} /></Container>;

  const o = org.data;
  const v = o.viewer;
  const active = v.membership_status === "active";
  const pending = v.membership_status === "pending";
  const showEmail = o.domain_verification_available && !(active && v.verified);
  const showRequest = o.allow_membership_requests && !active && !pending;

  // Progress shown in the stepper — derived only from the account and membership state the API returned.
  const accountOk = me.data.email_verified;
  const steps: { label: string; hint: string; state: StepState }[] = [
    { label: "Verify your account email", hint: accountOk ? "Your account email is verified." : "Use the link we sent when you signed up.", state: accountOk ? "done" : "current" },
    {
      label: "Choose how to join",
      hint: pending ? "Request sent." : active ? "You’re a member." : "Institutional email or a request.",
      state: pending || active ? "done" : accountOk ? "current" : "upcoming",
    },
    {
      label: "Verified membership",
      hint: active && v.verified ? "Shown as verified on your profile." : pending ? "Waiting for an administrator." : "Confirm the link or get approved.",
      state: active && v.verified ? "done" : pending || active ? "current" : "upcoming",
    },
  ];

  return (
    <Container size="lg" className="pb-20">
      <Link
        href={`/orgs/${o.slug}`}
        className="mt-7 inline-flex max-w-full items-center gap-1.5 rounded-sm text-sm text-muted transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
      >
        <ArrowLeft className="h-4 w-4 shrink-0" aria-hidden /> <span className="truncate">Back to {o.name}</span>
      </Link>

      <header className="relative isolate mt-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-card animate-rise">
        <AccentEdge color={o.accent_color} />
        <AccentGlow color={o.accent_color} strength={22} className="-right-24 -top-36 h-80 w-[36rem] max-w-full" />
        <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:p-8">
          <OrgLogo name={o.name} logoUrl={o.logo_url} accentColor={o.accent_color} size={60} />
          <div className="min-w-0">
            <p className="text-eyebrow text-accent-strong">Membership</p>
            <h1 className="mt-1.5 text-title text-fg [overflow-wrap:anywhere]">Join {o.name}</h1>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <OrgVerificationBadge status={o.verification_status} />
              {o.is_demo ? <DemoBadge /> : null}
            </div>
          </div>
        </div>
        <div className="border-t border-border bg-bg-elevated/30 p-3 sm:p-4">
          <Stepper steps={steps} label="Joining progress" className="bg-border" />
        </div>
      </header>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="min-w-0 space-y-6">
          {!me.data.email_verified ? (
            <InlineNotice tone="warning" title="Verify your account email first">
              Joining an organization requires a verified account email. Use the link we sent when you signed up.
            </InlineNotice>
          ) : null}

          {active && v.verified ? (
            <section className="relative isolate overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen px-6 py-10 text-center shadow-card animate-rise" role="status">
              <div aria-hidden className="pointer-events-none absolute inset-0 -z-10" style={{ background: "radial-gradient(50% 60% at 50% 0%, color-mix(in oklab, var(--success) 12%, transparent), transparent)" }} />
              <SuccessMark className="mx-auto" animated={false} />
              <h2 className="mt-5 text-lg font-semibold tracking-[-0.015em] text-fg">You’re already a verified member</h2>
              <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-muted">Your membership of {o.name} is verified and appears as verified on your profile.</p>
              <div className="mt-6 flex justify-center">
                <LinkButton href={`/orgs/${o.slug}`} size="sm" variant="secondary">View organization</LinkButton>
              </div>
            </section>
          ) : null}
          {active && !v.verified && !o.domain_verification_available ? (
            <InlineNotice tone="info" title="You’re a member">Your membership isn’t verified. Ask an administrator for an invite link to verify it.</InlineNotice>
          ) : null}
          {pending ? (
            <section className="flex flex-col items-center gap-4 rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen px-6 py-8 text-center shadow-card sm:flex-row sm:text-left" role="status">
              <StateMark tone="warning" icon={<Clock />} />
              <div className="min-w-0">
                <h2 className="text-base font-semibold tracking-[-0.015em] text-fg">Your request is pending</h2>
                <p className="mt-1 text-sm leading-relaxed text-muted">
                  An administrator will review it.{o.domain_verification_available ? " You can also verify instantly with your institutional email below." : ""}
                </p>
              </div>
            </section>
          ) : null}

          {showEmail ? <EmailPath org={o} /> : null}
          {showEmail && showRequest ? (
            <div className="flex items-center gap-3 text-eyebrow text-subtle" aria-hidden>
              <span className="h-px flex-1 bg-border" />
              or
              <span className="h-px flex-1 bg-border" />
            </div>
          ) : null}
          {showRequest ? <RequestPath org={o} option={showEmail ? "Option 2 · Reviewed" : "Reviewed by an admin"} /> : null}

          {!showEmail && !showRequest && !active && !pending ? (
            <EmptyState
              icon={<KeyRound />}
              title="Membership is by invitation"
              description={`${o.name} isn’t accepting requests${o.type === "university" && o.verification_status !== "verified" ? " and hasn’t been verified for institutional email yet" : ""}. Ask an administrator for an invite link.`}
              action={<LinkButton href={`/orgs/${o.slug}`} variant="secondary">Back to organization</LinkButton>}
            />
          ) : null}
        </div>

        <aside className="min-w-0 lg:sticky lg:top-24 lg:self-start" aria-label="About verified membership">
          <div className="rounded-[var(--radius-lg)] border border-border bg-surface/70 p-5 text-sm">
            <p className="font-medium text-fg">How membership appears on your profile</p>
            <ul className="mt-4 space-y-4">
              <li>
                <VerifiedBadge label="Verified member" />
                <p className="mt-1.5 leading-relaxed text-muted">after confirming an institutional email, accepting an admin invite, or being approved by an admin.</p>
              </li>
              <li className="border-t border-border pt-4">
                <SelfDeclaredBadge />
                <p className="mt-1.5 leading-relaxed text-muted">an affiliation you only listed yourself — never shown as verified.</p>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </Container>
  );
}
