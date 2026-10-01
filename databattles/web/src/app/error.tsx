"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/page";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <Container size="md" className="py-24 text-center">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="mt-2 text-muted">An unexpected error occurred while showing this page.</p>
      {error.digest ? <p className="mt-2 font-mono text-xs text-subtle">Reference: {error.digest}</p> : null}
      <Button className="mt-6" onClick={reset}>Try again</Button>
    </Container>
  );
}
