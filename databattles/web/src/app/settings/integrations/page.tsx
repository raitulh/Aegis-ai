"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, KeyRound, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import type { GitHubAccountStatus } from "@/components/profile/types";
import { Avatar } from "@/components/ui/avatar";
import { VerifiedBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { ErrorState, InlineNotice, SkeletonRows, Spinner } from "@/components/ui/states";
import { API_BASE, del, get } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Message } from "@/lib/types";

const ACCOUNT_KEY = ["opensource", "github", "account"] as const;

const RESULT_MESSAGES: Record<string, { tone: "success" | "danger" | "warning"; title: string; body: string }> = {
  connected: { tone: "success", title: "GitHub connected", body: "Merged pull requests to registered repositories will now be attributed to your profile." },
  state_error: {
    tone: "danger",
    title: "The connection request expired",
    body: "For your security the link request must start and finish in the same signed-in browser within 10 minutes. Please try again.",
  },
  github_linked_elsewhere: { tone: "danger", title: "Already linked elsewhere", body: "That GitHub account is linked to another DataBattles profile. Disconnect it there first." },
  github_already_linked: { tone: "warning", title: "Another GitHub account is linked", body: "Disconnect your current GitHub account before linking a different one." },
  oauth_failed: { tone: "danger", title: "GitHub sign-in failed", body: "We couldn't complete the exchange with GitHub, so nothing was linked. Please try again." },
};

function ResultNotice() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const code = params.get("github");
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (code && handled.current !== code) {
      handled.current = code;
      qc.invalidateQueries({ queryKey: ACCOUNT_KEY });
      if (code === "connected") {
        qc.invalidateQueries({ queryKey: qk.me });
        qc.invalidateQueries({ queryKey: qk.dashboard });
      }
    }
  }, [code, qc]);

  if (!code) return null;
  const msg = RESULT_MESSAGES[code] ?? {
    tone: "danger" as const,
    title: "GitHub connection failed",
    body: `GitHub returned an error (${code.slice(0, 60)}). Please try again; if it keeps failing, contact support with this code.`,
  };
  return (
    <InlineNotice
      tone={msg.tone}
      title={msg.title}
      action={<Button size="sm" variant="ghost" onClick={() => router.replace(pathname, { scroll: false })}>Dismiss</Button>}
    >
      {msg.body}
    </InlineNotice>
  );
}

function GitHubCard() {
  const me = useRequireAuth();
  const qc = useQueryClient();
  const account = useQuery({ queryKey: ACCOUNT_KEY, queryFn: () => get<GitHubAccountStatus>("/opensource/github/account"), enabled: Boolean(me.data) });
  const disconnect = useApiMutation(() => del<Message>("/opensource/github/account"), {
    success: (m) => m.message,
    invalidate: [ACCOUNT_KEY, qk.me, qk.dashboard],
    onSuccess: () => {
      if (me.data) qc.invalidateQueries({ queryKey: qk.profile(me.data.handle) });
    },
  });

  return (
    <Card>
      <CardHeader
        title={<span className="inline-flex items-center gap-2"><GitBranch className="h-4 w-4" aria-hidden />GitHub</span>}
        description="Link your GitHub account to have merged pull requests attributed to your profile and to verify you maintain your project repositories."
      />
      <CardBody>
        {account.isPending ? (
          <SkeletonRows rows={2} />
        ) : account.isError ? (
          <ErrorState error={account.error} onRetry={() => account.refetch()} />
        ) : account.data.connected ? (
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <Avatar name={account.data.login ?? "GitHub"} src={account.data.avatar_url} size={44} />
              <div>
                <p className="flex flex-wrap items-center gap-2 font-medium text-fg">
                  <a href={`https://github.com/${account.data.login}`} target="_blank" rel="noopener noreferrer" className="hover:text-accent-strong">
                    @{account.data.login}
                  </a>
                  <VerifiedBadge label="Linked via OAuth" />
                </p>
                <p className="text-xs text-muted">
                  {account.data.connected_at ? (
                    <>Connected <span title={formatDateTime(account.data.connected_at)}>{relativeTime(account.data.connected_at)}</span></>
                  ) : "Connected"}
                  {account.data.scopes ? <> · scopes: <span className="font-mono">{account.data.scopes}</span></> : <> · public profile access only</>}
                </p>
              </div>
            </div>
            <ConfirmDialog
              trigger={<Button variant="outline">Disconnect</Button>}
              title="Disconnect GitHub?"
              description="Your stored GitHub token is deleted. Merged pull requests will no longer be attributed to your profile, and maintainer verification on your projects is re-checked."
              confirmLabel="Disconnect"
              onConfirm={() => disconnect.mutateAsync(undefined).catch(() => undefined)}
            />
          </div>
        ) : account.data.oauth_enabled ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted">Not connected. You&apos;ll be sent to GitHub to approve access, then returned here.</p>
            {/* Full page navigation: the API sets a signed state cookie and redirects to GitHub. */}
            <a
              href={`${API_BASE}/opensource/github/connect`}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-[var(--radius-md)] bg-accent px-4 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
            >
              <GitBranch className="h-4 w-4" aria-hidden />
              Connect GitHub
            </a>
          </div>
        ) : (
          <InlineNotice tone="warning" title="GitHub sign-in isn't configured on this deployment">
            An administrator needs to set the GitHub OAuth client ID and secret before accounts can be linked. Your profile works without it, but
            open-source contributions can&apos;t be attributed until then.
          </InlineNotice>
        )}

        <div className="mt-6 grid gap-4 border-t border-border pt-5 text-sm sm:grid-cols-2">
          <div className="flex gap-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
            <div>
              <p className="font-medium text-fg">Least-privilege access</p>
              <p className="mt-0.5 text-muted">
                We request only the <span className="font-mono text-fg">read:user</span> scope — read-only access to your GitHub profile. No
                repository access and no write permissions.
              </p>
            </div>
          </div>
          <div className="flex gap-3">
            <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
            <div>
              <p className="font-medium text-fg">Tokens stay on the server</p>
              <p className="mt-0.5 text-muted">
                Your access token is encrypted at rest and never sent to the browser. Disconnecting deletes it immediately.
              </p>
            </div>
          </div>
        </div>
        <p className="mt-4 text-xs text-subtle">
          Only accounts linked here count — a GitHub username typed into a profile is never used for attribution. See the{" "}
          <Link href="/open-source" className="text-accent-strong hover:underline">open-source hub</Link> for registered repositories.
        </p>
      </CardBody>
    </Card>
  );
}

export default function IntegrationsSettingsPage() {
  return (
    <div className="space-y-6">
      <Suspense fallback={<Spinner />}>
        <ResultNotice />
      </Suspense>
      <GitHubCard />
    </div>
  );
}
