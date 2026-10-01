"use client";

import { CheckCircle2, MailX } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { FormError } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { EmptyState, Spinner } from "@/components/ui/states";
import { ApiError, errorMessage, post } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Message } from "@/lib/types";

function UnsubscribeInner() {
  const token = useSearchParams().get("token") ?? "";
  const me = useMe();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!token || token.length < 10) {
    return (
      <EmptyState
        icon={<MailX className="h-5 w-5" />}
        title="This unsubscribe link is incomplete"
        description="Open the link from the email again, or manage your email preferences in settings."
        action={<LinkButton href="/settings/notifications" variant="secondary">Notification settings</LinkButton>}
      />
    );
  }

  if (state === "done") {
    return (
      <Card>
        <CardBody className="py-10 text-center">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-success-soft text-success">
            <CheckCircle2 className="h-6 w-6" aria-hidden />
          </span>
          <h1 className="mt-4 text-xl font-semibold text-fg">You&apos;re unsubscribed</h1>
          <p role="status" className="mx-auto mt-2 max-w-md text-sm text-muted">{message}</p>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted">In-app notifications are unchanged.</p>
          <div className="mt-6 flex justify-center">
            <LinkButton href="/settings/notifications" variant="secondary">Manage notification settings</LinkButton>
          </div>
          {me.data === null ? <p className="mt-3 text-xs text-subtle">You&apos;ll need to sign in to change other preferences.</p> : null}
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardBody className="py-10 text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-surface-2 text-muted">
          <MailX className="h-6 w-6" aria-hidden />
        </span>
        <h1 className="mt-4 text-xl font-semibold text-fg">Unsubscribe from these emails?</h1>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted">
          You&apos;ll stop receiving emails of the type this link was sent for. Account security emails, like password resets, are always sent.
        </p>
        <div className="mx-auto mt-6 max-w-sm space-y-3">
          <FormError message={error} />
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
          <Link href="/settings/notifications" className="block text-sm text-muted hover:text-fg">Choose exactly which emails to get instead</Link>
        </div>
      </CardBody>
    </Card>
  );
}

export default function UnsubscribePage() {
  return (
    <Container size="md" className="py-12 pb-16">
      <Suspense fallback={<Spinner />}>
        <UnsubscribeInner />
      </Suspense>
    </Container>
  );
}
