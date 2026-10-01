"use client";

import { useQuery } from "@tanstack/react-query";
import { Compass, Mail, MailOpen, Users } from "lucide-react";

import { ck } from "@/components/competition/context";
import { InvitationCard } from "@/components/competition/invitation-card";
import type { Invitation } from "@/components/competition/types";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useNow, useRequireAuth } from "@/lib/hooks";

function InvitesSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading invitations">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" style={{ opacity: 1 - i * 0.15 }}>
          <div className="flex items-start gap-3.5 px-5 py-4">
            <Skeleton className="h-10 w-10 rounded-xl" />
            <div className="flex-1">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="mt-2 h-3 w-64 max-w-full" />
            </div>
          </div>
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <Skeleton className="h-8 w-24 rounded-[var(--radius-md)]" />
            <Skeleton className="h-8 w-36 rounded-[var(--radius-md)]" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function InvitesPage() {
  const me = useRequireAuth();
  const signedIn = Boolean(me.data);
  const query = useQuery({
    queryKey: ck.myInvitations,
    queryFn: () => get<Invitation[]>("/me/team-invitations"),
    enabled: signedIn,
  });
  const now = useNow(60_000).getTime();
  // The API returns every pending invitation, including expired ones (the cards label those "Expired").
  const count = query.data?.filter((i) => new Date(i.expires_at).getTime() > now).length;

  return (
    <Container size="md" className="pb-16">
      <PageHeader
        eyebrow="Teams"
        icon={<Mail />}
        title="Team invitations"
        description="Invitations to join teams in competitions. Accepting one registers you for that competition and closes your other invitations for it."
        meta={
          count ? (
            <span className="inline-flex items-center gap-1.5">
              <Users className="h-3.5 w-3.5" aria-hidden />
              <span className="tabular font-medium text-muted">{count}</span> pending
            </span>
          ) : undefined
        }
      />
      {!signedIn || query.isPending ? (
        // Signed-out visitors are redirected to sign in by useRequireAuth.
        <InvitesSkeleton />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : query.data.length === 0 ? (
        <section aria-labelledby="invites-empty-heading">
          <h2 id="invites-empty-heading" className="sr-only">Pending invitations</h2>
          <EmptyState
            icon={<MailOpen />}
            title="No pending invitations"
            description="When a team captain invites you, it shows up here and in your notifications."
            action={
              <>
                <LinkButton href="/competitions" variant="secondary" icon={<Compass className="h-4 w-4" aria-hidden />}>Browse competitions</LinkButton>
                <LinkButton href="/discussions" variant="ghost">Find teammates</LinkButton>
              </>
            }
          />
        </section>
      ) : (
        <ul className="space-y-4" aria-label="Pending invitations">
          {query.data.map((inv, i) => (
            <li key={inv.id} className="animate-rise" style={{ animationDelay: `${Math.min(i, 6) * 60}ms` }}>
              <InvitationCard invitation={inv} />
            </li>
          ))}
        </ul>
      )}
    </Container>
  );
}
