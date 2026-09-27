"use client";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [guest, setGuest] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    const form = new FormData(e.currentTarget);
    try {
      const path = mode === "signup" ? "/auth/signup" : "/auth/login";
      const body =
        mode === "signup"
          ? { email: form.get("email"), password: form.get("password"), full_name: form.get("name"), organization_name: form.get("org") }
          : { email: form.get("email"), password: form.get("password") };
      await api.post(path, body);
      router.push("/dashboard");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function tryDemo() {
    setGuest(true);
    try {
      await api.post("/auth/guest");
      router.push("/dashboard");
    } catch {
      toast.error("Could not start the sandbox");
      setGuest(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 aegis-noise">
      <div className="absolute inset-0 aegis-grid opacity-30" />
      <div className="absolute inset-0 aegis-radial" />
      <div className="relative w-full max-w-sm">
        <Link href="/" className="mb-8 flex justify-center focus-ring rounded-md">
          <Logo />
        </Link>
        <div className="rounded-[var(--radius-xl)] border border-[var(--color-border-strong)] bg-[var(--color-bg-elevated)] p-7 shadow-[var(--shadow-lg)]">
          <h1 className="text-xl font-semibold tracking-tight">{mode === "signup" ? "Create your workspace" : "Welcome back"}</h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">{mode === "signup" ? "Start auditing your AI systems in minutes." : "Sign in to your Aegis workspace."}</p>
          <form onSubmit={submit} className="mt-6 space-y-3">
            {mode === "signup" && (
              <>
                <Field name="name" label="Name" type="text" placeholder="Alex Rivera" />
                <Field name="org" label="Workspace name" type="text" placeholder="Acme AI" />
              </>
            )}
            <Field name="email" label="Email" type="email" placeholder="you@company.com" required />
            <Field name="password" label="Password" type="password" placeholder="••••••••••" required minLength={mode === "signup" ? 10 : undefined} />
            <Button type="submit" className="w-full" loading={loading}>
              {mode === "signup" ? "Create workspace" : "Sign in"}
            </Button>
          </form>
          <div className="my-5 flex items-center gap-3 text-xs text-[var(--color-text-subtle)]">
            <div className="h-px flex-1 bg-[var(--color-border)]" /> or <div className="h-px flex-1 bg-[var(--color-border)]" />
          </div>
          <Button variant="secondary" className="w-full" onClick={tryDemo} loading={guest}>
            Explore — no signup
          </Button>
          <p className="mt-5 text-center text-sm text-[var(--color-text-muted)]">
            {mode === "signup" ? (
              <>
                Already have an account?{" "}
                <Link href="/login" className="text-[var(--color-accent-bright)] hover:underline">
                  Sign in
                </Link>
              </>
            ) : (
              <>
                New to Aegis?{" "}
                <Link href="/signup" className="text-[var(--color-accent-bright)] hover:underline">
                  Create a workspace
                </Link>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}

function Field({ name, label, ...props }: { name: string; label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">{label}</span>
      <input
        name={name}
        {...props}
        className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none transition-colors placeholder:text-[var(--color-text-subtle)] focus:border-[var(--color-accent)] focus-ring"
      />
    </label>
  );
}
