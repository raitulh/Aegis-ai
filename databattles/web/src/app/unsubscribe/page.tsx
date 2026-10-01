"use client";

import { BellRing, Check, MailX, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";

import { LogoMark } from "@/components/brand/logo";
import { Button, LinkButton } from "@/components/ui/button";
import { FormError } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/states";
import { ApiError, errorMessage, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMe } from "@/lib/hooks";
import type { Message } from "@/lib/types";

/** Branded, centred panel shared by every state of this page. */
function Panel({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="relative mx-auto max-w-lg overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-elevated animate-rise">
      <div aria-hidden className="pointer-events-none absolute inset-x-8 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-70" />
      <div className="flex items-center gap-2.5 border-b border-border px-6 py-3.5">
        <LogoMark size={20} />
        <span className="text-sm font-semibold tracking-[-0.01em] text-fg">DataBattles</span>
        <span aria-hidden className="h-3.5 w-px bg-border-strong" />
        <span className="text-eyebrow text-subtle">Email preferences</span>
      </div>
      <div className="px-6 py-9 text-center sm:px-10 sm:py-10">{children}</div>
      {footer ? <div className="border-t border-border bg-bg-elevated/50 px-6 py-3.5 text-xs text-subtle sm:px-10">{footer}</div> : null}
    </div>
  );
}

function Mark({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warning" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative mx-auto flex h-14 w-14 items-center justify-center rounded-full ring-1 ring-inset [&_svg]:h-6 [&_svg]:w-6",
        tone === "warning" ? "bg-warning-soft text-warning ring-[color-mix(in_oklab,var(--warning)_30%,transparent)]" : "bg-surface-2 text-accent-strong ring-border",
      )}
    >
      {/* Static decorative ring on the neutral (confirm) state only; nothing here is loading, so nothing moves. */}
      {tone === "neutral" ? <span className="absolute -inset-1.5 rounded-full border border-dashed border-border-strong" /> : null}
      {children}
    </span>
  );
}

const SECURITY_NOTE = (
  <span className="flex items-center justify-center gap-1.5 text-center">
    <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-success" aria-hidden />
    Account security emails, like password resets, are always sent.
  </span>
);

function UnsubscribeInner() {
  const token = useSearchParams().get("token") ?? "";
  const me = useMe();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!token || token.length < 10) {
    return (
      <Panel>
        <Mark tone="warning"><MailX /></Mark>
        <h1 className="mt-6 text-xl font-semibold tracking-[-0.02em] text-fg sm:text-2xl">This unsubscribe link is incomplete</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">Open the link from the email again, or manage your email preferences in settings.</p>
        <div className="mt-7 flex justify-center">
          <LinkButton href="/settings/notifications" variant="secondary">Notification settings</LinkButton>
        </div>
      </Panel>
    );
  }

  if (state === "done") {
    return (
      <Panel footer={me.data === null ? <p className="text-center">You&apos;ll need to sign in to change other preferences.</p> : SECURITY_NOTE}>
        <span aria-hidden className="relative mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-inset ring-[color-mix(in_oklab,var(--success)_30%,transparent)] animate-pop">
          <span className="absolute inset-0 rounded-full motion-safe:animate-pulse-ring" />
          <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className="motion-safe:animate-check" />
          </svg>
        </span>
        <h1 className="mt-6 text-xl font-semibold tracking-[-0.02em] text-fg sm:text-2xl">You&apos;re unsubscribed</h1>
        <p role="status" className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">{message}</p>
        <p className="mx-auto mt-2 inline-flex max-w-sm items-center gap-1.5 text-sm text-muted">
          <BellRing className="h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden />
          In-app notifications are unchanged.
        </p>
        <div className="mt-7 flex justify-center">
          <LinkButton href="/settings/notifications" variant="secondary">Manage notification settings</LinkButton>
        </div>
      </Panel>
    );
  }

  return (
    <Panel footer={SECURITY_NOTE}>
      <Mark><MailX /></Mark>
      <h1 className="mt-6 text-xl font-semibold tracking-[-0.02em] text-fg sm:text-2xl">Unsubscribe from these emails?</h1>
      <ul className="mx-auto mt-4 max-w-sm space-y-2 text-left text-sm leading-relaxed text-muted">
        <li className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong" aria-hidden><Check className="h-2.5 w-2.5" /></span>
          You&apos;ll stop receiving emails of the type this link was sent for.
        </li>
        <li className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong" aria-hidden><Check className="h-2.5 w-2.5" /></span>
          In-app notifications aren&apos;t affected, and you can turn these emails back on in notification settings.
        </li>
      </ul>
      <div className="mx-auto mt-7 max-w-sm space-y-3">
        <div className="text-left"><FormError message={error} /></div>
        <Button
          size="lg"
          className="w-full"
          loading={state === "busy"}
          onClick={async () => {
            setState("busy");
            setError(null);
            try {
              const m = await post<Message>("/notifications/unsubscribe", { token });
              setMessage(m.message);
              setState("done");
            } catch (e) {
              setError(e instanceof ApiError && e.code === "token_invalid" ? "This unsubscribe link is invalid or has expired. You can still turn off emails in notification settings." : errorMessage(e));
              setState("idle");
            }
          }}
        >
          Confirm unsubscribe
        </Button>
        <Link
          href="/settings/notifications"
          className="block rounded-md py-1 text-sm text-muted transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
        >
          Choose exactly which emails to get instead
        </Link>
      </div>
    </Panel>
  );
}

function UnsubscribeSkeleton() {
  return (
    <div className="mx-auto max-w-lg overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface" role="status" aria-label="Loading">
      <div className="border-b border-border px-6 py-3.5"><Skeleton className="h-4 w-40" /></div>
      <div className="flex flex-col items-center gap-3 px-6 py-10">
        <Skeleton className="h-14 w-14 rounded-full" />
        <Skeleton className="mt-3 h-6 w-64 max-w-full" />
        <Skeleton className="h-4 w-72 max-w-full" />
        <Skeleton className="mt-5 h-11 w-full max-w-sm" />
      </div>
    </div>
  );
}

export default function UnsubscribePage() {
  return (
    <Container size="md" className="py-12 pb-16 sm:py-20">
      <Suspense fallback={<UnsubscribeSkeleton />}>
        <UnsubscribeInner />
      </Suspense>
    </Container>
  );
}
