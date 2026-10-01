"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { OrgLogo } from "@/components/orgs/org-ui";
import type { OrgDetail } from "@/components/orgs/types";
import { useRequireAuthKeepQuery } from "@/components/orgs/use-auth-redirect";
import { VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
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

  return (
    <Container size="md" className="py-16">
      <Card className="mx-auto max-w-md">
        <CardBody className="flex flex-col items-center px-6 py-10 text-center">
          <h1 className="sr-only">Confirm institutional email</h1>
          {state === "working" ? (
            <Spinner label="Confirming your institutional email" />
          ) : state === "ok" && org ? (
            <>
              <CheckCircle2 className="h-10 w-10 text-success" aria-hidden />
              <p className="mt-3 text-lg font-semibold text-fg">You’re a verified member</p>
              <div className="mt-4 flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-surface-2 px-4 py-3">
                <OrgLogo name={org.name} logoUrl={org.logo_url} accentColor={org.accent_color} size={36} />
                <div className="text-left">
                  <p className="text-sm font-medium text-fg">{org.name}</p>
                  <VerifiedBadge label="Verified member" />
                </div>
              </div>
              <p className="mt-4 text-sm text-muted">Your profile now shows this membership as verified. Your institutional email is not displayed.</p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                <LinkButton href={`/orgs/${org.slug}`}>Go to {org.name}</LinkButton>
                <LinkButton href={`/u/${me.data.handle}`} variant="secondary">View your profile</LinkButton>
              </div>
            </>
          ) : (
            <>
              <XCircle className="h-10 w-10 text-danger" aria-hidden />
              <p className="mt-3 text-lg font-semibold text-fg">Couldn’t confirm this link</p>
              <p className="mt-2 text-sm text-muted" role="alert">{message}</p>
              <LinkButton href="/orgs/mine" variant="secondary" className="mt-6">My organizations</LinkButton>
            </>
          )}
        </CardBody>
      </Card>
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
