"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { ApiError, post } from "@/lib/api";

function Reset() {
  const token = useSearchParams().get("token") ?? "";
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = confirm.length > 0 && confirm !== password;
  return (
    <AuthCard title="Choose a new password" footer={<Link href="/login" className="text-accent-strong hover:underline">Back to sign in</Link>}>
      <form className="space-y-4" onSubmit={async (e) => {
        e.preventDefault();
        if (mismatch) return;
        setBusy(true);
        setError(null);
        try {
          await post("/auth/reset-password", { token, password });
          toast.success("Password updated. Sign in with your new password.");
          router.push("/login");
        } catch (err) {
          setError(err as ApiError);
        } finally {
          setBusy(false);
        }
      }}>
        <Field label="New password" hint="At least 10 characters." error={error?.fields.password}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
        </Field>
        <Field label="Confirm password" error={mismatch ? "Passwords don't match." : null}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />}
        </Field>
        {error && !Object.keys(error.fields).length ? <FormError message={error.message} /> : null}
        <Button type="submit" className="w-full" loading={busy} disabled={!token || mismatch || !password}>Update password</Button>
      </form>
    </AuthCard>
  );
}

export default function ResetPasswordPage() {
  return <Suspense><Reset /></Suspense>;
}
