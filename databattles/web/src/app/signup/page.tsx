"use client";

import { MailCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuthCard, DevMailboxHint, OAuthButtons } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, FormError, Input } from "@/components/ui/form";
import { ApiError, post } from "@/lib/api";

function passwordHints(pw: string, email: string): string[] {
  const hints: string[] = [];
  if (pw.length < 10) hints.push("at least 10 characters");
  if (email && pw.toLowerCase().includes(email.split("@")[0].toLowerCase())) hints.push("not based on your email");
  return hints;
}

function SignupForm() {
  const next = useSearchParams().get("next");
  useEffect(() => {
    // The verification link opens in a new tab/session; remember where to go afterwards (same-site paths only).
    try {
      if (next && next.startsWith("/") && !next.startsWith("//")) localStorage.setItem("db-post-auth-next", next);
    } catch {
      /* ignore */
    }
  }, [next]);
  const [form, setForm] = useState({ display_name: "", email: "", handle: "", password: "" });
  const [accept, setAccept] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const hints = passwordHints(form.password, form.email);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await post("/auth/signup", { ...form, handle: form.handle || null, accept_terms: accept });
      setDone(true);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthCard title="Check your inbox" footer={<Link href="/login" className="text-accent-strong hover:underline">Back to sign in</Link>}>
        <div className="flex flex-col items-center text-center">
          <MailCheck className="h-10 w-10 text-success" />
          <p className="mt-4 text-sm text-muted">
            If <span className="font-medium text-fg">{form.email}</span> can be used, we sent a link to verify it. The link expires in 24 hours.
          </p>
          <Button variant="secondary" className="mt-6" onClick={() => post("/auth/resend-verification", { email: form.email })}>Resend email</Button>
        </div>
        <DevMailboxHint />
      </AuthCard>
    );
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <AuthCard title="Create your account" subtitle="Free for students, clubs and universities." footer={<>Already have an account? <Link href="/login" className="font-medium text-accent-strong hover:underline">Sign in</Link></>}>
      <OAuthButtons next={next} />
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Full name" required error={error?.fields.display_name}>
          {(p) => <Input {...p} autoComplete="name" value={form.display_name} onChange={set("display_name")} maxLength={80} />}
        </Field>
        <Field label="Email" required error={error?.fields.email} hint="Use your university email to verify membership later.">
          {(p) => <Input {...p} type="email" autoComplete="email" value={form.email} onChange={set("email")} />}
        </Field>
        <Field label="Username" hint="Optional — letters, numbers, - and _. Shown in your profile URL." error={error?.fields.handle}>
          {(p) => <Input {...p} autoComplete="username" value={form.handle} onChange={set("handle")} maxLength={30} placeholder="ada-lovelace" />}
        </Field>
        <Field label="Password" required error={error?.fields.password} hint={hints.length ? `Use ${hints.join(", ")}.` : "Looks good."}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        <Checkbox checked={accept} onChange={(e) => setAccept(e.target.checked)}
          label={<>I agree to the <Link href="/terms" className="text-accent-strong underline">Terms</Link> and <Link href="/guidelines" className="text-accent-strong underline">Community guidelines</Link></>} />
        {error && !Object.keys(error.fields).length ? <FormError message={error.message} /> : null}
        <Button type="submit" className="w-full" loading={busy} disabled={!accept}>Create account</Button>
      </form>
    </AuthCard>
  );
}

export default function SignupPage() {
  return <Suspense><SignupForm /></Suspense>;
}
