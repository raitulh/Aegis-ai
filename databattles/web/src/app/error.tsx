"use client";

import { AlertTriangle, Home, RotateCcw } from "lucide-react";
import { useEffect } from "react";

import { OrbitMotif } from "@/components/auth/orbit-motif";
import { BackButton } from "@/components/auth/recovery";
import { Button, LinkButton } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <section className="relative isolate overflow-hidden" aria-labelledby="error-title">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-0 -z-10 h-[26rem] w-[min(46rem,100%)] -translate-x-1/2"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklab, var(--danger) 10%, transparent), transparent)" }}
      />
      <Container size="md" className="flex min-h-[min(44rem,calc(100dvh-6rem))] flex-col items-center justify-center py-16 text-center sm:py-24">
        <OrbitMotif size="lg" tone="danger" className="animate-fade-in">
          <AlertTriangle />
        </OrbitMotif>
        <p className="mt-9 animate-rise text-eyebrow text-danger">Unexpected error</p>
        <h1 id="error-title" className="mt-3 animate-rise text-title text-fg [animation-delay:60ms]">
          Something went wrong
        </h1>
        <p className="mt-3 max-w-md animate-rise text-[15px] leading-relaxed text-muted [animation-delay:120ms]">
          An unexpected error occurred while showing this page. Trying again usually fixes it.
        </p>
        {error.digest ? (
          <p className="mt-5 inline-flex max-w-full animate-rise items-center gap-2 rounded-[var(--radius-md)] border border-border bg-bg-elevated py-1 pl-3 pr-1 font-mono text-xs text-subtle [animation-delay:160ms]">
            <span className="min-w-0 truncate">Reference: {error.digest}</span>
            <CopyButton value={error.digest} />
          </p>
        ) : null}
        <div className="mt-8 flex animate-rise flex-wrap justify-center gap-2 [animation-delay:200ms]">
          <Button onClick={reset} icon={<RotateCcw className="h-4 w-4" />}>
            Try again
          </Button>
          <BackButton />
          <LinkButton href="/" variant="ghost" icon={<Home className="h-4 w-4" />}>
            Go home
          </LinkButton>
        </div>
      </Container>
    </section>
  );
}
