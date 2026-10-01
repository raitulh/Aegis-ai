"use client";

import { Mail } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/brand/logo";
import { cn } from "@/lib/cn";
import { useConfig } from "@/lib/hooks";
import { BrandPanel, LEGAL_LINKS, VALUE_PROPS } from "./brand-panel";

/**
 * Shared shell for the focused auth flows (login, signup, password reset, email verification, onboarding).
 * lg+: a sticky brand panel on the left and the form card centred on the right. Below lg the form comes first
 * under a compact brand header. The app shell already paints the intense ambient background for these routes.
 */
export function AuthCard({
  title,
  subtitle,
  children,
  footer,
  eyebrow,
  size = "md",
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  eyebrow?: ReactNode;
  size?: "md" | "lg";
}) {
  return (
    <div className="grid min-h-dvh grid-cols-1 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <BrandPanel />
      <div className="relative flex min-h-dvh min-w-0 flex-col">
        <header className="flex h-16 shrink-0 items-center px-4 sm:px-8 lg:hidden">
          <Link
            href="/"
            className="inline-flex rounded-md focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--ring)]"
            aria-label="DataBattles home"
          >
            <Logo />
          </Link>
        </header>
        <div className="flex flex-1 items-start justify-center px-4 pb-10 pt-2 sm:items-center sm:px-8 sm:py-12">
          <div className={cn("w-full min-w-0 animate-rise-lg", size === "lg" ? "max-w-[34rem]" : "max-w-[26.5rem]")}>
            <div className="border-gradient relative rounded-[var(--radius-2xl)] surface-glass surface-sheen shadow-elevated">
              <div className="relative px-5 py-6 min-[380px]:p-6 sm:p-9">
                {eyebrow ? <p className="mb-3 text-eyebrow text-accent-strong">{eyebrow}</p> : null}
                <h1 className="text-[1.65rem] font-semibold leading-[1.15] tracking-[-0.03em] text-fg">{title}</h1>
                {subtitle ? <p className="mt-2 text-sm leading-relaxed text-muted">{subtitle}</p> : null}
                <div className="mt-7">{children}</div>
              </div>
            </div>
            {footer ? <div className="mt-6 text-center text-sm text-muted">{footer}</div> : null}
            <ul aria-label="Why DataBattles" className="mt-10 flex flex-wrap justify-center gap-x-5 gap-y-2 text-xs text-subtle lg:hidden">
              {VALUE_PROPS.map((v) => (
                <li key={v.title} className="inline-flex items-center gap-1.5">
                  <v.icon className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> {v.title}
                </li>
              ))}
            </ul>
          </div>
        </div>
        <nav aria-label="Legal" className="flex justify-center gap-5 px-4 pb-6 text-xs text-subtle lg:hidden">
          {LEGAL_LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="inline-flex min-h-9 items-center transition-colors hover:text-fg">
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = { google: "Continue with Google", github: "Continue with GitHub" };

/** Monochrome provider marks (currentColor) so they follow the theme. */
function ProviderMark({ provider }: { provider: string }) {
  if (provider === "github") {
    return (
      <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden>
        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
      </svg>
    );
  }
  if (provider === "google") {
    return (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
        <path d="M12.48 10.92v3.28h7.84c-.24 1.84-.85 3.19-1.79 4.13-1.15 1.15-2.93 2.4-6.05 2.4-4.83 0-8.6-3.89-8.6-8.72s3.77-8.72 8.6-8.72c2.6 0 4.51 1.03 5.91 2.35l2.31-2.31C18.75 1.44 16.13 0 12.48 0 5.87 0 .31 5.39.31 12s5.56 12 12.17 12c3.57 0 6.27-1.17 8.37-3.36 2.16-2.16 2.84-5.21 2.84-7.67 0-.76-.05-1.47-.17-2.05H12.48Z" />
      </svg>
    );
  }
  return null;
}

export function OAuthButtons({ next }: { next?: string | null }) {
  const providers = useConfig().data?.auth_providers ?? [];
  if (!providers.length) return null;
  return (
    <div className="space-y-2.5">
      {providers.map((p) => (
        <a
          key={p}
          href={`/api/v1/auth/oauth/${p}/start${next ? `?next=${encodeURIComponent(next)}` : ""}`}
          className={cn(
            "flex h-11 w-full items-center justify-center gap-2.5 rounded-[var(--radius-md)] border border-border bg-surface-2 text-sm font-medium text-fg",
            "shadow-[inset_0_1px_0_var(--hairline-highlight)] transition-[background-color,border-color,transform] duration-200 ease-out-expo",
            "hover:border-border-strong hover:bg-surface-3 active:scale-[0.99]",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
          )}
        >
          <ProviderMark provider={p} />
          {PROVIDER_LABEL[p] ?? `Continue with ${p}`}
        </a>
      ))}
      <div className="flex items-center gap-3 py-3 text-subtle">
        <span className="h-px flex-1 bg-gradient-to-r from-transparent to-border-strong" />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.16em]">or</span>
        <span className="h-px flex-1 bg-gradient-to-l from-transparent to-border-strong" />
      </div>
    </div>
  );
}

export function DevMailboxHint() {
  const env = useConfig().data?.env;
  if (!env || env === "production") return null;
  return (
    <p className="mt-5 flex gap-2.5 rounded-[var(--radius-md)] border border-dashed border-border-strong bg-bg-elevated/50 px-3 py-2.5 text-xs leading-relaxed text-subtle">
      <Mail className="mt-px h-3.5 w-3.5 shrink-0 text-cyan" aria-hidden />
      <span>
        Local development: emails are not sent. Open the{" "}
        <Link href="/dev/mailbox" className="font-medium text-accent-strong underline underline-offset-2">
          dev mailbox
        </Link>{" "}
        to find verification links.
      </span>
    </p>
  );
}
