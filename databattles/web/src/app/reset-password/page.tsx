"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { AuthCard } from "@/components/auth/auth-card";
import { AuthStatus } from "@/components/auth/auth-status";
import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { Field, FormError } from "@/components/ui/form";
import { InlineNotice } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";

function Reset() {
  const token = useSearchParams().get("token") ?? "";
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // Presentational only: shown while the existing redirect to /login completes.
  const [updated, setUpdated] = useState(false);
  const mismatch = confirm.length > 0 && confirm !== password;
  return (
    <AuthCard eyebrow="Account recovery" title="Choose a new password" footer={<Link href="/login" className="text-accent-strong hover:underline">Back to sign in</Link>}>
      {updated ? (
        <AuthStatus tone="success" celebrate title="Password updated">
          Sign in with your new password.
        </AuthStatus>
      ) : (
        <form className="space-y-5" onSubmit={async (e) => {
          e.preventDefault();
          if (mismatch) return;
          setBusy(true);
          setError(null);
          try {
            await post("/auth/reset-password", { token, password });
            toast.success("Password updated. Sign in with your new password.");
            setUpdated(true);
            router.push("/login");
          } catch (err) {
            setError(err as ApiError);
          } finally {
            setBusy(false);
          }
        }}>
          {!token ? (
            <InlineNotice tone="warning" title="Reset link required">
              Open the link from your reset email, or <Link href="/forgot-password" className="font-medium text-accent-strong underline">request a new one</Link>.
            </InlineNotice>
          ) : null}
          <Field label="New password" hint="At least 10 characters." error={error?.fields.password}>
            {(p) => <PasswordInput {...p} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          <Field
            label="Confirm password"
            error={mismatch ? "Passwords don't match." : null}
            hint={confirm.length > 0 && !mismatch ? <span className="text-success">Passwords match.</span> : undefined}
          >
            {(p) => <PasswordInput {...p} autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />}
          </Field>
          {error && !Object.keys(error.fields).length ? <FormError message={error.message} /> : null}
          <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!token || mismatch || !password}>Update password</Button>
        </form>
      )}
    </AuthCard>
  );
}

export default function ResetPasswordPage() {
  return <Suspense><Reset /></Suspense>;
}
