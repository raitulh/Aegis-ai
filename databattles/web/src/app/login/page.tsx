"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { AuthCard, DevMailboxHint, OAuthButtons } from "@/components/auth/auth-card";
import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { InlineNotice } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";
import { qk } from "@/lib/query";
import type { Me } from "@/lib/types";

const OAUTH_ERRORS: Record<string, string> = {
  oauth_state: "The sign-in session expired. Please try again.",
  oauth_failed: "The external provider could not complete sign-in.",
  oauth_email_unverified: "Your provider account email is not verified.",
  oauth_denied: "Sign-in was canceled on the provider's page.",
};

function safeNext(n: string | null): string {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/dashboard";
}

function LoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [resent, setResent] = useState(false);
  const oauthError = params.get("error");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ user: Me }>("/auth/login", { email, password });
      qc.setQueryData(qk.me, res.user);
      router.push(res.user.onboarding_completed ? next : `/onboarding?next=${encodeURIComponent(next)}`);
      router.refresh();
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      eyebrow="Sign in"
      title="Welcome back"
      subtitle="Sign in to continue to DataBattles."
      footer={<>New here? <Link href={next !== "/dashboard" ? `/signup?next=${encodeURIComponent(next)}` : "/signup"} className="font-medium text-accent-strong hover:underline">Create an account</Link></>}
    >
      {oauthError ? <div className="mb-5"><InlineNotice tone="danger">{OAUTH_ERRORS[oauthError] ?? "Sign-in failed. Please try again."}</InlineNotice></div> : null}
      <OAuthButtons next={next} />
      <form onSubmit={submit} className="space-y-5" noValidate>
        <Field label="Email" error={error?.fields.email}>
          {(p) => <Input {...p} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />}
        </Field>
        {/* The reset link sits beside (not inside) the label so the field's accessible name is exactly "Password". */}
        <div className="relative">
          <Field label="Password" error={error?.fields.password}>
            {(p) => <PasswordInput {...p} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />}
          </Field>
          <Link href="/forgot-password" className="absolute -top-2.5 right-0 rounded-sm py-2.5 text-xs font-medium text-accent-strong hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">Forgot password?</Link>
        </div>
        {error && !Object.keys(error.fields).length ? (
          error.code === "email_not_verified" ? (
            <InlineNotice tone="warning" title="Verify your email first" action={
              <Button size="sm" variant="secondary" disabled={resent} onClick={async () => { await post("/auth/resend-verification", { email }); setResent(true); }}>
                {resent ? "Link sent" : "Resend link"}
              </Button>}>
              We sent a verification link when you signed up.
            </InlineNotice>
          ) : <FormError message={error.message} />
        ) : null}
        <Button type="submit" size="lg" className="w-full" loading={busy}>Sign in</Button>
      </form>
      <DevMailboxHint />
    </AuthCard>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
