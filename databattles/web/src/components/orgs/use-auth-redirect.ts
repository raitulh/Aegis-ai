"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { useMe } from "@/lib/hooks";

/**
 * Like `useRequireAuth`, but keeps the query string in `next` so one-time links
 * (e.g. `?token=…`) still work after signing in. Must be rendered inside <Suspense>.
 */
export function useRequireAuthKeepQuery() {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  useEffect(() => {
    if (me.isSuccess && me.data === null) {
      const qs = params.toString();
      const next = qs ? `${pathname}?${qs}` : pathname;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [me.isSuccess, me.data, router, pathname, params]);
  return me;
}
