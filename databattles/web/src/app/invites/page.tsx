"use client";

import { useQuery } from "@tanstack/react-query";
import { MailOpen } from "lucide-react";

import { ck } from "@/components/competition/context";
import { InvitationCard } from "@/components/competition/invitation-card";
import type { Invitation } from "@/components/competition/types";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useRequireAuth } from "@/lib/hooks";

export default function InvitesPage() {
  const me = useRequireAuth();
  const signedIn = Boolean(me.data);
  const query = useQuery({
    queryKey: ck.myInvitations,
    queryFn: () => get<Invitation[]>("/me/team-invitations"),
    enabled: signedIn,
  });

  return (
    <Container size="md" className="pb-16">
      <PageHeader
        title="Team invitations"
        description="Invitations to join teams in competitions. Accepting one registers you for that competition and closes your other invitations for it."
      />
      {!signedIn || query.isPending ? (
        // Signed-out visitors are redirected to sign in by useRequireAuth.
        <SkeletonRows rows={3} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : query.data.length === 0 ? (
        <EmptyState
          icon={<MailOpen className="h-5 w-5" />}
          title="No pending invitations"
          description="When a team captain invites you, it shows up here and in your notifications."
          action={<LinkButton href="/competitions" variant="secondary">Browse competitions</LinkButton>}
        />
      ) : (
        <ul className="space-y-4" aria-label="Pending invitations">
          {query.data.map((inv) => (
            <li key={inv.id}>
              <InvitationCard invitation={inv} />
            </li>
          ))}
        </ul>
      )}
    </Container>
  );
}
