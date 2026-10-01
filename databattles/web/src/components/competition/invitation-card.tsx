"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Mail, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { UserLink } from "@/components/domain/cards";
import { Button } from "@/components/ui/button";
import { Checkbox, FormError } from "@/components/ui/form";
import { ApiError, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime } from "@/lib/format";
import type { Message } from "@/lib/types";
import { ck } from "./context";
import type { Invitation } from "./types";

/**
 * A pending team invitation with Accept / Decline. Accepting also registers the invitee for the competition
 * when needed, so the rules must be accepted first.
 */
export function InvitationCard({
  invitation,
  alreadyParticipant,
  showCompetition = true,
  onResponded,
  className,
}: {
  invitation: Invitation;
  /** When known, participants don't need to accept the rules again. */
  alreadyParticipant?: boolean;
  showCompetition?: boolean;
  onResponded?: (accepted: boolean) => void;
  className?: string;
}) {
  const qc = useQueryClient();
  const [rules, setRules] = useState(Boolean(alreadyParticipant));
  const slug = invitation.competition_slug;
  const respond = useMutation<Message, ApiError, boolean>({
    mutationFn: (accept) =>
      post<Message>(`/me/team-invitations/${invitation.id}/respond`, { accept, accept_rules: accept ? rules : false }),
    onSuccess: async (res, accept) => {
      toast.success(res.message || (accept ? "You joined the team." : "Invitation declined."));
      await Promise.all([
        qc.invalidateQueries({ queryKey: ck.myInvitations }),
        qc.invalidateQueries({ queryKey: ["competitions", slug] }),
      ]);
      onResponded?.(accept);
    },
  });
  const expired = new Date(invitation.expires_at).getTime() < Date.now();
  const err = respond.error;

  return (
    <article className={cn("rounded-[var(--radius-lg)] border border-border bg-surface p-4", className)} aria-labelledby={`inv-${invitation.id}`}>
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong">
          <Mail className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h3 id={`inv-${invitation.id}`} className="font-semibold text-fg">
            Join team <span className="text-accent-strong">{invitation.team_name}</span>
          </h3>
          {showCompetition ? (
            <p className="text-sm text-muted">
              in <Link href={`/competitions/${slug}`} className="font-medium text-fg hover:text-accent-strong">{invitation.competition_title}</Link>
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
            {invitation.inviter ? (
              <span className="inline-flex items-center gap-1">Invited by <UserLink user={invitation.inviter} size={16} className="text-xs" /></span>
            ) : null}
            <span>· sent {relativeTime(invitation.created_at)}</span>
            <span title={formatDateTime(invitation.expires_at)}>· {expired ? "expired" : `expires ${relativeTime(invitation.expires_at)}`}</span>
          </div>
        </div>
      </div>

      {!alreadyParticipant ? (
        <div className="mt-4">
          <Checkbox
            label={
              <>
                I have read and accept the{" "}
                <Link href={`/competitions/${slug}#rules`} className="text-accent-strong underline" target="_blank" rel="noopener noreferrer">
                  competition rules
                </Link>
              </>
            }
            description="Accepting registers you for the competition if you haven't joined yet."
            checked={rules}
            onChange={(e) => setRules(e.target.checked)}
          />
        </div>
      ) : null}

      {err ? <div className="mt-3"><FormError message={err.message} /></div> : null}

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button
          variant="secondary"
          size="sm"
          icon={<X className="h-4 w-4" aria-hidden />}
          loading={respond.isPending && respond.variables === false}
          disabled={respond.isPending}
          onClick={() => respond.mutate(false)}
        >
          Decline
        </Button>
        <Button
          size="sm"
          icon={<Check className="h-4 w-4" aria-hidden />}
          loading={respond.isPending && respond.variables === true}
          disabled={respond.isPending || expired || !rules}
          onClick={() => respond.mutate(true)}
        >
          Accept & join team
        </Button>
      </div>
    </article>
  );
}
