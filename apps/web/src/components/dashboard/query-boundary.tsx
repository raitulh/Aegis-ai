"use client";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { PlanLimitNotice } from "@/components/ui/display";
import { ErrorState, Skeleton } from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";

/**
 * Loading → skeleton, error → explained error (permission and plan errors are explained, not shown as
 * "something went wrong"), success → children. A disabled query without data stays in the skeleton state.
 */
export function QueryBoundary<T>({ query, skeleton, children }: { query: UseQueryResult<T>; skeleton?: ReactNode; children: (data: T) => ReactNode }) {
  if (query.isError) {
    const e = query.error;
    if (e instanceof ApiError && e.isPlanLimit) return <PlanLimitNotice error={e} />;
    if (e instanceof ApiError && e.status === 403) return <ErrorState message="Your role does not have access to this information." />;
    if (e instanceof ApiError && e.status === 404) return <ErrorState message="Not found — it may have been deleted, or it belongs to another workspace." />;
    return <ErrorState message={(e as Error)?.message} onRetry={() => query.refetch()} />;
  }
  if (query.isPending || query.data === undefined) {
    return <>{skeleton ?? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>}</>;
  }
  return <>{children(query.data)}</>;
}
