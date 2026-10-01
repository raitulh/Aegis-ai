"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Code2, Inbox, Mail as MailIcon, RefreshCw } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { DotBadge } from "@/components/admin/admin-ui";
import { CopyButton } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, QueryState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";

interface Mail { id: string; to_email: string; subject: string; template: string; text_body: string | null; status: string; created_at: string }

function linkify(text: string) {
  return text.split(/(https?:\/\/\S+)/g).map((part, i) =>
    part.startsWith("http") ? <a key={i} href={part.replace(/^https?:\/\/[^/]+/, "")} className="text-accent-strong underline">{part}</a> : <span key={i}>{part}</span>,
  );
}

/** Links found in a message body, made relative the same way `linkify` does (so they open in this app). */
function bodyLinks(text: string | null) {
  if (!text) return [];
  return [...new Set(text.match(/https?:\/\/\S+/g) ?? [])].map((url) => ({ url, href: url.replace(/^https?:\/\/[^/]+/, "") }));
}

const STATUS_TONE: Record<string, "neutral" | "success" | "warning" | "danger" | "info"> = {
  sent: "success",
  queued: "info",
  failed: "danger",
  suppressed: "neutral",
};

function MessageView({ m }: { m: Mail }) {
  const links = bodyLinks(m.text_body);
  return (
    <div className="min-w-0">
      <div className="flex flex-col gap-3 border-b border-border pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-fg [overflow-wrap:anywhere]">{m.subject}</h2>
          <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-xs">
            <dt className="text-subtle">To</dt>
            <dd className="truncate font-mono text-fg">{m.to_email}</dd>
            <dt className="text-subtle">Template</dt>
            <dd className="truncate font-mono text-muted">{m.template}</dd>
            <dt className="text-subtle">Created</dt>
            <dd className="tabular text-muted">{formatDateTime(m.created_at)}</dd>
          </dl>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <DotBadge tone={STATUS_TONE[m.status] ?? "neutral"}>{titleCase(m.status)}</DotBadge>
          {m.text_body ? <CopyButton value={m.text_body} label="Copy text" /> : null}
        </div>
      </div>
      {m.text_body ? (
        <pre className="mt-4 whitespace-pre-wrap font-sans text-sm leading-relaxed text-muted [overflow-wrap:anywhere]">{linkify(m.text_body)}</pre>
      ) : (
        <p className="mt-4 text-sm text-subtle">This message has no plain-text body.</p>
      )}
      {links.length ? (
        <div className="mt-6 rounded-[var(--radius-md)] border border-border bg-bg-elevated p-3">
          <p className="mb-2 text-eyebrow text-subtle">Links in this message</p>
          <ul className="space-y-1">
            {links.map((l) => (
              <li key={l.url} className="min-w-0">
                <a
                  href={l.href}
                  className="group flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 font-mono text-[11.5px] text-accent-strong transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)]"
                >
                  <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-subtle group-hover:text-accent-strong" aria-hidden />
                  <span className="truncate">{l.href}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function MailboxSkeleton() {
  return (
    <div className="grid grid-cols-1 overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]" role="status" aria-label="Loading messages">
      <div className="divide-y divide-border lg:border-r lg:border-border">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="space-y-2 px-4 py-3.5" style={{ opacity: 1 - i * 0.1 }}>
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        ))}
      </div>
      <div className="hidden space-y-3 p-6 lg:block">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-3 w-1/3" />
        <Skeleton className="mt-6 h-40 w-full" />
      </div>
    </div>
  );
}

/** Matches Tailwind's `lg` breakpoint, so the reader is rendered in exactly one place (inline below `lg`, side pane from `lg`). */
const LG_QUERY = "(min-width: 64rem)";
function subscribeLg(onChange: () => void) {
  const mq = window.matchMedia(LG_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
function useIsLg() {
  return useSyncExternalStore(subscribeLg, () => window.matchMedia(LG_QUERY).matches, () => false);
}

/** Development-only view of the email outbox (the API returns 404 in production). */
export default function DevMailboxPage() {
  const q = useQuery({ queryKey: ["dev-mailbox"], queryFn: () => get<Mail[]>("/auth/dev/mailbox"), refetchInterval: 5000 });
  const [selected, setSelected] = useState<string | null>(null);
  const isLg = useIsLg();
  return (
    <Container size="xl" className="pb-20">
      <PageHeader
        eyebrow="Developer tools"
        icon={<Code2 />}
        title="Dev mailbox"
        description="Emails generated locally are shown here instead of being delivered. Disabled in production."
        meta={
          <>
            {q.data ? (
              <span className="inline-flex items-center gap-1.5">
                <MailIcon className="h-3.5 w-3.5" aria-hidden />
                <span className="tabular">{formatNumber(q.data.length)}</span> {q.data.length === 1 ? "message" : "messages"}
              </span>
            ) : null}
            <span className="inline-flex items-center gap-1.5">
              <RefreshCw className={cn("h-3.5 w-3.5", q.isFetching && "animate-spin")} aria-hidden />
              Checks for new mail every 5 seconds
            </span>
          </>
        }
      />
      <QueryState
        query={q}
        loading={<MailboxSkeleton />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={<Inbox />} title="No emails yet" description="Sign up or request a password reset to see messages here." />}
      >
        {(mails) => {
          const current = mails.find((m) => m.id === selected) ?? mails[0];
          return (
            <div className="grid grid-cols-1 overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
              <div className="min-w-0 lg:border-r lg:border-border">
                <div className="flex items-center justify-between border-b border-border bg-bg-elevated/70 px-4 py-2.5">
                  <p className="text-eyebrow text-subtle">Inbox</p>
                  <p className="tabular font-mono text-[11px] text-subtle">{formatNumber(mails.length)}</p>
                </div>
                <ul className="divide-y divide-border lg:max-h-[calc(100dvh-16rem)] lg:overflow-y-auto">
                  {mails.map((m) => {
                    const on = m.id === current.id;
                    return (
                      <li key={m.id} className="min-w-0">
                        <button
                          type="button"
                          aria-pressed={on}
                          onClick={() => setSelected(m.id)}
                          className={cn(
                            "relative block w-full min-w-0 px-4 py-3 text-left transition-colors",
                            "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                            on ? "bg-surface-2" : "hover:bg-surface-2/50",
                          )}
                        >
                          <span aria-hidden className={cn("absolute inset-y-2 left-0 w-[2px] rounded-full bg-brand transition-opacity", on ? "opacity-100" : "opacity-0")} />
                          <span className="flex items-center justify-between gap-3 text-[11.5px]">
                            <span className="min-w-0 truncate font-mono text-muted">{m.to_email}</span>
                            <span className="tabular shrink-0 text-subtle" title={formatDateTime(m.created_at)}>{relativeTime(m.created_at)}</span>
                          </span>
                          <span className={cn("mt-1 block truncate text-sm", on ? "font-medium text-fg" : "text-fg/90")}>{m.subject}</span>
                          <span className="mt-1.5 flex items-center gap-2 text-[11px] text-subtle">
                            <span className="truncate font-mono">{m.template}</span>
                            <span aria-hidden>·</span>
                            <span className="shrink-0">{titleCase(m.status)}</span>
                          </span>
                        </button>
                        {on && !isLg ? (
                          <div className="border-t border-border bg-bg-elevated/50 px-4 py-5 lg:hidden">
                            <MessageView m={m} />
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
              {isLg ? (
                <article className="hidden min-w-0 p-6 lg:block xl:p-8" aria-label="Selected message">
                  <MessageView m={current} />
                </article>
              ) : null}
            </div>
          );
        }}
      </QueryState>
    </Container>
  );
}
