"use client";

import Link from "next/link";
import { useState } from "react";

import { AuthCard, DevMailboxHint } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { InlineNotice } from "@/components/ui/states";
import { errorMessage, post } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <AuthCard title="Reset your password" subtitle="We'll email you a link that works once and expires in 30 minutes." footer={<Link href="/login" className="text-accent-strong hover:underline">Back to sign in</Link>}>
      {sent ? (
        <InlineNotice tone="success" title="Check your inbox">If an account exists for {email}, a reset link is on its way.</InlineNotice>
      ) : (
        <form className="space-y-4" onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await post("/auth/forgot-password", { email });
            setSent(true);
          } catch (err) {
            setError(errorMessage(err));
          } finally {
            setBusy(false);
          }
        }}>
          <Field label="Email">{(p) => <Input {...p} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <FormError message={error} />
          <Button type="submit" className="w-full" loading={busy}>Send reset link</Button>
        </form>
      )}
      <DevMailboxHint />
    </AuthCard>
  );
}
