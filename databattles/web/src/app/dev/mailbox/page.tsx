"use client";

import { useQuery } from "@tanstack/react-query";

import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, QueryState } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

interface Mail { id: string; to_email: string; subject: string; template: string; text_body: string | null; status: string; created_at: string }

function linkify(text: string) {
  return text.split(/(https?:\/\/\S+)/g).map((part, i) =>
    part.startsWith("http") ? <a key={i} href={part.replace(/^https?:\/\/[^/]+/, "")} className="text-accent-strong underline">{part}</a> : <span key={i}>{part}</span>,
  );
}

/** Development-only view of the email outbox (the API returns 404 in production). */
export default function DevMailboxPage() {
  const q = useQuery({ queryKey: ["dev-mailbox"], queryFn: () => get<Mail[]>("/auth/dev/mailbox"), refetchInterval: 5000 });
  return (
    <Container size="lg">
      <PageHeader title="Dev mailbox" description="Emails generated locally are shown here instead of being delivered. Disabled in production." />
      <QueryState query={q} isEmpty={(d) => d.length === 0} empty={<EmptyState title="No emails yet" description="Sign up or request a password reset to see messages here." />}>
        {(mails) => (
          <ul className="space-y-3">
            {mails.map((m) => (
              <li key={m.id} className="rounded-[var(--radius-lg)] border border-border bg-surface p-4">
                <div className="flex flex-wrap justify-between gap-2 text-xs text-subtle">
                  <span>To {m.to_email} · {m.template}</span>
                  <span>{formatDateTime(m.created_at)} · {m.status}</span>
                </div>
                <p className="mt-1 font-medium">{m.subject}</p>
                {m.text_body ? <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-muted">{linkify(m.text_body)}</pre> : null}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Container>
  );
}
