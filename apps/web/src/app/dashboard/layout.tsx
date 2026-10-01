import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { DashboardShell } from "@/components/dashboard/shell";
import { serverFetch } from "@/lib/server-api";
import type { Session } from "@/lib/types";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const result = await serverFetch<Session>("/auth/session");
  if (!result.ok && (result.reason === "unauthenticated" || result.reason === "forbidden")) redirect("/login");
  if (!result.ok) {
    // An API outage is not a logout: say what is happening and offer a retry.
    return (
      <main className="grid min-h-screen place-items-center px-6">
        <div className="max-w-md space-y-3 text-center">
          <p className="font-mono text-xs uppercase tracking-wider text-[var(--color-medium)]">Service unavailable</p>
          <h1 className="text-xl font-semibold">Aegis can't reach its API right now</h1>
          <p className="text-sm text-[var(--color-text-muted)]">Your session is intact. This is usually temporary — retry in a moment.</p>
          <Link href="/dashboard" className="inline-flex rounded-[var(--radius)] bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-white">
            Retry
          </Link>
        </div>
      </main>
    );
  }
  // Seed the session (already fetched for the auth guard) into the query cache so permission-gated UI
  // renders identically on the server and on the client — no hydration mismatch, no button flicker.
  // The key mirrors `keys.session` in lib/queries.ts.
  const queryClient = new QueryClient();
  queryClient.setQueryData(["session"], result.data);
  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <DashboardShell>{children}</DashboardShell>
    </HydrationBoundary>
  );
}
