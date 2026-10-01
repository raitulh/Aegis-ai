"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, Link2Off, MailWarning } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { OrgLogo, OrgVerificationBadge, ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/components/orgs/org-ui";
import type { InvitePreview, OrgDetail } from "@/components/orgs/types";
import { Badge, VerifiedBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter } from "@/components/ui/card";
import { FormError } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

function InvalidInvite() {
  return (
    <EmptyState
      icon={<Link2Off className="h-5 w-5" />}
      title="This invite link is invalid or has expired"
      description="Invites can expire, be revoked, or reach their usage limit. Ask the administrator who sent it for a new link."
      action={<LinkButton href="/orgs" variant="secondary">Browse organizations</LinkButton>}
    />
  );
}

function AcceptInvite() {
  const token = useSearchParams().get("token") ?? "";
  const pathname = usePathname();
  const me = useMe();
  const qc = useQueryClient();
  const valid = token.length >= 10;
  const preview = useQuery({
    queryKey: ["orgs", "invite-preview", token],
    queryFn: () => get<InvitePreview>("/orgs/invites/preview", { token }),
    enabled: valid,
    retry: false,
  });
  const [joined, setJoined] = useState<OrgDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const org = await post<OrgDetail>("/orgs/invites/accept", { token });
      setJoined(org);
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.me }),
        qc.invalidateQueries({ queryKey: qk.org(org.slug) }),
        qc.invalidateQueries({ queryKey: ["orgs", "mine"] }),
      ]);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "error", "Could not accept the invite.", null, null));
    } finally {
      setBusy(false);
    }
  }

  if (!valid) return <Container size="md" className="py-16"><InvalidInvite /></Container>;
  if (preview.isPending) return <Spinner label="Checking invite" />;
  if (preview.isError) {
    const e = preview.error;
    return (
      <Container size="md" className="py-16">
        {e instanceof ApiError && (e.code === "invite_invalid" || e.status === 404) ? <InvalidInvite /> : <ErrorState error={e} onRetry={() => preview.refetch()} />}
      </Container>
    );
  }

  const inv = preview.data;
  const next = `${pathname}?token=${encodeURIComponent(token)}`;

  if (joined) {
    return (
      <Container size="md" className="py-16">
        <Card className="mx-auto max-w-md">
          <CardBody className="flex flex-col items-center px-6 py-10 text-center">
            <CheckCircle2 className="h-10 w-10 text-success" aria-hidden />
            <h1 className="mt-3 text-lg font-semibold text-fg">Welcome to {joined.name}</h1>
            <div className="mt-2 flex flex-wrap justify-center gap-1.5">
              <Badge tone="accent">{ROLE_LABELS[joined.viewer.role ?? inv.role] ?? titleCase(joined.viewer.role ?? inv.role)}</Badge>
              <VerifiedBadge label="Verified member" />
            </div>
            <p className="mt-3 text-sm text-muted">Memberships from an admin invite are verified and shown as verified on your profile.</p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              <LinkButton href={`/orgs/${joined.slug}`}>Go to {joined.name}</LinkButton>
              {joined.viewer.can_manage_content ? <LinkButton href={`/orgs/${joined.slug}/admin`} variant="secondary">Open admin</LinkButton> : null}
            </div>
          </CardBody>
        </Card>
      </Container>
    );
  }

  return (
    <Container size="md" className="py-16">
      <Card className="mx-auto max-w-md">
        <CardBody className="px-6 py-8">
          <p className="text-xs font-medium uppercase tracking-wider text-accent-strong">Invitation</p>
          <h1 className="mt-1 text-xl font-semibold text-fg">Join {inv.org.name}</h1>
          <div className="mt-5 flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-surface-2 p-3">
            <OrgLogo name={inv.org.name} logoUrl={inv.org.logo_url} size={44} />
            <div className="min-w-0">
              <p className="truncate font-medium text-fg">{inv.org.name}</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <Badge tone="outline">{titleCase(inv.org.type)}</Badge>
                <OrgVerificationBadge status={inv.org.verification_status} />
              </div>
            </div>
          </div>
          <dl className="mt-5 space-y-3 text-sm">
            <div>
              <dt className="text-xs text-subtle">Role</dt>
              <dd className="mt-0.5 text-fg">
                <span className="font-medium">{ROLE_LABELS[inv.role] ?? titleCase(inv.role)}</span>
                <span className="text-muted"> — {ROLE_DESCRIPTIONS[inv.role] ?? ""}</span>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-subtle">Expires</dt>
              <dd className="mt-0.5 inline-flex items-center gap-1.5 text-fg">
                <Clock className="h-3.5 w-3.5 text-subtle" aria-hidden />
                {formatDateTime(inv.expires_at)} <span className="text-muted">({relativeTime(inv.expires_at)})</span>
              </dd>
            </div>
          </dl>
          {inv.email_bound ? (
            <p className="mt-4 flex items-start gap-2 text-xs text-muted">
              <MailWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              This invite was sent to a specific email address. Accept it while signed in to the account with that email.
            </p>
          ) : null}

          {error?.code === "invite_email_mismatch" ? (
            <div className="mt-4">
              <InlineNotice tone="warning" title="Wrong account">
                This invite was sent to a different email address than the one on your account (@{me.data?.handle}). Sign in with the invited
                account, or ask the administrator to send the invite to your current email.
              </InlineNotice>
            </div>
          ) : error?.code === "invite_invalid" || error?.status === 404 ? (
            <div className="mt-4"><InlineNotice tone="danger" title="Invite no longer valid">It may have expired, been revoked or used up.</InlineNotice></div>
          ) : error?.code === "email_not_verified" ? (
            <div className="mt-4"><InlineNotice tone="warning" title="Verify your email first">{error.message}</InlineNotice></div>
          ) : (
            <div className="mt-4"><FormError message={error?.message} /></div>
          )}
        </CardBody>
        <CardFooter className="flex-wrap">
          {me.isPending ? (
            <Spinner label="Checking your session" />
          ) : me.data ? (
            <>
              <LinkButton href="/orgs" variant="ghost">Not now</LinkButton>
              <Button onClick={accept} loading={busy}>Accept invite</Button>
            </>
          ) : (
            <>
              <p className="mr-auto text-xs text-subtle">New here? Create an account, verify your email, then open this link again.</p>
              <LinkButton href="/signup" variant="secondary">Create account</LinkButton>
              <LinkButton href={`/login?next=${encodeURIComponent(next)}`}>Sign in to accept</LinkButton>
            </>
          )}
        </CardFooter>
      </Card>
    </Container>
  );
}

export default function OrgInvitePage() {
  return (
    <Suspense fallback={<Spinner />}>
      <AcceptInvite />
    </Suspense>
  );
}
