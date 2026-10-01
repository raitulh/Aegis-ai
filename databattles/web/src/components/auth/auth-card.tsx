"use client";

import { Sparkles } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { useConfig } from "@/lib/hooks";

export function AuthCard({ title, subtitle, children, footer }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="glow-top pointer-events-none absolute inset-0" aria-hidden />
      <div className="relative w-full max-w-md">
        <Link href="/" className="mx-auto mb-8 flex w-fit items-center gap-2 font-semibold">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-cyan text-accent-fg"><Sparkles className="h-4 w-4" /></span>
          DataBattles
        </Link>
        <div className="rounded-[var(--radius-xl)] border border-border bg-surface p-6 shadow-card sm:p-8">
          <h1 className="text-xl font-semibold">{title}</h1>
          {subtitle ? <p className="mt-1.5 text-sm text-muted">{subtitle}</p> : null}
          <div className="mt-6">{children}</div>
        </div>
        {footer ? <div className="mt-6 text-center text-sm text-muted">{footer}</div> : null}
      </div>
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = { google: "Continue with Google", github: "Continue with GitHub" };

export function OAuthButtons({ next }: { next?: string | null }) {
  const providers = useConfig().data?.auth_providers ?? [];
  if (!providers.length) return null;
  return (
    <div className="space-y-2">
      {providers.map((p) => (
        <a key={p} href={`/api/v1/auth/oauth/${p}/start${next ? `?next=${encodeURIComponent(next)}` : ""}`}
          className="flex h-10 w-full items-center justify-center rounded-[var(--radius-md)] border border-border bg-surface-2 text-sm font-medium hover:bg-surface-3">
          {PROVIDER_LABEL[p] ?? `Continue with ${p}`}
        </a>
      ))}
      <div className="my-4 flex items-center gap-3 text-xs text-subtle"><span className="h-px flex-1 bg-border" />or<span className="h-px flex-1 bg-border" /></div>
    </div>
  );
}

export function DevMailboxHint() {
  const env = useConfig().data?.env;
  if (!env || env === "production") return null;
  return (
    <p className="mt-4 rounded-md border border-dashed border-border px-3 py-2 text-xs text-subtle">
      Local development: emails are not sent. Open the <Link href="/dev/mailbox" className="text-accent-strong underline">dev mailbox</Link> to find verification links.
    </p>
  );
}
