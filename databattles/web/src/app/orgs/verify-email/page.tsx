"use client";

import { useQueryClient } from "@tanstack/react-query";
import { XCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { OrgLogo } from "@/components/orgs/org-ui";
import { AccentEdge, AccentGlow, StateMark, Stepper, SuccessMark } from "@/components/orgs/org-visuals";
import type { OrgDetail } from "@/components/orgs/types";
import { useRequireAuthKeepQuery } from "@/components/orgs/use-auth-redirect";
import { VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { Spinner } from "@/components/ui/states";
import { ApiError, errorMessage, post } from "@/lib/api";
import { qk } from "@/lib/query";

function ConfirmOrgEmail() {
  const token = useSearchParams().get("token");
  const me = useRequireAuthKeepQuery();
  const qc = useQueryClient();
  const [state, setState] = useState<"working" | "ok" | "error">("working");
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [message, setMessage] = useState("");
  const ran = useRef(false);

  useEffect(() => {
    if (!me.data || ran.current) return;
    ran.current = true;
    if (!token || token.length < 10) {
      setState("error");
      setMessage("This link is missing its token or is incomplete. Copy the full link from the email.");
      return;
    }
    post<OrgDetail>("/orgs/verify-email/confirm", { token })
      .then(async (res) => {
        setOrg(res);
        setState("ok");
        await Promise.all([
          qc.invalidateQueries({ queryKey: qk.me }),
          qc.invalidateQueries({ queryKey: qk.org(res.slug) }),
          qc.invalidateQueries({ queryKey: ["orgs", "mine"] }),
        ]);
      })
      .catch((e) => {
        setState("error");
        setMessage(
          e instanceof ApiError && e.code === "token_invalid"
            ? "This verification link is invalid or has expired. Links work once, expire after 24 hours, and must be opened while signed in to the account that requested them."
            : errorMessage(e),
        );
      });
  }, [me.data, token, qc]);

  if (me.isPending || !me.data) return <Spinner label="Checking your session" />;

  const ok = state === "ok" && org;

  return (
    <Container size="md" className="py-12 sm:py-16">
      <Stepper
        label="Email confirmation progress"
        className="mx-auto mb-6 max-w-lg"
        steps={[
          { label: "Signed in", state: "done" },
          { label: "Confirm link", state: state === "ok" ? "done" : "current" },
          { label: "Verified member", state: ok ? "done" : "upcoming" },
        ]}
      />
      <div className="relative isolate mx-auto w-full max-w-lg overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen text-center shadow-elevated animate-rise">
        <AccentEdge color={ok ? org.accent_color : undefined} />
        {ok ? <AccentGlow color={org.accent_color} strength={18} className="-top-28 left-1/2 h-60 w-[30rem] max-w-full -translate-x-1/2" /> : null}
        <div className="flex flex-col items-center px-6 py-10 sm:px-10">
          <h1 className="sr-only">Confirm institutional email</h1>
          {state === "working" ? (
            <Spinner label="Confirming your institutional email" />
          ) : ok ? (
            <div role="status" className="flex flex-col items-center">
              <SuccessMark />
              <p className="mt-5 text-xl font-semibold tracking-[-0.02em] text-fg">You’re a verified member</p>
              <div className="mt-5 flex max-w-full items-center gap-3 rounded-[var(--radius-lg)] border border-border bg-bg-elevated/70 px-4 py-3 text-left animate-pop [animation-delay:200ms]">
                <OrgLogo name={org.name} logoUrl={org.logo_url} accentColor={org.accent_color} size={40} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-fg">{org.name}</p>
                  <div className="mt-1"><VerifiedBadge label="Verified member" /></div>
                </div>
              </div>
              <p className="mt-5 max-w-sm text-sm leading-relaxed text-muted">Your profile now shows this membership as verified. Your institutional email is not displayed.</p>
              <div className="mt-7 flex flex-wrap justify-center gap-2">
                <LinkButton href={`/orgs/${org.slug}`}>Go to {org.name}</LinkButton>
                <LinkButton href={`/u/${me.data.handle}`} variant="secondary">View your profile</LinkButton>
              </div>
            </div>
          ) : (
            <>
              <StateMark tone="danger" icon={<XCircle />} />
              <p className="mt-5 text-lg font-semibold tracking-[-0.015em] text-fg">Couldn’t confirm this link</p>
              <p className="mt-2 max-w-sm text-sm leading-relaxed text-muted" role="alert">{message}</p>
              <LinkButton href="/orgs/mine" variant="secondary" className="mt-7">My organizations</LinkButton>
            </>
          )}
        </div>
      </div>
    </Container>
  );
}

export default function OrgVerifyEmailPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <ConfirmOrgEmail />
    </Suspense>
  );
}
