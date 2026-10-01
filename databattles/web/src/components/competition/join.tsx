"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, LogIn, UserPlus } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button, LinkButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, FormError, Input } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { SignInPrompt } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail } from "@/lib/types";
import { ck } from "./context";
import { describeJoinBlocker } from "./labels";

/** Invalidate everything that depends on the viewer's participation, keeping the freshly returned detail. */
export function useRefreshParticipation(slug: string) {
  const qc = useQueryClient();
  return async (detail?: CompetitionDetail) => {
    if (detail) qc.setQueryData(qk.competition(slug), detail);
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["competitions", slug], predicate: (q) => (detail ? q.queryKey.length > 2 : true) }),
      qc.invalidateQueries({ queryKey: ck.myInvitations }),
      qc.invalidateQueries({ queryKey: ["competitions", "list"] }),
    ]);
  };
}

export function JoinDialog({
  comp,
  open,
  onOpenChange,
}: {
  comp: CompetitionDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const refresh = useRefreshParticipation(comp.slug);
  const [accepted, setAccepted] = useState(false);
  // Invite links from organizers look like /competitions/{slug}?code=XXXX — prefill the code.
  const [code, setCode] = useState(() =>
    typeof window !== "undefined" ? (new URLSearchParams(window.location.search).get("code") ?? "") : "",
  );
  const needsCode = comp.viewer.needs_invite_code;

  const join = useMutation<CompetitionDetail, ApiError>({
    mutationFn: () =>
      post<CompetitionDetail>(`/competitions/${encodeURIComponent(comp.slug)}/join`, {
        accept_rules: accepted,
        invite_code: needsCode && code.trim() ? code.trim() : undefined,
      }),
    onSuccess: async (detail) => {
      await refresh(detail);
      onOpenChange(false);
      if (!detail.viewer.team && detail.team_max_size > 1) {
        toast.success(`You joined ${detail.title}. Next: create or join a team.`);
        router.push(`/competitions/${detail.slug}/team`);
      } else {
        toast.success(`You joined ${detail.title}. Good luck!`);
      }
    },
  });

  const err = join.error;
  const codeError = err?.code === "invite_required" ? err.message : err?.fields.invite_code;
  const rulesError = err?.code === "rules_not_accepted" ? err.message : err?.fields.accept_rules;
  const blockers = (err?.details?.blockers as string[] | undefined) ?? [];
  const generalError = err && !codeError && !rulesError && !blockers.length ? err.message : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) join.reset();
      }}
      size="lg"
      title={`Join ${comp.title}`}
      description="Read the rules carefully — by joining you agree to follow them for the whole competition."
      footer={
        <>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            icon={<UserPlus className="h-4 w-4" aria-hidden />}
            loading={join.isPending}
            disabled={!accepted || (needsCode && !code.trim() && comp.viewer.pending_invitations === 0)}
            onClick={() => join.mutate()}
          >
            Join competition
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (accepted) join.mutate();
        }}
      >
        <section aria-labelledby="join-rules-heading">
          <h3 id="join-rules-heading" className="mb-2 text-sm font-semibold text-fg">Competition rules</h3>
          <div className="max-h-72 overflow-y-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated px-4 py-3" tabIndex={0} aria-label="Rules text">
            {comp.rules_html ? (
              <Prose html={comp.rules_html} className="text-sm" />
            ) : (
              <p className="text-sm text-muted">The organizers haven&apos;t published additional rules. The platform code of conduct applies.</p>
            )}
          </div>
          <ul className="mt-3 grid gap-1 text-xs text-muted sm:grid-cols-2">
            <li>Team size: {comp.team_min_size === comp.team_max_size ? comp.team_min_size : `${comp.team_min_size}–${comp.team_max_size}`}</li>
            {comp.scoring_mode === "automatic" ? <li>Daily submissions: {formatNumber(comp.daily_submission_limit)}</li> : null}
            {comp.scoring_mode === "automatic" ? <li>Final submissions you can select: {comp.final_selection_limit}</li> : null}
            {comp.total_submission_limit ? <li>Total submissions: {formatNumber(comp.total_submission_limit)}</li> : null}
          </ul>
        </section>

        {needsCode ? (
          <Field
            label="Invite code"
            error={codeError}
            hint={
              comp.viewer.pending_invitations > 0
                ? "You have a pending team invitation, so the code is optional."
                : "This competition is invite-only. Enter the code the organizers shared with you."
            }
          >
            {(p) => <Input {...p} value={code} onChange={(e) => setCode(e.target.value)} maxLength={32} autoComplete="off" spellCheck={false} />}
          </Field>
        ) : null}

        <div>
          <Checkbox
            label="I have read and accept the competition rules"
            description="Your acceptance and the rules version are recorded with your registration."
            checked={accepted}
            onChange={(e) => setAccepted(e.target.checked)}
            aria-invalid={rulesError ? true : undefined}
          />
          {rulesError ? <p role="alert" className="mt-1 text-xs font-medium text-danger">{rulesError}</p> : null}
        </div>

        {blockers.length ? (
          <div role="alert" className="rounded-[var(--radius-md)] border border-danger/40 bg-danger-soft px-3 py-2 text-sm text-danger">
            <p className="font-medium">{err?.message}</p>
            <ul className="mt-1 list-disc pl-5">
              {blockers.map((b) => <li key={b}>{describeJoinBlocker(b, comp)}</li>)}
            </ul>
          </div>
        ) : null}
        <FormError message={generalError} />
        {/* Enables Enter-to-submit without duplicating the footer button for screen readers. */}
        <button type="submit" className="sr-only" tabIndex={-1} aria-hidden>Join</button>
      </form>
    </Dialog>
  );
}

function ResendVerification({ email }: { email: string }) {
  const [sent, setSent] = useState(false);
  const resend = useMutation<unknown, ApiError>({
    mutationFn: () => post("/auth/resend-verification", { email }),
    onSuccess: () => {
      setSent(true);
      toast.success("Verification email sent — check your inbox.");
    },
    onError: (e) => toast.error(e.message),
  });
  return (
    <Button size="sm" variant="link" disabled={sent} loading={resend.isPending} onClick={() => resend.mutate()}>
      {sent ? "Email sent" : "Resend verification email"}
    </Button>
  );
}

/** Blocker list with a helpful next action where one exists. */
export function JoinBlockers({ comp }: { comp: CompetitionDetail }) {
  const me = useMe().data;
  const blockers = comp.viewer.join_blockers.filter((b) => b !== "sign_in_required");
  if (!blockers.length) return null;
  return (
    <ul className="space-y-1.5 text-sm" aria-label="Why you can't join yet">
      {blockers.map((b) => (
        <li key={b} className="flex items-start gap-2 text-muted">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
          <span>
            {describeJoinBlocker(b, comp)}
            {b === "email_not_verified" && me?.email ? <> <ResendVerification email={me.email} /></> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The primary "Join" call to action: sign-in prompt when signed out, the join dialog when allowed,
 * otherwise the reasons the viewer cannot join. Renders nothing for existing participants.
 */
export function JoinAction({ comp, size = "md", showBlockers = true }: { comp: CompetitionDetail; size?: "md" | "lg"; showBlockers?: boolean }) {
  const me = useMe();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  if (comp.viewer.is_participant) return null;
  if (me.isPending) return null;
  if (!me.data || !comp.viewer.is_authenticated) {
    return (
      <div className="flex flex-col items-start gap-2">
        <LinkButton href={`/login?next=${encodeURIComponent(pathname || `/competitions/${comp.slug}`)}`} size={size} icon={<LogIn className="h-4 w-4" aria-hidden />}>
          Sign in to join
        </LinkButton>
        <SignInPrompt text="Registration is free for students." />
      </div>
    );
  }
  const staffOnly = comp.viewer.join_blockers.includes("staff_cannot_participate");
  if (staffOnly) return null;
  return (
    <div className="flex flex-col items-start gap-3">
      {comp.viewer.can_join ? (
        <>
          <Button size={size} icon={<UserPlus className="h-4 w-4" aria-hidden />} onClick={() => setOpen(true)}>
            Join competition
          </Button>
          <JoinDialog comp={comp} open={open} onOpenChange={setOpen} />
        </>
      ) : (
        <Button size={size} disabled icon={<UserPlus className="h-4 w-4" aria-hidden />}>
          Join competition
        </Button>
      )}
      {showBlockers ? <JoinBlockers comp={comp} /> : null}
    </div>
  );
}
