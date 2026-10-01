"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Link2Off, MailWarning, ShieldCheck, UserRound } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";

import { OrgLogo, OrgVerificationBadge, ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/components/orgs/org-ui";
import { AccentEdge, OrgTypeIcon, Stepper, SuccessMark } from "@/components/orgs/org-visuals";
import type { InvitePreview, OrgDetail } from "@/components/orgs/types";
import { Badge, VerifiedBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { FormError } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

function InvalidInvite() {
  return (
    <>
      <h1 className="sr-only">Organization invite</h1>
      <EmptyState
        icon={<Link2Off />}
        title="This invite link is invalid or has expired"
        description="Invites can expire, be revoked, or reach their usage limit. Ask the administrator who sent it for a new link."
        action={<LinkButton href="/orgs" variant="secondary">Browse organizations</LinkButton>}
      />
    </>
  );
}

/** Centered card shell shared by every state of the invite flow. */
function FlowCard({ children, footer, className }: { children: ReactNode; footer?: ReactNode; className?: string }) {
  return (
    <div className={cn("relative isolate mx-auto w-full max-w-lg overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-elevated animate-rise", className)}>
      <AccentEdge />
      <div aria-hidden className="pointer-events-none absolute -top-24 left-1/2 -z-10 h-56 w-[32rem] max-w-full -translate-x-1/2" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top,black,transparent_60%)]" />
      {children}
      {footer ? <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border bg-bg-elevated/40 px-6 py-4">{footer}</div> : null}
    </div>
  );
}

function InviteSkeleton() {
  return (
    <Container size="md" className="py-12 sm:py-16">
      <div className="mx-auto max-w-lg" role="status" aria-label="Checking invite">
        <Skeleton className="h-16 w-full rounded-[var(--radius-lg)]" />
        <div className="mt-6 rounded-[var(--radius-2xl)] border border-border bg-surface p-7">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3 h-7 w-2/3" />
          <Skeleton className="mt-6 h-16 w-full rounded-[var(--radius-md)]" />
          <Skeleton className="mt-6 h-10 w-full" />
        </div>
      </div>
    </Container>
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
  if (preview.isPending) return <InviteSkeleton />;
  if (preview.isError) {
    const e = preview.error;
    return (
      <Container size="md" className="py-16">
        {e instanceof ApiError && (e.code === "invite_invalid" || e.status === 404) ? (
          <InvalidInvite />
        ) : (
          <>
            <h1 className="sr-only">Organization invite</h1>
            <ErrorState error={e} onRetry={() => preview.refetch()} />
          </>
        )}
      </Container>
    );
  }

  const inv = preview.data;
  const next = `${pathname}?token=${encodeURIComponent(token)}`;
  const signedIn = Boolean(me.data);

  const stepper = (
    <Stepper
      label="Invite progress"
      className="mx-auto mb-6 max-w-lg"
      steps={[
        { label: "Sign in", state: signedIn || joined ? "done" : "current" },
        { label: "Accept invite", state: joined ? "done" : signedIn ? "current" : "upcoming" },
        { label: "Verified member", state: joined ? "done" : "upcoming" },
      ]}
    />
  );

  if (joined) {
    return (
      <Container size="md" className="py-12 sm:py-16">
        {stepper}
        <FlowCard className="text-center">
          <div className="flex flex-col items-center px-6 py-10 sm:px-10" role="status">
            <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-48" style={{ background: "radial-gradient(50% 80% at 50% 0%, color-mix(in oklab, var(--success) 14%, transparent), transparent)" }} />
            <SuccessMark />
            <h1 className="mt-5 text-xl font-semibold tracking-[-0.02em] text-fg">Welcome to {joined.name}</h1>
            <div className="mt-3 flex flex-wrap justify-center gap-1.5 animate-pop [animation-delay:200ms]">
              <Badge tone="accent">{ROLE_LABELS[joined.viewer.role ?? inv.role] ?? titleCase(joined.viewer.role ?? inv.role)}</Badge>
              <VerifiedBadge label="Verified member" />
            </div>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">Memberships from an admin invite are verified and shown as verified on your profile.</p>
            <div className="mt-7 flex flex-wrap justify-center gap-2">
              <LinkButton href={`/orgs/${joined.slug}`}>Go to {joined.name}</LinkButton>
              {joined.viewer.can_manage_content ? <LinkButton href={`/orgs/${joined.slug}/admin`} variant="secondary">Open admin</LinkButton> : null}
            </div>
          </div>
        </FlowCard>
      </Container>
    );
  }

  return (
    <Container size="md" className="py-12 sm:py-16">
      {stepper}
      <FlowCard
        footer={
          me.isPending ? (
            <Spinner label="Checking your session" />
          ) : me.data ? (
            <>
              <LinkButton href="/orgs" variant="ghost">Not now</LinkButton>
              <Button onClick={accept} loading={busy} icon={<ShieldCheck className="h-4 w-4" />}>Accept invite</Button>
            </>
          ) : (
            <>
              <p className="mr-auto w-full text-xs leading-relaxed text-subtle sm:w-auto sm:max-w-[14rem]">New here? Create an account, verify your email, then open this link again.</p>
              <LinkButton href="/signup" variant="secondary">Create account</LinkButton>
              <LinkButton href={`/login?next=${encodeURIComponent(next)}`}>Sign in to accept</LinkButton>
            </>
          )
        }
      >
        <div className="px-6 py-7 sm:px-8">
          <p className="text-eyebrow text-accent-strong">Invitation</p>
          <h1 className="mt-1.5 text-[1.6rem] font-semibold leading-tight tracking-[-0.025em] text-fg [overflow-wrap:anywhere]">Join {inv.org.name}</h1>

          <div className="mt-6 flex items-center gap-3.5 rounded-[var(--radius-lg)] border border-border bg-bg-elevated/70 p-3.5">
            <OrgLogo name={inv.org.name} logoUrl={inv.org.logo_url} size={48} />
            <div className="min-w-0">
              <p className="truncate font-medium text-fg">{inv.org.name}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <Badge tone="outline" icon={<OrgTypeIcon type={inv.org.type} className="h-3 w-3" />}>{titleCase(inv.org.type)}</Badge>
                <OrgVerificationBadge status={inv.org.verification_status} />
              </div>
            </div>
          </div>

          <dl className="mt-6 grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border text-sm">
            <div className="bg-surface px-4 py-3">
              <dt className="flex items-center gap-1.5 text-eyebrow text-subtle"><UserRound className="h-3.5 w-3.5" aria-hidden />Role</dt>
              <dd className="mt-1.5 text-fg">
                <span className="font-medium">{ROLE_LABELS[inv.role] ?? titleCase(inv.role)}</span>
                <span className="text-muted"> — {ROLE_DESCRIPTIONS[inv.role] ?? ""}</span>
              </dd>
            </div>
            <div className="bg-surface px-4 py-3">
              <dt className="flex items-center gap-1.5 text-eyebrow text-subtle"><Clock className="h-3.5 w-3.5" aria-hidden />Expires</dt>
              <dd className="tabular mt-1.5 text-fg">
                {formatDateTime(inv.expires_at)} <span className="text-muted">({relativeTime(inv.expires_at)})</span>
              </dd>
            </div>
          </dl>
          {inv.email_bound ? (
            <p className="mt-4 flex items-start gap-2 text-xs leading-relaxed text-muted">
              <MailWarning className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
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
        </div>
      </FlowCard>
      <p className="mx-auto mt-4 max-w-lg text-center text-xs leading-relaxed text-subtle">
        Accepting an admin invite creates a verified membership, shown as verified on your profile.
      </p>
    </Container>
  );
}

export default function OrgInvitePage() {
  return (
    <Suspense fallback={<InviteSkeleton />}>
      <AcceptInvite />
    </Suspense>
  );
}
