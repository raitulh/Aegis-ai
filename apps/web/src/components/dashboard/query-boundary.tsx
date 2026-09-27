"use client";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ErrorState, Skeleton } from "@/components/ui/primitives";

export function QueryBoundary<T>({ query, skeleton, children }: { query: UseQueryResult<T>; skeleton?: ReactNode; children: (data: T) => ReactNode }) {
  if (query.isLoading) return <>{skeleton ?? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>}</>;
  if (query.isError || query.data === undefined) return <ErrorState message={(query.error as Error)?.message} onRetry={() => query.refetch()} />;
  return <>{children(query.data)}</>;
}
