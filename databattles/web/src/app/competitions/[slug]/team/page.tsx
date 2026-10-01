"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Crown,
  ExternalLink,
  Lock,
  LogOut,
  MailPlus,
  MessagesSquare,
  Save,
  Search,
  Trash2,
  UserMinus,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Sparkline } from "@/components/charts/charts";
import { ck, useCompetition } from "@/components/competition/context";
import { DateTime } from "@/components/competition/datetime";
import { InvitationCard } from "@/components/competition/invitation-card";
import { JoinAction, useRefreshParticipation } from "@/components/competition/join";
import type { Invitation, ScorePoint, TeamListItem } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { AvatarStack } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/form";
import { ProgressBar } from "@/components/ui/misc";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, InlineNotice, SignInPrompt, SkeletonRows, Spinner } from "@/components/ui/states";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, formatScore, relativeTime } from "@/lib/format";
import { useApiMutation, useDebounced, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail, Message, Page, TeamOut } from "@/lib/types";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
/** Swallow the rejection after `useApiMutation` has already toasted it (ConfirmDialog awaits the promise). */
const settle = (p: Promise<unknown>) => p.then(() => undefined, () => undefined);

function teamKeys(slug: string) {
  return [qk.team(slug), qk.competition(slug), ["competitions", slug, "teams"]] as const;
}

// ----------------------------------------------------------------------------- invitations

function useClaim(token: string | null, enabled: boolean) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["team-invite-claim", token],
    queryFn: async () => {
      const inv = await post<Invitation>("/me/team-invitations/claim", { token });
      await qc.invalidateQueries({ queryKey: ck.myInvitations });
      return inv;
    },
    enabled: Boolean(token) && enabled,
    retry: false,
    staleTime: Infinity,
    gcTime: 5 * 60_000,
  });
}

function ClaimNotice({ token }: { token: string }) {
  const me = useMe();
  const pathname = usePathname();
  const router = useRouter();
  const claim = useClaim(token, Boolean(me.data));
  const dismiss = () => router.replace(pathname, { scroll: false });

  if (me.isPending) return <Spinner label="Checking your invitation" />;
  if (!me.data) {
    const next = `${pathname}?invite=${encodeURIComponent(token)}`;
    return (
      <InlineNotice
        tone="info"
        title="You've been invited to a team"
        action={<LinkButton href={`/login?next=${encodeURIComponent(next)}`} size="sm">Sign in to respond</LinkButton>}
      >
        Sign in (or create an account with the invited email address) to see and accept the invitation.
      </InlineNotice>
    );
  }
  if (claim.isPending) return <Spinner label="Checking your invitation" />;
  if (claim.isError) {
    const e = claim.error;
    return (
      <InlineNotice tone="danger" title="This invitation link can't be used" action={<Button size="sm" variant="secondary" onClick={dismiss}>Dismiss</Button>}>
        {e instanceof ApiError ? e.message : "The invitation could not be loaded."}
      </InlineNotice>
    );
  }
  return (
    <InlineNotice tone="success" title="Invitation found" action={<Button size="sm" variant="ghost" onClick={dismiss}>Dismiss</Button>}>
      Your invitation to <strong>{claim.data.team_name}</strong> is below — accept it to join the team.
    </InlineNotice>
  );
}

function MyInvitations({ comp, highlightId, onResponded }: { comp: CompetitionDetail; highlightId?: string; onResponded: () => void }) {
  const me = useMe().data;
  const query = useQuery({
    queryKey: ck.myInvitations,
    queryFn: () => get<Invitation[]>("/me/team-invitations"),
    enabled: Boolean(me),
  });
  const mine = (query.data ?? []).filter((i) => i.competition_slug === comp.slug);
  if (!me || !mine.length) return null;
  return (
    <section aria-labelledby="my-invites-heading" className="space-y-3">
      <h2 id="my-invites-heading" className="text-lg font-semibold text-fg">
        Your invitations <Badge tone="accent">{mine.length}</Badge>
      </h2>
      {comp.viewer.team && !comp.viewer.team.is_solo ? (
        <p className="text-sm text-muted">You&apos;re already on a team. To accept, leave your current team first.</p>
      ) : null}
      {mine.map((inv) => (
        <InvitationCard
          key={inv.id}
          invitation={inv}
          showCompetition={false}
          alreadyParticipant={comp.viewer.is_participant}
          onResponded={onResponded}
          className={cn(inv.id === highlightId && "ring-2 ring-accent")}
        />
      ))}
    </section>
  );
}

// ----------------------------------------------------------------------------- create team

function CreateTeam({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const create = useApiMutation((n: string) => post<TeamOut>(`/competitions/${encodeURIComponent(slug)}/teams`, { name: n }), {
    success: (t) => `Team ${t.name} created — now invite your teammates.`,
    invalidate: teamKeys(slug),
    onSuccess: (t) => qc.setQueryData(qk.team(slug), t),
  });
  return (
    <Card>
      <CardHeader title="Create a team" description={`Teams have ${comp.team_min_size}–${comp.team_max_size} members. You'll be the captain and can invite others.`} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim().length >= 2) create.mutate(name.trim());
        }}
      >
        <CardBody>
          <Field label="Team name" required hint="2–60 characters, unique within this competition." error={create.error?.fields.name}>
            {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} autoComplete="off" />}
          </Field>
        </CardBody>
        <CardFooter>
          <Button type="submit" loading={create.isPending} disabled={name.trim().length < 2} icon={<Users className="h-4 w-4" aria-hidden />}>
            Create team
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

// ----------------------------------------------------------------------------- team management

function InviteForm({ comp, team }: { comp: CompetitionDetail; team: TeamOut }) {
  const slug = comp.slug;
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const isEmail = EMAIL_RE.test(trimmed);
  const invite = useApiMutation(
    (body: { handle?: string; email?: string }) => post<Message>(`/competitions/${encodeURIComponent(slug)}/teams/${team.id}/invitations`, body),
    {
      success: () => (isEmail ? "Invitation emailed" : "Invitation sent"),
      invalidate: [qk.team(slug)],
      onSuccess: () => setValue(""),
    },
  );
  const slots = team.max_size - team.members.length - team.pending_invitations.length;
  if (slots <= 0) {
    return <p className="text-sm text-muted">Your team is full ({team.max_size} members including pending invitations).</p>;
  }
  return (
    <form
      className="flex flex-col gap-2 sm:flex-row sm:items-start"
      onSubmit={(e) => {
        e.preventDefault();
        if (!trimmed) return;
        invite.mutate(isEmail ? { email: trimmed } : { handle: trimmed.replace(/^@/, "") });
      }}
    >
      <Field
        className="flex-1"
        label="Invite by handle or email"
        hint={`${slots} ${slots === 1 ? "spot" : "spots"} left. People without an account get an email link.`}
        error={invite.error?.fields.handle ?? invite.error?.fields.email}
      >
        {(p) => <Input {...p} value={value} onChange={(e) => setValue(e.target.value)} placeholder="@handle or name@university.edu" autoComplete="off" maxLength={320} />}
      </Field>
      <Button type="submit" className="sm:mt-6" loading={invite.isPending} disabled={!trimmed} icon={<MailPlus className="h-4 w-4" aria-hidden />}>
        Send invite
      </Button>
    </form>
  );
}

function TeamSettings({ comp, team }: { comp: CompetitionDetail; team: TeamOut }) {
  const slug = comp.slug;
  const qc = useQueryClient();
  const [name, setName] = useState(team.name);
  const [workspace, setWorkspace] = useState(team.workspace_url ?? "");
  useEffect(() => {
    setName(team.name);
    setWorkspace(team.workspace_url ?? "");
  }, [team.name, team.workspace_url]);
  const save = useApiMutation(
    (body: { name?: string; workspace_url?: string }) => patch<TeamOut>(`/competitions/${encodeURIComponent(slug)}/teams/${team.id}`, body),
    { success: "Team updated", invalidate: teamKeys(slug), onSuccess: (t) => qc.setQueryData(qk.team(slug), t) },
  );
  const nameChanged = name.trim() !== team.name;
  const wsChanged = workspace.trim() !== (team.workspace_url ?? "");
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const body: { name?: string; workspace_url?: string } = {};
        if (nameChanged && !team.locked) body.name = name.trim();
        if (wsChanged) body.workspace_url = workspace.trim();
        if (Object.keys(body).length) save.mutate(body);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Team name" error={save.error?.fields.name} hint={team.locked ? "Names are locked with team membership." : undefined}>
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} disabled={team.locked} />}
        </Field>
        <Field label="Shared workspace link" hint="Colab, Kaggle, GitHub or a doc — visible to your team only." error={save.error?.fields.workspace_url}>
          {(p) => <Input {...p} type="url" inputMode="url" value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="https://…" maxLength={500} />}
        </Field>
      </div>
      <div className="flex justify-end">
        <Button type="submit" variant="secondary" loading={save.isPending} disabled={!(nameChanged && !team.locked) && !wsChanged} icon={<Save className="h-4 w-4" aria-hidden />}>
          Save changes
        </Button>
      </div>
    </form>
  );
}

function TeamCard({ comp, team, meId }: { comp: CompetitionDetail; team: TeamOut; meId: string }) {
  const slug = comp.slug;
  const base = `/competitions/${encodeURIComponent(slug)}/teams/${team.id}`;
  const isCaptain = team.captain_id === meId;
  const size = team.members.length;
  const refresh = useRefreshParticipation(slug);
  const keys = teamKeys(slug);

  const transfer = useApiMutation((userId: string) => post<TeamOut>(`${base}/transfer`, { user_id: userId }), {
    success: "Captaincy transferred",
    invalidate: keys,
  });
  const remove = useApiMutation(
    ({ userId, reason }: { userId: string; reason: string }) => post<Message>(`${base}/members/${userId}/remove`, { reason }),
    { success: "Member removed", invalidate: keys },
  );
  const revoke = useApiMutation((invId: string) => del<Message>(`${base}/invitations/${invId}`), {
    success: "Invitation revoked",
    invalidate: [qk.team(slug)],
  });
  const leave = useApiMutation(() => post<Message>(`${base}/leave`), {
    success: "You left the team",
    onSuccess: () => void refresh(),
  });
  const remove_team = useApiMutation(() => del<Message>(base), {
    success: "Team deleted",
    onSuccess: () => void refresh(),
  });

  const points = ((team.score_history ?? []) as unknown as ScorePoint[]).map((p) => p.score).filter((s): s is number => typeof s === "number");
  const belowMin = size < team.min_size;
  const canLeave = !team.locked && !(isCaptain && size > 1) && !(size === 1 && team.submission_count > 0);
  const leaveHint = team.locked
    ? "Team membership is locked."
    : isCaptain && size > 1
      ? "Transfer captaincy to another member before leaving."
      : size === 1 && team.submission_count > 0
        ? "You're the only member of a team with submissions, so the team must stay on record."
        : undefined;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2 text-base">
            {team.is_solo ? "Your entry" : team.name}
            {team.is_solo ? <Badge tone="outline">Individual</Badge> : null}
            {isCaptain && !team.is_solo ? <Badge tone="accent" icon={<Crown className="h-3 w-3" aria-hidden />}>You&apos;re captain</Badge> : null}
            {team.locked ? <Badge tone="warning" icon={<Lock className="h-3 w-3" aria-hidden />}>Locked</Badge> : null}
          </span>
        }
        description={`Created ${relativeTime(team.created_at)} · ${formatNumber(team.submission_count)} ${team.submission_count === 1 ? "submission" : "submissions"}`}
        action={
          points.length >= 2 ? (
            <div className="text-right">
              <Sparkline data={points} label="Public score trend" width={110} height={28} />
              <p className="text-[11px] text-subtle">latest {formatScore(points[points.length - 1], 4)}</p>
            </div>
          ) : null
        }
      />
      <CardBody className="space-y-6">
        {team.max_size > 1 ? (
          <div>
            <div className="mb-1.5 flex justify-between text-xs text-muted">
              <span>Team size</span>
              <span>
                {size} of {team.max_size} {team.min_size > 1 ? `· minimum ${team.min_size}` : ""}
              </span>
            </div>
            <ProgressBar value={(size / team.max_size) * 100} label="Team size" />
            {belowMin ? (
              <p className="mt-2 text-xs font-medium text-warning">
                Your team needs at least {team.min_size} members before it can submit — invite {team.min_size - size} more.
              </p>
            ) : null}
          </div>
        ) : null}

        {team.workspace_url ? (
          <a href={team.workspace_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-strong hover:underline">
            Team workspace <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        ) : null}

        <section aria-labelledby="members-heading">
          <h3 id="members-heading" className="mb-2 text-sm font-semibold text-fg">Members</h3>
          <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
            {team.members.map((m) => {
              const self = m.user.id === meId;
              const captain = m.user.id === team.captain_id;
              return (
                <li key={m.user.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex min-w-0 items-center gap-2">
                    <UserLink user={m.user} size={28} />
                    {captain ? <Badge tone="accent" icon={<Crown className="h-3 w-3" aria-hidden />}>Captain</Badge> : null}
                    {self ? <Badge tone="outline">You</Badge> : null}
                    <span className="hidden text-xs text-subtle sm:inline">joined {relativeTime(m.joined_at)}</span>
                  </div>
                  {isCaptain && !self ? (
                    <div className="flex flex-wrap gap-1.5">
                      <ConfirmDialog
                        trigger={<Button size="sm" variant="ghost" icon={<Crown className="h-3.5 w-3.5" aria-hidden />}>Make captain</Button>}
                        title={`Make ${m.user.display_name} captain?`}
                        description="The captain manages invitations, members and team settings. You'll become a regular member."
                        confirmLabel="Transfer captaincy"
                        tone="primary"
                        onConfirm={() => settle(transfer.mutateAsync(m.user.id))}
                      />
                      {!team.locked ? (
                        <ConfirmDialog
                          trigger={<Button size="sm" variant="ghost" className="text-danger" icon={<UserMinus className="h-3.5 w-3.5" aria-hidden />}>Remove</Button>}
                          title={`Remove ${m.user.display_name} from the team?`}
                          description="They'll be notified. Submissions they made stay with the team."
                          confirmLabel="Remove member"
                          requireReason
                          reasonLabel="Reason (recorded in the audit log)"
                          onConfirm={(reason) => settle(remove.mutateAsync({ userId: m.user.id, reason }))}
                        />
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>

        {isCaptain && team.pending_invitations.length ? (
          <section aria-labelledby="pending-heading">
            <h3 id="pending-heading" className="mb-2 text-sm font-semibold text-fg">Pending invitations</h3>
            <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
              {team.pending_invitations.map((inv) => (
                <li key={inv.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    {inv.invitee ? <UserLink user={inv.invitee} size={24} /> : <span className="font-mono text-sm text-fg">{inv.invitee_email ?? "Email invitation"}</span>}
                    <p className="text-xs text-subtle">Sent {relativeTime(inv.created_at)} · expires {relativeTime(inv.expires_at)}</p>
                  </div>
                  {!team.locked ? (
                    <ConfirmDialog
                      trigger={<Button size="sm" variant="ghost">Revoke</Button>}
                      title="Revoke this invitation?"
                      description="The link stops working and the spot opens up again."
                      confirmLabel="Revoke"
                      onConfirm={() => settle(revoke.mutateAsync(inv.id))}
                    />
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {isCaptain && !team.locked && team.max_size > 1 ? (
          <section aria-labelledby="invite-heading">
            <h3 id="invite-heading" className="mb-2 text-sm font-semibold text-fg">{team.is_solo ? "Invite teammates" : "Invite members"}</h3>
            {team.is_solo ? (
              <p className="mb-3 text-sm text-muted">You&apos;re competing individually. Invite others to turn your entry into a team.</p>
            ) : null}
            <InviteForm comp={comp} team={team} />
          </section>
        ) : null}

        {isCaptain ? (
          <section aria-labelledby="settings-heading">
            <h3 id="settings-heading" className="mb-2 text-sm font-semibold text-fg">Team settings</h3>
            <TeamSettings comp={comp} team={team} />
          </section>
        ) : null}
      </CardBody>
      {!team.is_solo ? (
        <CardFooter className="flex-wrap justify-between">
          <span className="text-xs text-subtle">{leaveHint}</span>
          <div className="flex flex-wrap gap-2">
            {isCaptain && !team.locked && team.submission_count === 0 ? (
              <ConfirmDialog
                trigger={<Button size="sm" variant="ghost" className="text-danger" icon={<Trash2 className="h-4 w-4" aria-hidden />}>Delete team</Button>}
                title={`Delete ${team.name}?`}
                description="All members and pending invitations are removed. This can't be undone."
                confirmLabel="Delete team"
                onConfirm={() => settle(remove_team.mutateAsync(undefined))}
              />
            ) : null}
            <ConfirmDialog
              trigger={<Button size="sm" variant="outline" disabled={!canLeave} icon={<LogOut className="h-4 w-4" aria-hidden />}>Leave team</Button>}
              title={`Leave ${team.name}?`}
              description={
                comp.team_min_size === 1
                  ? "You'll continue in the competition as an individual. The team keeps its submissions."
                  : "You'll need to create or join another team to keep submitting. The team keeps its submissions."
              }
              confirmLabel="Leave team"
              onConfirm={() => settle(leave.mutateAsync(undefined))}
            />
          </div>
        </CardFooter>
      ) : null}
    </Card>
  );
}

function Withdraw({ comp }: { comp: CompetitionDetail }) {
  const refresh = useRefreshParticipation(comp.slug);
  const router = useRouter();
  const withdraw = useApiMutation(() => post<Message>(`/competitions/${encodeURIComponent(comp.slug)}/withdraw`), {
    success: "You left the competition",
    onSuccess: () => {
      void refresh();
      router.push(`/competitions/${comp.slug}`);
    },
  });
  if (!comp.viewer.is_participant || comp.lifecycle !== "published") return null;
  return (
    <Card>
      <CardHeader title="Leave competition" description="Withdraw your registration. Individual entries that already submitted stay on record and can't be withdrawn." />
      <CardFooter className="justify-start border-t-0 pt-0">
        <ConfirmDialog
          trigger={<Button size="sm" variant="outline" className="text-danger">Withdraw from competition</Button>}
          title={`Withdraw from ${comp.title}?`}
          description="You'll leave your team and lose access to participant-only pages. You can re-join later while registration is open."
          confirmLabel="Withdraw"
          onConfirm={() => settle(withdraw.mutateAsync(undefined))}
        />
      </CardFooter>
    </Card>
  );
}

// ----------------------------------------------------------------------------- sidebar

function TeamRulesCard({ comp }: { comp: CompetitionDetail }) {
  return (
    <Card>
      <CardHeader title="Team rules" />
      <CardBody className="space-y-3 text-sm text-muted">
        <p>
          {comp.team_max_size === 1 ? (
            <>This competition is <strong className="text-fg">individual only</strong>.</>
          ) : (
            <>Teams of <strong className="text-fg">{comp.team_min_size}–{comp.team_max_size}</strong> members.</>
          )}
        </p>
        {comp.team_lock_at ? (
          <div>
            <p className="text-xs text-subtle">Team changes lock</p>
            <DateTime value={comp.team_lock_at} eventTimeZone={comp.timezone} relative className="text-fg" />
          </div>
        ) : (
          <p>Team changes lock when the competition ends.</p>
        )}
        {comp.team_max_size > 1 ? (
          <ul className="list-disc space-y-1 pl-5 text-xs">
            <li>Pending invitations count toward the size limit.</li>
            <li>Captains must transfer captaincy before leaving.</li>
            <li>Teams with submissions can&apos;t be deleted; submissions always stay with the team.</li>
            <li>You can merge an individual entry into a team only before it has submissions.</li>
          </ul>
        ) : null}
      </CardBody>
    </Card>
  );
}

function BrowseTeams({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const debounced = useDebounced(q.trim(), 300);
  useEffect(() => setPage(1), [debounced]);
  const params = { page, page_size: 10, q: debounced || undefined };
  const query = useQuery({
    queryKey: ck.teams(slug, params),
    queryFn: ({ signal }) => get<Page<TeamListItem>>(`/competitions/${encodeURIComponent(slug)}/teams`, params, signal),
    placeholderData: keepPreviousData,
    enabled: comp.team_max_size > 1,
  });
  if (comp.team_max_size === 1) return null;
  return (
    <Card>
      <CardHeader title="Teams" description={`${formatNumber(comp.team_count)} teams and individual entries`} />
      <CardBody className="space-y-3">
        <div className="relative">
          <label htmlFor="team-search" className="sr-only">Search teams</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input id="team-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search teams…" className="h-9 pl-9" maxLength={60} />
        </div>
        {query.isPending ? (
          <SkeletonRows rows={4} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.data.items.length === 0 ? (
          <p className="py-4 text-center text-sm text-subtle">{debounced ? "No teams match that name." : "No teams yet — be the first."}</p>
        ) : (
          <ul className={cn("divide-y divide-border", query.isPlaceholderData && "opacity-60")}>
            {query.data.items.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-fg">
                    {t.is_solo && t.members[0] ? t.members[0].display_name : t.name}
                    {comp.viewer.team?.id === t.id ? <Badge tone="accent" className="ml-2">Yours</Badge> : null}
                  </p>
                  <p className="text-xs text-subtle">{t.is_solo ? "Individual" : `${t.member_count} ${t.member_count === 1 ? "member" : "members"}`}</p>
                </div>
                <AvatarStack people={t.members} max={4} size={22} />
              </li>
            ))}
          </ul>
        )}
        {query.data ? <Pagination page={page} pageSize={10} total={query.data.total} onPage={setPage} /> : null}
        <p className="border-t border-border pt-3 text-xs text-muted">
          Looking for teammates?{" "}
          <Link href={`/competitions/${slug}/discussion`} className="inline-flex items-center gap-1 font-medium text-accent-strong hover:underline">
            <MessagesSquare className="h-3.5 w-3.5" aria-hidden /> Ask in the discussion
          </Link>
        </p>
      </CardBody>
    </Card>
  );
}

// ----------------------------------------------------------------------------- page

function MyTeamSection({ comp }: { comp: CompetitionDetail }) {
  const me = useMe();
  const slug = comp.slug;
  const team = useQuery({
    queryKey: qk.team(slug),
    queryFn: () => get<TeamOut | null>(`/competitions/${encodeURIComponent(slug)}/team`),
    enabled: Boolean(me.data),
  });

  if (me.isPending) return <SkeletonRows rows={4} />;
  if (!me.data) {
    return (
      <Card>
        <CardBody className="py-8 text-center">
          <SignInPrompt text="Sign in to manage your team." />
        </CardBody>
      </Card>
    );
  }
  if (team.isPending) return <SkeletonRows rows={4} />;
  if (team.isError) return <ErrorState error={team.error} onRetry={() => team.refetch()} />;

  if (team.data) return <TeamCard comp={comp} team={team.data} meId={me.data.id} />;

  if (!comp.viewer.is_participant) {
    return (
      <EmptyState
        icon={<Users className="h-5 w-5" />}
        title="You're not registered yet"
        description="Join the competition to create a team — or accept an invitation above, which registers you automatically."
        action={<JoinAction comp={comp} />}
      />
    );
  }
  const locked = Boolean(comp.team_lock_at && new Date(comp.team_lock_at).getTime() <= Date.now()) || comp.status === "ended" || comp.status === "completed";
  if (locked) {
    return <InlineNotice tone="warning" title="Teams are locked">Team membership can no longer change in this competition.</InlineNotice>;
  }
  if (comp.team_max_size === 1) {
    return <InlineNotice tone="info" title="Individual competition">Your individual entry is created automatically when you join.</InlineNotice>;
  }
  return <CreateTeam comp={comp} />;
}

function TeamPageInner() {
  const comp = useCompetition();
  const params = useSearchParams();
  const token = params.get("invite");
  const me = useMe();
  const claim = useClaim(token, Boolean(me.data));
  const router = useRouter();
  const pathname = usePathname();

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0 space-y-6">
        {token ? <ClaimNotice token={token} /> : null}
        <MyInvitations
          comp={comp}
          highlightId={claim.data?.id}
          onResponded={() => {
            if (token) router.replace(pathname, { scroll: false });
          }}
        />
        <MyTeamSection comp={comp} />
        <Withdraw comp={comp} />
      </div>
      <aside className="space-y-4" aria-label="Team information">
        <TeamRulesCard comp={comp} />
        <BrowseTeams comp={comp} />
      </aside>
    </div>
  );
}

export default function TeamPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={6} />}>
      <TeamPageInner />
    </Suspense>
  );
}
