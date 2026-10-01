"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Inbox, Info, Loader2, Lock, OctagonAlert, RotateCcw, SearchX, TriangleAlert, WifiOff } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button, LinkButton } from "./button";

/* ------------------------------------------------------------------ Skeletons */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4", className)} aria-hidden />;
}

/** Card-grid placeholder shaped like the media cards it stands in for (cover, meta line, title, footer). */
export function SkeletonCards({ count = 6, className, media = true }: { count?: number; className?: string; media?: boolean }) {
  return (
    <div className={cn("grid gap-4 sm:grid-cols-2 lg:grid-cols-3", className)} role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" style={{ opacity: 1 - i * 0.06 }}>
          {media ? <Skeleton className="h-28 w-full rounded-none" /> : null}
          <div className="p-4">
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="mt-3 h-4 w-4/5" />
            <Skeleton className="mt-2 h-3 w-3/5" />
            <div className="mt-5 flex items-center justify-between">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-20" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-surface px-3 py-2.5" style={{ opacity: 1 - i * 0.07 }}>
          <Skeleton className="h-7 w-7 shrink-0 rounded-full" />
          <Skeleton className="h-3.5 flex-1" />
          <Skeleton className="hidden h-3.5 w-24 sm:block" />
        </div>
      ))}
    </div>
  );
}

/** Row of metric tiles. */
export function SkeletonStats({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid grid-cols-2 gap-3 lg:grid-cols-4", className)} role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-[var(--radius-lg)] border border-border bg-surface p-4">
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="mt-4 h-7 w-16" />
        </div>
      ))}
    </div>
  );
}

/** Detail-page hero: eyebrow, title, summary and a facts row. */
export function SkeletonHero({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-[var(--radius-xl)] border border-border bg-surface p-6 sm:p-8", className)} role="status" aria-label="Loading">
      <Skeleton className="h-5 w-40 rounded-full" />
      <Skeleton className="mt-5 h-9 w-2/3" />
      <Skeleton className="mt-3 h-4 w-1/2" />
      <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i}>
            <Skeleton className="h-2.5 w-16" />
            <Skeleton className="mt-2 h-5 w-24" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2.5 py-12 text-sm text-muted">
      <span className="relative flex h-4 w-4 items-center justify-center" aria-hidden>
        <span className="absolute inset-0 rounded-full border border-border-strong" />
        <Loader2 className="h-4 w-4 animate-spin text-accent-strong" />
      </span>
      {label}…
    </div>
  );
}

/* ------------------------------------------------------------------ State shells */

type ShellTone = "neutral" | "danger" | "warning";

/** Icon set inside a small orbit motif — the DataBattles signature, kept quiet. */
function StateIcon({ children, tone }: { children: ReactNode; tone: ShellTone }) {
  const color = tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-accent-strong";
  return (
    <div className={cn("relative mb-5 flex h-14 w-14 items-center justify-center", color)} aria-hidden>
      <span className="absolute inset-0 rounded-full border border-dashed border-border-strong motion-safe:animate-spin-slow" />
      <span className="absolute -right-0.5 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-current opacity-70" />
      <span
        className={cn(
          "relative flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface-2 shadow-[inset_0_1px_0_var(--hairline-highlight)] [&_svg]:h-[18px] [&_svg]:w-[18px]",
        )}
      >
        {children}
      </span>
    </div>
  );
}

function StateShell({
  icon,
  title,
  description,
  action,
  className,
  tone = "neutral",
}: {
  icon: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  tone?: ShellTone;
}) {
  return (
    <div
      className={cn(
        "relative flex flex-col items-center justify-center overflow-hidden rounded-[var(--radius-lg)] border border-dashed border-border-strong bg-surface/40 px-6 py-14 text-center",
        className,
      )}
    >
      <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]" />
      <div className="relative flex flex-col items-center">
        <StateIcon tone={tone}>{icon}</StateIcon>
        <h3 className="text-base font-semibold tracking-[-0.01em] text-fg">{title}</h3>
        {description ? <div className="mt-1.5 max-w-md text-sm leading-relaxed text-muted">{description}</div> : null}
        {action ? <div className="mt-6 flex flex-wrap justify-center gap-2">{action}</div> : null}
      </div>
    </div>
  );
}

export function EmptyState({ title, description, action, icon, className }: { title: ReactNode; description?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return <StateShell icon={icon ?? <Inbox />} title={title} description={description} action={action} className={className} />;
}

export function NoResults({ onReset }: { onReset?: () => void }) {
  return (
    <StateShell
      icon={<SearchX />}
      title="No matches"
      description="Try different keywords or clear some filters."
      action={onReset ? <Button variant="secondary" onClick={onReset} icon={<RotateCcw className="h-4 w-4" />}>Clear filters</Button> : undefined}
    />
  );
}

export function PermissionDenied({ message, signIn }: { message?: string; signIn?: boolean }) {
  return (
    <StateShell
      tone="warning"
      icon={<Lock />}
      title={signIn ? "Sign in required" : "You don't have access"}
      description={message ?? (signIn ? "Sign in to continue." : "Ask an organizer or administrator if you think you should have access.")}
      action={signIn ? <LinkButton href="/login">Sign in</LinkButton> : <LinkButton href="/" variant="secondary">Go home</LinkButton>}
    />
  );
}

export function NotFoundState({ what = "page" }: { what?: string }) {
  return (
    <StateShell
      icon={<SearchX />}
      title={`This ${what} doesn't exist`}
      description="It may have been moved, made private, or never existed."
      action={<LinkButton href="/" variant="secondary">Go home</LinkButton>}
    />
  );
}

function BackButton() {
  const router = useRouter();
  return (
    <Button
      variant="ghost"
      icon={<ArrowLeft className="h-4 w-4" />}
      onClick={() => {
        if (typeof window !== "undefined" && window.history.length > 1) router.back();
        else router.push("/");
      }}
    >
      Go back
    </Button>
  );
}

/**
 * Error with a safe explanation, retry and a way out. Only messages from the API (which are written to be
 * user-safe) are shown; anything else falls back to generic copy so implementation details never leak.
 */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (error instanceof ApiError) {
    if (error.status === 401) return <PermissionDenied signIn />;
    if (error.status === 403) return <PermissionDenied message={error.message} />;
    if (error.status === 404) return <NotFoundState what="item" />;
  }
  const offline = error instanceof ApiError && error.code === "network_error";
  const message =
    error instanceof ApiError && error.message
      ? error.message
      : "An unexpected error occurred while loading this. Please try again in a moment.";
  return (
    <StateShell
      tone="danger"
      icon={offline ? <WifiOff /> : <AlertTriangle />}
      title={offline ? "Connection problem" : "Something went wrong"}
      description={
        <>
          {offline ? "We couldn't reach DataBattles. Check your connection and try again." : message}
          {error instanceof ApiError && error.requestId ? (
            <span className="mt-3 block font-mono text-[11px] text-subtle">Reference: {error.requestId}</span>
          ) : null}
        </>
      }
      action={
        <>
          {onRetry ? <Button variant="secondary" onClick={onRetry} icon={<RotateCcw className="h-4 w-4" />}>Try again</Button> : null}
          <BackButton />
        </>
      }
    />
  );
}

/**
 * Renders loading / error / empty / success for a query consistently.
 */
export function QueryState<T>({
  query,
  loading,
  empty,
  isEmpty,
  children,
}: {
  query: UseQueryResult<T, Error>;
  loading?: ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <>{loading ?? <Spinner />}</>;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (isEmpty && isEmpty(query.data)) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return <>{children(query.data)}</>;
}

const NOTICE_ICON = { info: Info, warning: TriangleAlert, danger: OctagonAlert, success: CheckCircle2 } as const;

export function InlineNotice({ tone = "info", title, children, action }: { tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  const tones = {
    info: "border-info/25 bg-info-soft text-info",
    warning: "border-warning/25 bg-warning-soft text-warning",
    danger: "border-danger/25 bg-danger-soft text-danger",
    success: "border-success/25 bg-success-soft text-success",
  };
  const Icon = NOTICE_ICON[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn("flex flex-col gap-3 rounded-[var(--radius-md)] border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between", tones[tone])}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <div className="min-w-0">
          {title ? <p className="font-semibold">{title}</p> : null}
          {children ? <div className="text-fg/80">{children}</div> : null}
        </div>
      </div>
      {action ? <div className="shrink-0 pl-6 sm:pl-0">{action}</div> : null}
    </div>
  );
}

export function SignInPrompt({ text = "Sign in to take part." }: { text?: string }) {
  return (
    <p className="text-sm text-muted">
      <Link href="/login" className="font-medium text-accent-strong hover:underline">Sign in</Link> or{" "}
      <Link href="/signup" className="font-medium text-accent-strong hover:underline">create an account</Link>. {text}
    </p>
  );
}
