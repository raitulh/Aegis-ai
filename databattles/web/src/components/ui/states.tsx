"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, Inbox, Loader2, Lock, SearchX, WifiOff } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button, LinkButton } from "./button";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4", className)} aria-hidden />;
}

export function SkeletonCards({ count = 6, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid gap-4 sm:grid-cols-2 lg:grid-cols-3", className)} role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-[var(--radius-lg)] border border-border bg-surface p-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="mt-4 h-4 w-3/4" />
          <Skeleton className="mt-2 h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

function StateShell({ icon, title, description, action, className, tone = "neutral" }: {
  icon: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  tone?: "neutral" | "danger";
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-[var(--radius-lg)] border border-dashed border-border px-6 py-12 text-center", className)}>
      <div className={cn("mb-4 flex h-11 w-11 items-center justify-center rounded-full", tone === "danger" ? "bg-danger-soft text-danger" : "bg-surface-2 text-muted")}>
        {icon}
      </div>
      <h3 className="text-base font-semibold text-fg">{title}</h3>
      {description ? <p className="mt-1.5 max-w-md text-sm text-muted">{description}</p> : null}
      {action ? <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function EmptyState({ title, description, action, icon, className }: { title: ReactNode; description?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return <StateShell icon={icon ?? <Inbox className="h-5 w-5" />} title={title} description={description} action={action} className={className} />;
}

export function NoResults({ onReset }: { onReset?: () => void }) {
  return (
    <StateShell
      icon={<SearchX className="h-5 w-5" />}
      title="No matches"
      description="Try different keywords or clear some filters."
      action={onReset ? <Button variant="secondary" onClick={onReset}>Clear filters</Button> : undefined}
    />
  );
}

export function PermissionDenied({ message, signIn }: { message?: string; signIn?: boolean }) {
  return (
    <StateShell
      icon={<Lock className="h-5 w-5" />}
      title={signIn ? "Sign in required" : "You don't have access"}
      description={message ?? (signIn ? "Sign in to continue." : "Ask an organizer or administrator if you think you should have access.")}
      action={signIn ? <LinkButton href="/login">Sign in</LinkButton> : <LinkButton href="/" variant="secondary">Go home</LinkButton>}
    />
  );
}

export function NotFoundState({ what = "page" }: { what?: string }) {
  return (
    <StateShell
      icon={<SearchX className="h-5 w-5" />}
      title={`This ${what} doesn't exist`}
      description="It may have been moved, made private, or never existed."
      action={<LinkButton href="/" variant="secondary">Go home</LinkButton>}
    />
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (error instanceof ApiError) {
    if (error.status === 401) return <PermissionDenied signIn />;
    if (error.status === 403) return <PermissionDenied message={error.message} />;
    if (error.status === 404) return <NotFoundState what="item" />;
  }
  const offline = error instanceof ApiError && error.code === "network_error";
  return (
    <StateShell
      tone="danger"
      icon={offline ? <WifiOff className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
      title={offline ? "Connection problem" : "Something went wrong"}
      description={
        <>
          {error instanceof Error ? error.message : "Unexpected error."}
          {error instanceof ApiError && error.requestId ? (
            <span className="mt-2 block font-mono text-xs text-subtle">Reference: {error.requestId}</span>
          ) : null}
        </>
      }
      action={onRetry ? <Button variant="secondary" onClick={onRetry}>Try again</Button> : undefined}
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

export function InlineNotice({ tone = "info", title, children, action }: { tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  const tones = {
    info: "border-info/30 bg-info-soft text-info",
    warning: "border-warning/30 bg-warning-soft text-warning",
    danger: "border-danger/30 bg-danger-soft text-danger",
    success: "border-success/30 bg-success-soft text-success",
  };
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("flex flex-col gap-2 rounded-[var(--radius-md)] border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between", tones[tone])}>
      <div>
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className="text-fg/80">{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
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
