"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MailCheck, Send, ShieldCheck, UserPlus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { OrgLogo, OrgVerificationBadge } from "@/components/orgs/org-ui";
import type { OrgDetail } from "@/components/orgs/types";
import { DemoBadge, SelfDeclaredBadge, VerifiedBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

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
    <Card>
      <CardHeader
        title={<span className="inline-flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-success" aria-hidden />Verify with your institutional email</span>}
        description="Instant and verified: we email a one-time confirmation link to your institutional address."
      />
      <CardBody>
        {sentTo ? (
          <div className="flex flex-col items-center py-4 text-center" role="status">
            <MailCheck className="h-10 w-10 text-success" aria-hidden />
            <p className="mt-3 font-medium text-fg">Check that inbox</p>
            <p className="mt-1 max-w-sm text-sm text-muted">
              We sent a confirmation link to <span className="font-medium text-fg">{sentTo}</span>. Open it while signed in to this account.
              The link expires in 24 hours.
            </p>
            <Button variant="ghost" size="sm" className="mt-4" onClick={() => setSentTo(null)}>Use a different address</Button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4" noValidate>
            <div>
              <p className="text-xs text-subtle">Accepted domains</p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5 font-mono text-xs">
                {org.email_domains.map((d) => <li key={d} className="rounded bg-surface-2 px-2 py-1 text-muted">@{d}</li>)}
              </ul>
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
      </CardBody>
    </Card>
  );
}

function RequestPath({ org }: { org: OrgDetail }) {
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
    <Card>
      <CardHeader
        title={<span className="inline-flex items-center gap-2"><UserPlus className="h-4 w-4 text-accent-strong" aria-hidden />Request membership</span>}
        description="An administrator reviews your request. Approved memberships are verified."
      />
      <CardBody>
        <form onSubmit={submit} className="space-y-4" noValidate>
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
      </CardBody>
    </Card>
  );
}

export default function JoinOrgPage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const org = useQuery({ queryKey: qk.org(slug), queryFn: () => get<OrgDetail>(`/orgs/${slug}`), enabled: Boolean(me.data) });

  if (me.isPending || !me.data) return <Spinner />;
  if (org.isPending) return <Spinner label="Loading organization" />;
  if (org.isError) return <Container className="py-10"><ErrorState error={org.error} onRetry={() => org.refetch()} /></Container>;

  const o = org.data;
  const v = o.viewer;
  const active = v.membership_status === "active";
  const pending = v.membership_status === "pending";
  const showEmail = o.domain_verification_available && !(active && v.verified);
  const showRequest = o.allow_membership_requests && !active && !pending;

  return (
    <Container size="md" className="py-8">
      <Link href={`/orgs/${o.slug}`} className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Back to {o.name}
      </Link>
      <header className="mt-4 flex items-center gap-4">
        <OrgLogo name={o.name} logoUrl={o.logo_url} accentColor={o.accent_color} size={56} />
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-fg">Join {o.name}</h1>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <OrgVerificationBadge status={o.verification_status} />
            {o.is_demo ? <DemoBadge /> : null}
          </div>
        </div>
      </header>

      <div className="mt-6 rounded-[var(--radius-lg)] border border-border bg-surface-2/50 p-4 text-sm text-muted">
        <p className="font-medium text-fg">How membership appears on your profile</p>
        <ul className="mt-2 space-y-2">
          <li className="flex flex-wrap items-center gap-2">
            <VerifiedBadge label="Verified member" />
            <span>after confirming an institutional email, accepting an admin invite, or being approved by an admin.</span>
          </li>
          <li className="flex flex-wrap items-center gap-2">
            <SelfDeclaredBadge />
            <span>an affiliation you only listed yourself — never shown as verified.</span>
          </li>
        </ul>
      </div>

      {!me.data.email_verified ? (
        <div className="mt-6">
          <InlineNotice tone="warning" title="Verify your account email first">
            Joining an organization requires a verified account email. Use the link we sent when you signed up.
          </InlineNotice>
        </div>
      ) : null}

      <div className="mt-6 space-y-6">
        {active && v.verified ? (
          <InlineNotice tone="success" title="You’re already a verified member" action={<LinkButton href={`/orgs/${o.slug}`} size="sm" variant="secondary">View organization</LinkButton>} />
        ) : null}
        {active && !v.verified && !o.domain_verification_available ? (
          <InlineNotice tone="info" title="You’re a member">Your membership isn’t verified. Ask an administrator for an invite link to verify it.</InlineNotice>
        ) : null}
        {pending ? (
          <InlineNotice tone="info" title="Your request is pending">
            An administrator will review it.{o.domain_verification_available ? " You can also verify instantly with your institutional email below." : ""}
          </InlineNotice>
        ) : null}

        {showEmail ? <EmailPath org={o} /> : null}
        {showRequest ? <RequestPath org={o} /> : null}

        {!showEmail && !showRequest && !active && !pending ? (
          <EmptyState
            title="Membership is by invitation"
            description={`${o.name} isn’t accepting requests${o.type === "university" && o.verification_status !== "verified" ? " and hasn’t been verified for institutional email yet" : ""}. Ask an administrator for an invite link.`}
            action={<LinkButton href={`/orgs/${o.slug}`} variant="secondary">Back to organization</LinkButton>}
          />
        ) : null}
      </div>
    </Container>
  );
}
