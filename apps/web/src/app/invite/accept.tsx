"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/logo";
import { Field, Input } from "@/components/ui/forms";
import { Button } from "@/components/ui/primitives";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/queries";

/** Accept a workspace invitation: signed-in users join directly; new users create an account. */
export function AcceptInvitation() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const session = useSession();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signedIn = !!session.data && !session.data.is_guest;
  const here = `/invite?token=${encodeURIComponent(token)}`;

  async function accept(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/invitations/accept", signedIn ? { token } : { token, full_name: name || undefined, password });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Sign in as the invited email address to accept this invitation." : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg)] px-4">
      <div className="w-full max-w-sm">
        <Link href="/" className="mb-8 flex justify-center rounded-md focus-ring">
          <Logo />
        </Link>
        <div className="rounded-[var(--radius-xl)] border border-[var(--color-border-strong)] bg-[var(--color-bg-elevated)] p-7 shadow-[var(--shadow-lg)]">
          <h1 className="text-xl font-semibold tracking-tight">Join a workspace</h1>
          {!token ? (
            <p className="mt-2 text-sm text-[var(--color-critical)]">This invitation link is incomplete. Ask the person who invited you for a new link.</p>
          ) : session.isLoading ? (
            <div className="mt-6 h-24 skeleton" />
          ) : signedIn ? (
            <div className="mt-4 space-y-4">
              <p className="text-sm text-[var(--color-text-muted)]">
                You are signed in as <span className="font-medium text-[var(--color-text)]">{session.data?.user.email}</span>. The invitation must be for this address.
              </p>
              <Button className="w-full" loading={busy} onClick={() => accept()}>
                Accept invitation
              </Button>
            </div>
          ) : (
            <>
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">Create your account to accept, or sign in if you already have one.</p>
              <form onSubmit={accept} className="mt-5 space-y-3">
                <Field label="Your name">{(p) => <Input {...p} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />}</Field>
                <Field label="Password" hint="At least 10 characters.">
                  {(p) => <Input {...p} type="password" autoComplete="new-password" required minLength={10} value={password} onChange={(e) => setPassword(e.target.value)} />}
                </Field>
                <Button type="submit" className="w-full" loading={busy}>
                  Create account and join
                </Button>
              </form>
              <p className="mt-4 text-center text-sm text-[var(--color-text-muted)]">
                Already have an account?{" "}
                <Link href={`/login?next=${encodeURIComponent(here)}`} className="text-[var(--color-accent-bright)] hover:underline">
                  Sign in first
                </Link>
              </p>
            </>
          )}
          {error ? (
            <p role="alert" className="mt-3 text-sm text-[var(--color-critical)]">
              {error}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
